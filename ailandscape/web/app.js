"use strict";

// Entity-type colours (kept in step with ailandscape/visualize.py).
const TYPE_COLORS = {
  place: "#4f83cc",
  organization: "#e08a3c",
  person: "#5fa55a",
  product: "#9b6dc7",
  concept: "#cc4f5a",
  group: "#3fb5a8",
  facility: "#c9a23b",
  event: "#8d8d8d",
  misc: "#9aa0a6",
};
const DEFAULT_COLOR = "#9aa0a6";

// Curated entry points for a newcomer — each loads a focused preset view.
const STARTER_VIEWS = {
  relations: { label: "Typed relationships", params: { relations_only: true } },
  orgs: { label: "Organizations", params: { type: "organization" } },
  concepts: { label: "AI capabilities", params: { type: "concept" } },
  people: { label: "Key people", params: { type: "person" } },
};

// Plain-language meaning of each typed relationship, shown in the guide.
const RELATION_GLOSSARY = [
  ["leads", "a person directs an organization"],
  ["part of", "an organization or group is a unit of a larger one"],
  ["located in", "an organization or facility is based in a place"],
  ["acquires", "one organization buys another"],
  ["partners with", "two organizations collaborate"],
  ["awards contract", "an organization awards another a contract"],
  ["develops", "an organization builds a product or capability"],
  ["supplies", "an organization provides goods or services"],
  ["co-occurrence", "entities mentioned together — not a stated relationship"],
];

// Quick lookup keyed by both the "leads" / "awards contract" surface and the
// underscore form ("awards_contract") the API returns. Used for edge hover
// tooltips and the search filter dropdown.
const RELATION_MEANING = (() => {
  const out = {};
  for (const [label, meaning] of RELATION_GLOSSARY) {
    out[label] = meaning;
    out[label.replace(/\s+/g, "_")] = meaning;
  }
  out.co_occurs_with = out["co-occurrence"];
  return out;
})();

let cy = null;
let currentParams = {};
let totalNodes = 0;
// Set of node ids the spike detector has flagged this session. Used to
// stamp a small "↑" badge next to entity rows in every list across the
// app, without weighing down every API response with per-node spike data.
let SPIKE_IDS = new Set();
let SPIKE_BY_ID = {};
// The active briefing window in days — switchable via tabs in the modal.
let BRIEFING_DAYS = 7;
// Semantic zoom: as the user zooms past these thresholds we re-fetch the
// graph with a larger max_nodes so previously-too-small nodes can appear.
// The thresholds and ladder are chosen so the densification feels like
// natural drill-in rather than a re-layout shock.
const ZOOM_DENSITY_LADDER = [90, 160, 260, 400];
let currentDensityIndex = 0;

// Use the fcose layout (better cluster spread) when its extension loaded.
let LAYOUT_NAME = "cose";
try {
  if (window.cytoscapeFcose) {
    cytoscape.use(window.cytoscapeFcose);
    LAYOUT_NAME = "fcose";
  }
} catch (e) {
  LAYOUT_NAME = "cose";
}

// No optional Cytoscape extensions — every modernization (hover halo,
// tooltip, right-click menu) is pure DOM/CSS/JS below so a third-party
// CDN outage can't break the graph.

function layoutOptions(name) {
  if (name === "fcose") {
    return {
      name: "fcose",
      quality: "proof",
      animate: false,
      randomize: true,
      fit: true,
      padding: 55,
      // Tuned up for breathing room: a 90-node default view was packing
      // labels on top of each other. Roughly 2x the repulsion + 50%
      // longer ideal edges spreads the graph without changing structure.
      nodeRepulsion: 32000,
      idealEdgeLength: 140,     // connected nodes (a grouping) stay close
      edgeElasticity: 0.3,
      nodeSeparation: 240,      // push everything else well apart
      gravity: 0.12,
      gravityRange: 5.0,
      numIter: 3000,
      packComponents: true,
      tile: true,
      // tileByZIndex / tilingPaddingVertical+Horizontal keep disconnected
      // mini-clusters from piling on top of each other in the corners.
      tilingPaddingVertical: 30,
      tilingPaddingHorizontal: 30,
    };
  }
  return {
    name: "cose",
    animate: false,
    fit: true,
    padding: 45,
    nodeRepulsion: 55000,
    idealEdgeLength: 150,
    gravity: 0.15,
    componentSpacing: 140,
  };
}

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// Small "↑" badge added to entity rows when the spike detector flagged
// the node this session. Lifted here so every list renderer in the app
// (search, dashboard, briefing, sidebar) can opt in by appending the
// returned string to its row HTML.
function spikeBadge(nodeId) {
  if (!nodeId || !SPIKE_IDS.has(Number(nodeId))) return "";
  const meta = SPIKE_BY_ID[Number(nodeId)];
  const title = meta
    ? "Trending: " + meta.recent + " mentions in last 30 days, " +
      meta.ratio + "× baseline"
    : "Trending";
  return ' <span class="spike-badge" title="' + escapeHtml(title) +
         '">↑</span>';
}

// localStorage with a graceful no-op fallback so private/incognito modes
// don't break the UI. Returns null on any failure rather than throwing.
function lsGet(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}
function lsSet(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* ignore */ }
}
function lsAddSeen(key, value) {
  try {
    const raw = localStorage.getItem(key) || "";
    const seen = new Set(raw ? raw.split(",") : []);
    seen.add(String(value));
    // Cap to keep the value short on long sessions.
    const arr = Array.from(seen);
    if (arr.length > 500) arr.splice(0, arr.length - 500);
    localStorage.setItem(key, arr.join(","));
  } catch (e) { /* ignore */ }
}
function lsHasSeen(key, value) {
  try {
    const raw = localStorage.getItem(key) || "";
    return raw.split(",").includes(String(value));
  } catch (e) { return false; }
}

function setMessage(text) {
  const box = $("message");
  box.textContent = text;
  box.classList.add("show");
  clearTimeout(setMessage._t);
  setMessage._t = setTimeout(() => box.classList.remove("show"), 4500);
}

async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail || detail;
    } catch (e) {
      /* ignore */
    }
    throw new Error(detail);
  }
  return resp.json();
}

// ---- graph rendering -------------------------------------------------------

function toElements(graph) {
  const maxMentions = Math.max(1, ...graph.nodes.map((n) => n.mentions));
  const elements = [];
  for (const n of graph.nodes) {
    // Log-scaled sizing compresses the long tail so a 1,400-mention giant
    // doesn't visually dwarf a 30-mention specific entity by 5×. The
    // base/range stays the same so the largest nodes still stand out.
    const sizeRatio = Math.log(1 + n.mentions) / Math.log(1 + maxMentions);
    elements.push({
      data: {
        id: String(n.id),
        label: n.label,
        type: n.type,
        mentions: n.mentions,
        documents: n.documents,
        color: TYPE_COLORS[n.type] || DEFAULT_COLOR,
        size: 18 + 48 * sizeRatio,
      },
    });
  }
  for (const e of graph.edges) {
    const relation = e.relation || "co_occurs_with";
    elements.push({
      data: {
        id: "e" + e.id,
        source: String(e.source),
        target: String(e.target),
        // Co-occurrence thickness tracks normalized strength, not raw
        // weight, so a thick line means a genuine association — not a hub.
        w: 0.6 + 5 * (e.strength || 0),
        relation: relation,
        relLabel:
          relation === "co_occurs_with" ? "" : relation.replace(/_/g, " "),
        evidence: e.evidence || "",
      },
    });
  }
  return elements;
}

const CY_STYLE = [
  {
    selector: "node",
    style: {
      "background-color": "data(color)",
      // Tiny dark border softens the edge against the dark canvas.
      "border-width": 1.2,
      "border-color": "#0a0f1a",
      "border-opacity": 0.6,
      width: "data(size)",
      height: "data(size)",
      label: "data(label)",
      color: "#eef2f7",
      "font-family": "Inter, system-ui, sans-serif",
      "font-weight": 500,
      // Prominent nodes get larger labels; small ones hide when zoomed out
      // so the default view stays uncluttered.
      "font-size": "mapData(size, 16, 72, 8, 20)",
      "min-zoomed-font-size": 11,
      "text-outline-color": "#0a0f1a",
      "text-outline-width": 3,    // a touch thicker for legibility on dark bg
      "text-outline-opacity": 0.9,
      "text-valign": "bottom",
      "text-margin-y": 4,
      // Soft "lit" feel via per-node shadow.
      "overlay-opacity": 0,
      "shadow-blur": 8,
      "shadow-color": "#000",
      "shadow-opacity": 0.35,
      "shadow-offset-x": 0,
      "shadow-offset-y": 1,
    },
  },
  {
    selector: "edge",
    style: {
      width: "data(w)",
      "line-color": "#3a4761",
      "curve-style": "bezier",
      "line-opacity": 0.4,
      opacity: 0.55,
    },
  },
  {
    // Typed semantic relationships stand out: bright, arrowed, labelled.
    selector: 'edge[relation != "co_occurs_with"]',
    style: {
      width: 2.6,
      "line-color": "#3b82f6",
      opacity: 0.95,
      "line-opacity": 0.85,
      "target-arrow-shape": "triangle",
      "target-arrow-color": "#3b82f6",
      "arrow-scale": 1.15,
      label: "data(relLabel)",
      "font-family": "Inter, system-ui, sans-serif",
      "font-weight": 500,
      "font-size": 9,
      "min-zoomed-font-size": 9,
      color: "#a8c4ff",
      "text-rotation": "autorotate",
      "text-background-color": "#0a0f1a",
      "text-background-opacity": 0.85,
      "text-background-padding": 3,
      "text-background-shape": "roundrectangle",
    },
  },
  // Selected node: clear accent ring.
  { selector: "node:selected", style: { "border-width": 3, "border-color": "#3b82f6", "border-opacity": 1 } },
  // .dim is the existing class used by selectNode -> highlightNeighborhood.
  { selector: ".dim", style: { opacity: 0.1, "text-opacity": 0.1 } },
  // ---- hover halo: triggered transiently on mouseover ----
  // The hovered node lifts: stronger shadow, brighter border, full opacity.
  {
    selector: "node.hovered",
    style: {
      "border-width": 3,
      "border-color": "#3b82f6",
      "border-opacity": 1,
      "shadow-blur": 22,
      "shadow-opacity": 0.65,
      "shadow-color": "#3b82f6",
      "z-index": 999,
    },
  },
  // The hovered node's neighbors brighten too — same affordance as the
  // selection highlight but transient.
  {
    selector: "node.neighbor-hovered",
    style: {
      "border-width": 2,
      "border-color": "#fff",
      "border-opacity": 0.7,
      "shadow-blur": 14,
      "shadow-opacity": 0.45,
      "z-index": 100,
    },
  },
  // Edges connected to the hovered node ride at full opacity + slightly
  // thicker so the relationship structure pops.
  {
    selector: "edge.hover-edge",
    style: {
      opacity: 1,
      "line-opacity": 1,
      width: "mapData(w, 0.6, 5.6, 1.5, 4.5)",
      "z-index": 50,
    },
  },
  // Everything NOT in the hovered neighborhood fades way back.
  {
    selector: ".fade",
    style: { opacity: 0.12, "text-opacity": 0.12 },
  },
];

function renderGraph(graph) {
  // Hide any leftover custom chrome before destroying the cy instance.
  _hideHoverTip();
  _hideActionMenu();
  if (cy) cy.destroy();

  const build = (layoutName) =>
    cytoscape({
      container: $("cy"),
      elements: toElements(graph),
      style: CY_STYLE,
      layout: layoutOptions(layoutName),
      minZoom: 0.08,
      maxZoom: 4.0,
      // Higher = faster zoom per scroll tick. 0.25 felt sluggish; 0.7 is
      // closer to a native browser zoom feel without overshooting.
      wheelSensitivity: 0.7,
    });
  try {
    cy = build(LAYOUT_NAME);
  } catch (e) {
    cy = build("cose"); // fall back if the fcose extension is unavailable
  }
  // Expose the cytoscape instance for in-page debugging and to let helpers
  // outside renderGraph (e.g. focus animations from the detail panel) reach
  // the live graph without threading the reference through every call.
  window.cy = cy;

  // ---- core interactions ----
  cy.on("tap", "node", (evt) => selectNode(evt.target.id()));
  cy.on("tap", "edge", (evt) => {
    const ev = evt.target.data("evidence");
    if (ev) setMessage("“" + ev + "”");
  });
  // Edge hover: keep the bottom-right toast (it's a glossary lookup, not
  // an entity card, and edges are too thin for an anchored popper).
  cy.on("mouseover", "edge", (evt) => {
    const relation = evt.target.data("relation") || "co_occurs_with";
    const meaning = RELATION_MEANING[relation];
    if (meaning) {
      const label = relation.replace(/_/g, " ");
      setMessage(label + " — " + meaning);
    }
  });
  cy.on("tap", (evt) => {
    if (evt.target === cy) clearSelection();
  });
  // Semantic zoom: when the user zooms in past a threshold, raise the
  // density ladder so the next graph load shows more (smaller) nodes. The
  // dispatch is debounced so a single scroll-burst doesn't fire a hundred
  // re-fetches.
  cy.on("zoom", debouncedMaybeDensify);

  // ---- hover halo: brighten the hovered node + its neighbors ----
  // Adds .hovered to the node, .neighbor-hovered to its closed-neighborhood
  // nodes, .hover-edge to incident edges, and .fade to everything else.
  // All classes removed on mouseout. The .dim class used by sticky
  // selection is untouched.
  cy.on("mouseover", "node", (evt) => {
    const n = evt.target;
    const nb = n.openNeighborhood().nodes();
    const edges = n.connectedEdges();
    cy.batch(() => {
      cy.elements().addClass("fade");
      n.removeClass("fade").addClass("hovered");
      nb.removeClass("fade").addClass("neighbor-hovered");
      edges.removeClass("fade").addClass("hover-edge");
    });
  });
  cy.on("mouseout", "node", () => {
    cy.batch(() => {
      cy.elements().removeClass("hovered neighbor-hovered hover-edge fade");
    });
  });

  // ---- custom hover tooltip ----
  // Pure-DOM: one #cy-tooltip div re-positioned + re-populated on each
  // node mouseover. No third-party CDN dependency. The tooltip floats
  // just above the hovered node and follows it if the node moves
  // (e.g. layout settle). Hidden on mouseout.
  cy.on("mouseover", "node", (evt) => _showHoverTip(evt.target));
  cy.on("mouseout", "node", () => _hideHoverTip());
  cy.on("pan zoom", () => { _hideHoverTip(); _hideActionMenu(); });

  // ---- custom right-click action menu ----
  // Pure-DOM: a stacked menu positioned at the right-click point with
  // Focus / Dossier / Show detail / Ignore / Merge. Hidden on Escape,
  // on a click outside, on pan/zoom, on a left-click anywhere.
  cy.on("cxttap", "node", (evt) => _showActionMenu(evt.target,
    evt.renderedPosition || evt.originalEvent));
  cy.on("tap", () => _hideActionMenu());
}

// Custom hover tooltip — no third-party libs. Reposition the single
// #cy-tooltip div over the hovered node's screen-space center and
// fade it in. The node's renderedBoundingBox is already in #cy's
// coordinate space, so positioning is one transform.
function _showHoverTip(node) {
  const tip = $("cy-tooltip");
  if (!tip || !cy) return;
  const data = node.data();
  const colorChip = data.color || "#9aa0a6";
  const neighborCount = node.degree();
  tip.innerHTML =
    '<div class="ail-tip-title">' + escapeHtml(data.label || "") + "</div>" +
    '<div class="ail-tip-meta">' +
    '<span class="ail-tip-badge" style="background:' + colorChip + '">' +
    escapeHtml(data.type || "?") + "</span>" +
    Number(data.mentions || 0) + " mentions · " +
    Number(data.documents || 0) + " docs · " +
    neighborCount + " connections" +
    "</div>" +
    '<div class="ail-tip-hint">click for detail · right-click for actions</div>';
  const bb = node.renderedBoundingBox();
  // Anchor at node's top-center (the ::after arrow lives at the
  // tooltip's bottom-center -- CSS transform translate(-50%, -100%)
  // already handles the offset).
  tip.style.left = ((bb.x1 + bb.x2) / 2) + "px";
  tip.style.top = bb.y1 + "px";
  tip.hidden = false;
  // Force a layout flush so the opacity transition actually animates.
  // Reading offsetWidth is the cheapest way; lint-friendly idiom.
  void tip.offsetWidth;
  tip.classList.add("show");
}

function _hideHoverTip() {
  const tip = $("cy-tooltip");
  if (!tip) return;
  tip.classList.remove("show");
  // Hide after the fade-out completes so the next show transition starts
  // from opacity:0 cleanly.
  setTimeout(() => { if (!tip.classList.contains("show")) tip.hidden = true; }, 140);
}

// Custom right-click action menu. The element already lives in the DOM
// (#cy-actions) with its buttons predefined; we show/hide + position
// it on demand and dispatch by data-act attribute.
let _cyActionsWired = false;

function _showActionMenu(node, pos) {
  const menu = $("cy-actions");
  if (!menu) return;
  _wireActionMenu();
  // Position relative to the #main container so the absolute-positioned
  // menu sits where the user clicked. pos.x/y are relative to the cy
  // container which IS #main (cytoscape sets it to absolute inset:0).
  const x = (pos && typeof pos.x === "number") ? pos.x : (pos && pos.offsetX) || 0;
  const y = (pos && typeof pos.y === "number") ? pos.y : (pos && pos.offsetY) || 0;
  menu.style.left = x + "px";
  menu.style.top = y + "px";
  menu.dataset.nodeId = node.id();
  menu.dataset.nodeLabel = node.data("label") || "";
  // Optional header showing what was clicked.
  menu.querySelector(".cy-actions-header")?.remove();
  const header = document.createElement("div");
  header.className = "cy-actions-header";
  header.textContent = node.data("label") || "(unnamed)";
  menu.prepend(header);
  menu.hidden = false;
  _hideHoverTip();
}

function _hideActionMenu() {
  const menu = $("cy-actions");
  if (menu) menu.hidden = true;
}

// One-time delegated click wiring on the menu buttons. The data-act
// attribute on each button selects which action to take; the node is
// looked up via menu.dataset.nodeId so we don't capture stale refs.
function _wireActionMenu() {
  if (_cyActionsWired) return;
  const menu = $("cy-actions");
  if (!menu) return;
  _cyActionsWired = true;
  menu.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const id = menu.dataset.nodeId;
    const label = menu.dataset.nodeLabel;
    _hideActionMenu();
    if (!id) return;
    switch (btn.dataset.act) {
      case "focus":
        loadGraph(Object.assign(readFilters(), { focus: label }));
        break;
      case "dossier":
        showDossier(id);
        break;
      case "select":
        selectNode(id);
        break;
      case "ignore":
        if (confirm('Ignore "' + label + '"? It will be dropped from the graph.')) {
          applyCorrection("ignore", [label]);
        }
        break;
      case "merge": {
        const target = prompt('Merge "' + label + '" into which entity?');
        if (target && target.trim()) applyCorrection("merge", [label, target.trim()]);
        break;
      }
    }
  });
  // Esc dismisses; click outside dismisses (handled by cy "tap" too).
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") _hideActionMenu();
  });
  document.addEventListener("click", (e) => {
    if (!menu.contains(e.target) && !menu.hidden) {
      // Only dismiss for clicks outside the menu AND outside the cy
      // container's right-click region. cy "tap" already covers left-
      // clicks on the canvas.
      const cyEl = $("cy");
      if (cyEl && cyEl.contains(e.target)) return;
      _hideActionMenu();
    }
  });
}

let densifyTimer = null;
function debouncedMaybeDensify() {
  clearTimeout(densifyTimer);
  densifyTimer = setTimeout(maybeDensify, 350);
}

// Cytoscape default zoom after fit is ~1.0. The thresholds below are tuned
// so a user who scroll-wheel-zooms once or twice past the default sees the
// graph fill in with previously-too-small nodes.
const ZOOM_THRESHOLDS = [1.3, 1.9, 2.6, 3.3];

function maybeDensify() {
  if (!cy) return;
  const z = cy.zoom();
  let target = 0;
  for (let i = 0; i < ZOOM_THRESHOLDS.length; i++) {
    if (z >= ZOOM_THRESHOLDS[i]) target = i + 1;
  }
  // Cap at the ladder length so we don't index past the array.
  target = Math.min(target, ZOOM_DENSITY_LADDER.length - 1);
  if (target === currentDensityIndex) return;
  // Only densify upward as the user drills in; zooming back out keeps the
  // current density so the graph doesn't shrink unexpectedly.
  if (target < currentDensityIndex) return;
  currentDensityIndex = target;
  const max = ZOOM_DENSITY_LADDER[target];
  setMessage("Loading more entities (zoom level " + (target + 1) + ")…");
  loadGraph(Object.assign({}, currentParams, { max_nodes: String(max) }), {
    preserveDensity: true,
  });
}

function highlightNeighborhood(id) {
  if (!cy) return;
  const node = cy.getElementById(String(id));
  if (node.empty()) return;
  cy.elements().addClass("dim");
  node.closedNeighborhood().removeClass("dim");
  cy.elements().unselect();
  node.select();
}

function clearSelection() {
  if (cy) cy.elements().removeClass("dim").unselect();
  $("detail-panel").hidden = true;
}

// ---- detail panel ----------------------------------------------------------

async function selectNode(id) {
  let data;
  try {
    data = await api("/api/node/" + id);
  } catch (e) {
    setMessage("Entity not in the current view.");
    return;
  }
  let docs = { documents: [], total: 0 };
  try {
    docs = await api("/api/node/" + id + "/documents");
  } catch (e) {
    /* leave the source-article list empty */
  }
  let adjacent = { adjacent: [] };
  try {
    adjacent = await api("/api/node/" + id + "/adjacent");
  } catch (e) {
    /* leave the adjacent-list empty */
  }
  renderDetail(data, docs, adjacent);
  highlightNeighborhood(id);
  // Track the visit so "Surprise me" doesn't keep re-picking the same
  // entities — running list of every entity the user has focused on.
  if (data && data.node && data.node.label) {
    lsAddSeen("ail_seen_entities", data.node.label);
  }
  // The Selected-entity panel lives at the top of the sidebar; scroll the
  // sidebar to its top so users always see the populated panel after a
  // click. Without this, on tall sidebars a click can silently update an
  // off-screen panel and look like a no-op.
  const panel = $("detail-panel");
  if (panel && !panel.hidden) {
    const sidebar = $("sidebar");
    if (sidebar) sidebar.scrollTop = 0;
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function momentumHtml(m) {
  if (!m) return "";
  const color = {
    rising: "#5fa55a",
    steady: "#9b6dc7",
    cooling: "#e08a3c",
  }[m.label] || "#7c8593";
  const recent = Number(m.recent_30d || 0);
  const prior = Number(m.prior_30d || 0);
  return (
    '<div class="momentum"><span class="badge" style="background:' +
    color + '">' + escapeHtml(m.label) + "</span> " + recent +
    " mentions in last 30 days · " + prior + " in the 30 days before</div>"
  );
}

function attributesHtml(attrs) {
  attrs = attrs || {};
  const keys = Object.keys(attrs);
  if (!keys.length) return "";
  const labels = {
    email: "Email", role: "Role", affiliation: "Affiliation",
    phone: "Phone", website: "Website",
  };
  return (
    "<h3>Attributes</h3>" +
    '<ul class="attributes">' +
    keys
      .map((k) => {
        const v = attrs[k];
        const label = labels[k] || k;
        if (k === "email") {
          return (
            '<li><span class="attr-key">' + escapeHtml(label) +
            '</span><a href="mailto:' + encodeURI(v) + '">' +
            escapeHtml(v) + "</a></li>"
          );
        }
        return (
          '<li><span class="attr-key">' + escapeHtml(label) +
          "</span>" + escapeHtml(String(v)) + "</li>"
        );
      })
      .join("") +
    "</ul>"
  );
}

function readBadge(a) {
  const reads = Number(a.claude_read_count || 0);
  if (!reads) return '<span class="read-badge unread">unread</span>';
  const cls = a.claude_read_fresh ? "fresh" : "stale";
  return (
    '<span class="read-badge ' + cls + '">' + reads + "× · " +
    (a.claude_read_fresh ? "fresh" : "stale") + "</span>"
  );
}

// ---- in-app article reader -------------------------------------------------

async function openArticle(contentHash) {
  if (!contentHash) return;
  $("article-drawer").hidden = false;
  $("article-body").innerHTML = '<p class="muted">Loading…</p>';
  let doc;
  try {
    doc = await api("/api/document/" + encodeURIComponent(contentHash));
  } catch (e) {
    $("article-body").innerHTML =
      '<p class="muted">Article not available.</p>';
    return;
  }
  $("article-meta").innerHTML =
    '<h2 class="article-title">' + escapeHtml(doc.title || "(no title)") + "</h2>" +
    '<div class="article-src">' + escapeHtml(doc.source || "") +
    (doc.published ? " · " + escapeHtml(doc.published) : "") + "</div>" +
    '<div class="article-actions">' +
    '<a class="article-link" href="' + encodeURI(doc.url) +
    '" target="_blank" rel="noopener noreferrer">View original ↗</a>' +
    ' <button id="article-mark-read" class="ghost">Mark as Claude-read</button>' +
    " " + readBadge(doc) + "</div>";
  $("article-body").innerHTML = paragraphify(doc.raw_text || "(no text)");
  $("article-mark-read").addEventListener("click", async () => {
    try {
      await api("/api/document/mark-read", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content_hash: doc.content_hash }),
      });
      setMessage("Marked as Claude-read.");
      // Re-render the article so the badge reflects the new state.
      openArticle(doc.content_hash);
    } catch (e) {
      setMessage("Mark-read failed: " + e.message);
    }
  });
}

function paragraphify(text) {
  return text
    .split(/\n\n+/)
    .map((p) => "<p>" + escapeHtml(p.trim()) + "</p>")
    .join("");
}

function closeArticle() {
  $("article-drawer").hidden = true;
  $("article-body").innerHTML = "";
}

function articlesHtml(docs) {
  const list = (docs && docs.documents) || [];
  if (!list.length) return "";
  const total = (docs && docs.total) || list.length;
  return (
    "<h3>Source articles (" + total + ")</h3>" +
    '<ul class="articles">' +
    list
      .slice(0, 10)
      .map(
        (a) =>
          '<li><a class="article-open" href="#read-' +
          encodeURIComponent(a.content_hash || "") +
          '" data-hash="' + escapeHtml(a.content_hash || "") + '">' +
          escapeHtml(a.title || a.url) + "</a> " + readBadge(a) +
          '<div class="src">' + escapeHtml(a.source || "") +
          (a.published ? " · " + escapeHtml(a.published) : "") +
          " · <a href=\"" + encodeURI(a.url) +
          '" target="_blank" rel="noopener noreferrer">original ↗</a>' +
          "</div></li>"
      )
      .join("") +
    "</ul>"
  );
}

function miniSparklineHtml(timeline) {
  // A 12-cell sparkline of recent monthly mentions, shown inline in the
  // detail panel so a single click reveals trend without opening the
  // full dossier modal. Padded with empty months so a node that ramped
  // up over only the last quarter still reads as "ramping."
  if (!timeline || !timeline.length) return "";
  const cells = timeline.slice(-12);
  const max = Math.max(1, ...cells.map((c) => c.count));
  const bars = cells
    .map(
      (c) =>
        '<span class="mini-bar" title="' + escapeHtml(c.month) + ": " +
        c.count + '" style="height:' +
        Math.max(3, Math.round((100 * c.count) / max)) + '%"></span>'
    )
    .join("");
  return (
    '<div class="mini-spark">' + bars + "</div>" +
    '<div class="mini-spark-note">monthly mentions, last ' + cells.length +
    " months</div>"
  );
}

function adjacentHtml(adjList) {
  if (!adjList || !adjList.length) return "";
  return (
    "<h3>You may not know about</h3>" +
    '<ul class="adjacent-list">' +
    adjList
      .map(
        (a) =>
          '<li data-id="' + a.id + '"><span>' + escapeHtml(a.label) +
          spikeBadge(a.id) + "</span><em>" + a.shared_neighbors +
          " shared</em></li>"
      )
      .join("") +
    "</ul>"
  );
}

function renderDetail(data, docs, adjacent) {
  const n = data.node;
  const color = TYPE_COLORS[n.type] || DEFAULT_COLOR;
  const neighbors = data.neighbors.slice(0, 14);
  const adjList = (adjacent && adjacent.adjacent) || [];
  $("detail").innerHTML =
    '<div class="entity-name">' + escapeHtml(n.label) + spikeBadge(n.id) +
    "</div>" +
    '<div class="entity-meta">' +
    '<span class="badge" style="background:' + color + '">' + escapeHtml(n.type) +
    "</span>" + n.mentions + " mentions · " + n.documents + " documents</div>" +
    miniSparklineHtml(docs && docs.timeline) +
    attributesHtml(n.attributes) +
    "<h3>Top connections (" + data.neighbors.length + ")</h3>" +
    '<ul class="neighbors">' +
    neighbors
      .map((x) => {
        let meta;
        let evidence = "";
        if (x.relation && x.relation !== "co_occurs_with") {
          const arrow = x.direction === "out" ? "&rarr;" : "&larr;";
          meta =
            '<em class="rel">' + arrow + " " +
            escapeHtml(x.relation.replace(/_/g, " ")) +
            confTag(x.confidence) + "</em>";
          if (x.evidence) {
            evidence =
              '<div class="evidence">“' + escapeHtml(x.evidence) +
              '”</div>';
          }
        } else {
          meta = "<em>" + x.weight + "</em>";
        }
        return (
          '<li data-id="' + x.id + '"><div class="nb-top"><span>' +
          escapeHtml(x.label) + "</span>" + meta + "</div>" + evidence + "</li>"
        );
      })
      .join("") +
    "</ul>" +
    adjacentHtml(adjList) +
    articlesHtml(docs) +
    '<div class="row"><button data-act="focus">Focus here</button>' +
    '<button data-act="dossier" class="ghost">Dossier</button></div>' +
    '<div class="row"><button data-act="ignore" class="ghost">Ignore</button>' +
    '<button data-act="merge" class="ghost">Merge into…</button></div>';
  $("detail-panel").hidden = false;
  // Adjacent-list rows are clickable to navigate to the suggested entity —
  // the whole point of the section is "you may not know about this one
  // either, here's a one-click jump."
  $("detail").querySelectorAll(".adjacent-list li").forEach((li) =>
    li.addEventListener("click", () => selectNode(li.dataset.id))
  );

  $("detail").querySelectorAll(".neighbors li").forEach((li) =>
    li.addEventListener("click", () => selectNode(li.dataset.id))
  );
  $("detail").querySelector('[data-act="focus"]').addEventListener("click", () =>
    loadGraph({ focus: n.label })
  );
  $("detail").querySelector('[data-act="dossier"]').addEventListener("click", () =>
    showDossier(n.id)
  );
  $("detail").querySelector('[data-act="ignore"]').addEventListener("click", () => {
    if (confirm('Ignore "' + n.label + '"? It will be dropped from the graph.'))
      applyCorrection("ignore", [n.label]);
  });
  $("detail").querySelector('[data-act="merge"]').addEventListener("click", () => {
    const target = prompt('Merge "' + n.label + '" into which entity?');
    if (target && target.trim()) applyCorrection("merge", [n.label, target.trim()]);
  });
}

// ---- corrections -----------------------------------------------------------

async function applyCorrection(action, terms) {
  setMessage("Applying correction…");
  try {
    const res = await api("/api/correct", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action, terms: terms }),
    });
    setMessage(
      "Correction applied — graph rebuilt (" +
        res.graph.nodes + " nodes, " + res.graph.edges + " edges)."
    );
    await refreshMeta();
    await loadGraph(currentParams);
  } catch (e) {
    setMessage("Correction failed: " + e.message);
  }
}

// ---- modal: dashboard & dossier --------------------------------------------

function openModal(html) {
  $("modal-body").innerHTML = html;
  $("modal-body").scrollTop = 0;
  $("modal").hidden = false;
}

function closeModal() {
  $("modal").hidden = true;
  $("modal-body").innerHTML = "";
}

function barChart(rows) {
  const max = Math.max(1, ...rows.map((r) => r[1]));
  return (
    '<div class="bars">' +
    rows
      .map(
        (r) =>
          '<div class="bar-row"><span class="bar-label">' +
          escapeHtml(String(r[0])) + '</span><span class="bar-track">' +
          '<span class="bar-fill" style="width:' +
          ((100 * r[1]) / max).toFixed(1) + '%"></span></span>' +
          '<span class="bar-val">' + Number(r[1]).toLocaleString() +
          "</span></div>"
      )
      .join("") +
    "</div>"
  );
}

function clickList(rows) {
  // Rows may be 2-tuples [name, meta] or 3-tuples [name, meta, nodeId].
  // The optional third element lets the renderer stamp a spike badge by
  // node id without changing existing callers.
  return (
    '<ul class="click-list">' +
    rows
      .map(
        (r) =>
          '<li data-focus="' + escapeHtml(r[0]) + '"><span>' +
          escapeHtml(r[0]) + spikeBadge(r[2]) + "</span><em>" +
          escapeHtml(String(r[1])) + "</em></li>"
      )
      .join("") +
    "</ul>"
  );
}

// Wire every [data-focus] element in the modal to focus the graph on it.
function wireFocusClicks() {
  $("modal-body")
    .querySelectorAll("[data-focus]")
    .forEach((el) =>
      el.addEventListener("click", () => {
        closeModal();
        loadGraph(Object.assign(readFilters(), { focus: el.dataset.focus }));
      })
    );
}

async function showDashboard() {
  let ov;
  try {
    ov = await api("/api/overview");
  } catch (e) {
    setMessage("Dashboard unavailable: " + e.message);
    return;
  }
  const f = ov.funnel;
  const q = ov.quality;
  const stat = (k, v) =>
    '<div class="dash-stat"><div class="v">' + v + '</div>' +
    '<div class="k">' + k + "</div></div>";
  const line = (k, v) =>
    '<div class="stat"><span class="label">' + k +
    '</span><span class="value">' + v + "</span></div>";
  const typedRels = ov.relation_types.filter((r) => r[0] !== "co_occurs_with");
  const cooc = ov.relation_types.find((r) => r[0] === "co_occurs_with");
  let html =
    '<h2 class="modal-title">Data overview</h2>' +
    '<div class="dash-grid">' +
    stat("Corpus documents", Number(f.documents).toLocaleString()) +
    stat("Raw NER mentions", Number(f.raw_mentions).toLocaleString()) +
    stat("Graph entities", Number(f.nodes).toLocaleString()) +
    stat("Relationships", Number(f.edges).toLocaleString()) +
    "</div>" +
    "<h3>Entity types</h3>" +
    barChart(ov.entity_types.map((r) => [r[0], r[1]])) +
    "<h3>Typed relationship types</h3>" +
    (typedRels.length
      ? barChart(typedRels.map((r) => [r[0].replace(/_/g, " "), r[1]]))
      : "<p>none yet</p>") +
    (cooc ? line("plus co-occurrence edges", cooc[1].toLocaleString()) : "") +
    "<h3>Most prominent entities</h3>" +
    clickList(
      ov.top_by_mentions.map((n) => [
        n.canonical_name,
        n.mention_count.toLocaleString() + " mentions",
        n.id,
      ])
    ) +
    "<h3>Most connected entities</h3>" +
    clickList(
      ov.most_connected.map((r) => [r[0], r[2].toLocaleString() + " links"])
    ) +
    "<h3>Nodes by mention count</h3>" +
    barChart(ov.distributions.mentions) +
    "<h3>Data quality</h3>" +
    line("Single-mention nodes", Math.round(q.singleton_pct) + "% of nodes") +
    line("Isolated nodes", Math.round(q.isolated_pct) + "% of nodes") +
    line("Partial-name duplicates", q.partial_name_dups);
  if (ov.reading) {
    const r = ov.reading;
    html +=
      "<h3>Claude reading coverage</h3>" +
      line("Fresh (read since last corpus update)",
           r.fresh + " of " + r.documents +
           " (" + Math.round(r.fresh_pct) + "%)") +
      line("Stale (read, but corpus has since changed)", r.stale) +
      line("Never read", r.never_read);
  }
  openModal(html);
  wireFocusClicks();
}

async function showDossier(id) {
  let data, docs;
  try {
    data = await api("/api/node/" + id);
    docs = await api("/api/node/" + id + "/documents");
  } catch (e) {
    setMessage("Dossier unavailable.");
    return;
  }
  const n = data.node;
  const color = TYPE_COLORS[n.type] || DEFAULT_COLOR;
  const typed = data.neighbors.filter(
    (x) => x.relation && x.relation !== "co_occurs_with"
  );
  const cooc = data.neighbors.filter(
    (x) => !x.relation || x.relation === "co_occurs_with"
  );
  let html =
    '<h2 class="modal-title">' + escapeHtml(n.label) + "</h2>" +
    '<div class="entity-meta"><span class="badge" style="background:' +
    color + '">' + escapeHtml(n.type) + "</span>" + n.mentions +
    " mentions · " + n.documents + " documents</div>";
  if (n.first_seen || n.last_seen) {
    html +=
      '<div class="dossier-dates">Seen ' +
      escapeHtml((n.first_seen || "?").slice(0, 10)) + " – " +
      escapeHtml((n.last_seen || "?").slice(0, 10)) + "</div>";
  }
  html += momentumHtml(docs.momentum);
  html += attributesHtml(n.attributes);
  if (typed.length) {
    html +=
      "<h3>Typed relationships (" + typed.length + ")</h3>" +
      typed
        .map((x) => {
          const arrow = x.direction === "out" ? "&rarr;" : "&larr;";
          return (
            '<div class="rel-line" data-id="' + x.id + '">' +
            '<span class="rel">' + arrow + " " +
            escapeHtml(x.relation.replace(/_/g, " ")) +
            confTag(x.confidence) + "</span> " +
            escapeHtml(x.label) +
            (x.evidence
              ? '<div class="evidence">“' + escapeHtml(x.evidence) + '”</div>'
              : "") +
            "</div>"
          );
        })
        .join("");
  }
  if (cooc.length) {
    html +=
      "<h3>Often appears with</h3>" +
      clickList(cooc.slice(0, 18).map((x) => [x.label, x.weight + " shared"]));
  }
  if (docs.timeline && docs.timeline.length > 1) {
    html +=
      "<h3>Activity over time</h3>" +
      barChart(docs.timeline.map((v) => [v.month, v.count]));
  }
  html += articlesHtml(docs);
  openModal(html);
  $("modal-body")
    .querySelectorAll(".rel-line[data-id]")
    .forEach((el) =>
      el.addEventListener("click", () => showDossier(el.dataset.id))
    );
  wireFocusClicks();
}

async function showTrends() {
  let t;
  try {
    t = await api("/api/trends");
  } catch (e) {
    setMessage("Trends unavailable: " + e.message);
    return;
  }
  // The spike list is its own endpoint so trends.build_trends doesn't
  // need to recompute the per-node date histograms in the common case.
  let spikes = [];
  try {
    const sp = await api("/api/spikes?limit=8");
    spikes = sp.spikes || [];
  } catch (e) { /* leave empty */ }
  let html =
    '<h2 class="modal-title">Trends over time</h2>' +
    "<h3>Document volume by month</h3>" +
    (t.document_volume.length
      ? barChart(t.document_volume.map((v) => [v.month, v.count]))
      : "<p>no dated documents</p>");
  if (spikes.length) {
    html += "<h3>Notable spikes</h3>" +
      '<p class="mini-spark-note">' +
      "Entities the corpus is suddenly talking about — recent rate at " +
      "least 3× their long-term baseline.</p>" +
      clickList(
        spikes.map((s) => [
          s.name,
          s.recent + " recent · " + s.ratio + "× baseline",
          s.id,
        ])
      );
  }
  html +=
    "<h3>Newly appeared entities</h3>" +
    clickList(
      t.new_entities.map((n) => [n.name, "first seen " + n.first_seen, n.id])
    ) +
    "<h3>Most recently active entities</h3>" +
    clickList(
      t.recent_entities.map((n) => [n.name, "last seen " + n.last_seen, n.id])
    );
  openModal(html);
  wireFocusClicks();
}

// Restore the default landing view. Reachable from both the View-panel
// "Reset" and a sticky topbar button, so a user lost in a deep filter
// state always has a one-click way back to the starting graph.
function resetView() {
  $("search").value = "";
  $("search-results").innerHTML = "";
  $("f-type").value = "";
  if ($("f-src-type")) $("f-src-type").value = "";
  if ($("f-dst-type")) $("f-dst-type").value = "";
  $("f-min-mentions").value = "0";
  $("f-min-weight").value = "2";
  $("f-max-nodes").value = "90";
  $("f-relations-only").checked = false;
  if ($("f-min-conf")) {
    $("f-min-conf").value = "0";
    $("f-min-conf-out").textContent = "0%";
  }
  if ($("f-min-strength")) {
    $("f-min-strength").value = "0";
    $("f-min-strength-out").textContent = "0.00";
  }
  // Clear the focus from the URL too — leaving the hash with focus= would
  // re-apply it on the next loadGraph and undo the reset.
  history.replaceState(null, "", window.location.pathname + window.location.search);
  loadGraph(readFilters());
  setMessage("View reset to the default landscape.");
}

function applyStarter(key) {
  const view = STARTER_VIEWS[key];
  if (!view) return;
  const p = view.params;
  $("f-type").value = p.type || "";
  $("f-relations-only").checked = !!p.relations_only;
  $("f-min-mentions").value = "0";
  $("f-min-weight").value = "2";
  $("f-max-nodes").value = "90";
  $("search").value = "";
  $("search-results").innerHTML = "";
  loadGraph(readFilters());
}

function showGuide() {
  let html =
    '<h2 class="modal-title">Navigating the AI landscape</h2>' +
    "<p>This knowledge graph is built from defense and AI reporting and " +
    "SBIR/STTR awards. Each node is an entity; blue arrows are typed " +
    "relationships read from the source text, grey lines are " +
    "co-occurrence.</p>" +
    "<h3>What lives where</h3>" +
    '<ul class="guide-list">' +
    "<li><b>Corpus</b> — the raw articles themselves, one JSON line per " +
    "article (title, body, source, published date). The version-controlled " +
    "source of truth that everything else is derived from. Tap an article " +
    "title anywhere in the app to read it in a side drawer.</li>" +
    "<li><b>Knowledge graph</b> — entities (people, orgs, products, " +
    "places, concepts) and the relationships between them, extracted from " +
    "the corpus. The web of nodes you see in the middle.</li>" +
    "<li><b>Dossier</b> — a one-page profile of a single entity, opened " +
    "from a node or any entity link. It pulls together that entity's " +
    "typed relationships (with the evidence sentence behind each one), " +
    "the entities it co-occurs with, an activity timeline, a rising/" +
    "steady/cooling momentum badge, and the source articles it appears " +
    "in. The dossier is a <em>view</em> over the corpus + graph for one " +
    "entity; the corpus is the substrate underneath every dossier.</li>" +
    "</ul>" +
    "<h3>How to explore</h3>" +
    '<ul class="guide-list">' +
    "<li>Click any node for its connections, the evidence behind each " +
    "relationship, and the source articles.</li>" +
    "<li><b>Story tours</b> in the sidebar walk you through curated threads " +
    "(Iran war, AI laser stack, low-cost strike, frontier models, " +
    "Replicator→CCA) with a card per stop.</li>" +
    "<li><b>What changed</b> in the sidebar shows new entities and articles " +
    "since your last visit.</li>" +
    "<li><b>Search</b> finds an entity or article (and now narrows by date " +
    "or relation); <b>Connection</b> shows how two entities are linked.</li>" +
    "<li><b>Today's briefing</b>, <b>Trends</b>, and <b>Dashboard</b> " +
    "summarise the whole landscape.</li>" +
    "<li>Sliders in <b>View</b> filter by relationship <em>confidence</em> " +
    "and co-occurrence <em>strength</em> — useful for separating stated " +
    "facts from passing mentions.</li>" +
    "</ul>" +
    "<h3>Relationship types</h3>" +
    '<ul class="guide-list">' +
    RELATION_GLOSSARY.map(
      (r) =>
        "<li><b>" + escapeHtml(r[0]) + "</b> — " + escapeHtml(r[1]) + "</li>"
    ).join("") +
    "</ul>" +
    "<h3>Start here</h3>" +
    '<div class="starter-grid modal-starters">' +
    Object.keys(STARTER_VIEWS)
      .map(
        (k) =>
          '<button data-starter="' + k + '">' +
          escapeHtml(STARTER_VIEWS[k].label) + "</button>"
      )
      .join("") +
    "</div>";
  openModal(html);
  $("modal-body")
    .querySelectorAll("[data-starter]")
    .forEach((b) =>
      b.addEventListener("click", () => {
        closeModal();
        applyStarter(b.dataset.starter);
      })
    );
}

function confTag(value) {
  return value != null
    ? ' <span class="conf">' + Math.round(value * 100) + "%</span>"
    : "";
}

function relLine(e) {
  return (
    '<div class="rel-line"><span class="rel">' +
    escapeHtml(e.subject) + " &rarr; " +
    escapeHtml(e.relation.replace(/_/g, " ")) +
    confTag(e.confidence) + " &rarr; " +
    escapeHtml(e.object) + "</span>" +
    (e.evidence
      ? '<div class="evidence">“' + escapeHtml(e.evidence) + '”</div>'
      : "") +
    "</div>"
  );
}

async function showBriefing(opts) {
  opts = opts || {};
  const days = Number(opts.days || BRIEFING_DAYS || 7);
  BRIEFING_DAYS = days;
  const params = new URLSearchParams();
  params.set("days", String(days));
  if (opts.subfield) params.set("subfield", opts.subfield);
  let b;
  try {
    b = await api("/api/briefing?" + params.toString());
  } catch (e) {
    setMessage("Briefing unavailable: " + e.message);
    return;
  }
  const t = b.totals;
  const subfieldHeader = opts.subfield
    ? '<div class="dossier-dates">Scoped to: ' +
      escapeHtml(opts.subfieldLabel || opts.subfield) + " · " +
      '<a href="#" id="briefing-clear-subfield">show full briefing</a></div>'
    : "";
  // Time-sliced tabs let the same modal serve a morning-headline (24h),
  // weekly catch-up (7d), or vacation-recovery (30d) read.
  const tabsHtml =
    '<div class="briefing-tabs">' +
    [
      ["24h", 1],
      ["7d", 7],
      ["30d", 30],
    ]
      .map(
        (pair) =>
          '<button class="briefing-tab' +
          (Number(pair[1]) === days ? " active" : "") +
          '" data-days="' + pair[1] + '">' + pair[0] + "</button>"
      )
      .join("") +
    "</div>";
  let html =
    '<h2 class="modal-title">Landscape briefing</h2>' +
    tabsHtml +
    subfieldHeader +
    '<div class="dossier-dates">' + b.window_days + "-day window · " +
    t.documents + " documents · " + t.entities + " entities · " +
    t.typed_relations + " typed relationships</div>";
  if (b.sbir_funding && b.sbir_funding.awards) {
    html +=
      '<div class="dossier-dates">SBIR/STTR funding: ' +
      b.sbir_funding.awards + " AI-related awards · $" +
      Number(b.sbir_funding.total_amount).toLocaleString() + " total</div>";
  }
  // The narrative is served from a daily-generated sidecar snapshot.
  // Render it inline on first open so visitors without a key still see
  // the latest synthesis; the button is now "Refresh" (operator-only,
  // shown only when the server reports can_refresh=true).
  html += '<div id="narrative-box"><p class="muted">Loading narrative…</p></div>';
  if (b.trending_topics.length) {
    html += "<h3>Trending AI topics</h3>" +
      barChart(b.trending_topics.map((c) => [c.name, c.mentions]));
  if (b.trending_topics.some((c) => SPIKE_IDS.has(Number(c.id)))) {
    html += '<p class="mini-spark-note">' +
      "↑ marks a recent spike vs the entity's long-term rate.</p>";
  }
  }
  html += "<h3>Most active entities</h3>" +
    clickList(b.top_entities.map((n) => [n.name, n.mentions + " mentions", n.id]));
  if (b.contract_awards.length) {
    html += "<h3>Contract awards &amp; deals</h3>" +
      b.contract_awards.map(relLine).join("");
  }
  if (b.key_relationships.length) {
    html += "<h3>Key relationships</h3>" +
      b.key_relationships.map(relLine).join("");
  }
  html += "<h3>Documents in the last " + b.window_days + " days (" +
    b.recent_count + ")</h3>" +
    '<ul class="articles">' +
    b.recent_documents
      .map(
        (d) =>
          '<li><a href="' + encodeURI(d.url) +
          '" target="_blank" rel="noopener noreferrer">' +
          escapeHtml(d.title || d.url) + '</a><div class="src">' +
          escapeHtml(d.source) + "</div></li>"
      )
      .join("") +
    "</ul>";
  openModal(html);
  wireFocusClicks();
  // Time-slice tabs (24h / 7d / 30d) reload the briefing in place.
  $("modal-body").querySelectorAll(".briefing-tab").forEach((btn) =>
    btn.addEventListener("click", () => {
      const next = Number(btn.dataset.days);
      if (next && next !== days) {
        showBriefing({
          days: next,
          subfield: opts.subfield,
          subfieldLabel: opts.subfieldLabel,
        });
      }
    })
  );
  const clearSub = $("briefing-clear-subfield");
  if (clearSub) {
    clearSub.addEventListener("click", (e) => {
      e.preventDefault();
      showBriefing({ days });
    });
  }
  loadBriefingNarrative();
}

// Render the analyst-narrative cache section into #narrative-box inside
// the open briefing modal. Called on initial open (cache-first read) and
// again after a refresh click.
async function loadBriefingNarrative(opts) {
  opts = opts || {};
  const box = $("narrative-box");
  if (!box) return;
  if (opts.refresh) box.innerHTML = '<p class="muted">Refreshing narrative…</p>';
  let r;
  const url = opts.refresh
    ? "/api/briefing/narrative?refresh=1"
    : "/api/briefing/narrative";
  try {
    r = await api(url);
  } catch (e) {
    box.innerHTML =
      '<p class="narrative-note">Narrative unavailable: ' +
      escapeHtml(e.message) + "</p>";
    return;
  }
  box.innerHTML =
    synthesisBodyHtml(r) +
    synthesisFooterHtml(r, {
      docsLabel: "recent documents",
      refreshTarget: "briefing",
    });
  const btn = box.querySelector(".synthesis-refresh");
  if (btn) {
    btn.addEventListener("click", () =>
      loadBriefingNarrative({ refresh: true })
    );
  }
}

async function findPath() {
  const from = $("path-from").value.trim();
  const to = $("path-to").value.trim();
  if (!from || !to) {
    setMessage("Enter both a 'from' and a 'to' entity.");
    return;
  }
  let res;
  try {
    res = await api(
      "/api/path?from=" + encodeURIComponent(from) +
      "&to=" + encodeURIComponent(to)
    );
  } catch (e) {
    setMessage("Path search failed: " + e.message);
    return;
  }
  let html = '<h2 class="modal-title">Connection path</h2>';
  if (!res.found) {
    html +=
      "<p>No path found between “" + escapeHtml(res.from.label) +
      "” and “" + escapeHtml(res.to.label) +
      "” in the current graph.</p>";
    openModal(html);
    return;
  }
  html +=
    '<div class="dossier-dates">' + res.length +
    (res.length === 1 ? " step" : " steps") +
    " from “" + escapeHtml(res.from.label) + "” to “" +
    escapeHtml(res.to.label) + "”</div><div class=\"path-chain\">";
  for (let i = 0; i < res.nodes.length; i++) {
    const node = res.nodes[i];
    html +=
      '<div class="path-node" data-focus="' + escapeHtml(node.label) +
      '">' + escapeHtml(node.label) +
      ' <span class="path-type">' + escapeHtml(node.type) + "</span></div>";
    if (i < res.edges.length) {
      const e = res.edges[i];
      const rel =
        e.relation === "co_occurs_with"
          ? "co-occurs with"
          : e.relation.replace(/_/g, " ");
      html +=
        '<div class="path-edge">&darr; ' + escapeHtml(rel) +
        (e.evidence
          ? '<div class="evidence">“' + escapeHtml(e.evidence) + '”</div>'
          : "") +
        "</div>";
    }
  }
  html += "</div>";
  openModal(html);
  wireFocusClicks();
}

// ---- capabilities modal (AI subfield map) ---------------------------------

async function showCapabilities() {
  let data;
  try {
    data = await api("/api/capabilities");
  } catch (e) {
    setMessage("Capabilities unavailable: " + e.message);
    return;
  }
  const subfields = data.subfields || [];
  let html =
    '<h2 class="modal-title">AI capabilities map</h2>' +
    "<p>The eight subfields the corpus organizes around — concepts on " +
    "the left, the organizations active in each on the right. Pick a card " +
    "to focus the graph or jump to that subfield's briefing.</p>" +
    '<div class="cap-cards">';
  for (const s of subfields) {
    const concepts = s.concepts || [];
    const orgs = s.top_organizations || [];
    html +=
      '<div class="cap-card" data-subfield="' + escapeHtml(s.id) + '">' +
      '<div class="cap-card-head">' +
      '<div class="cap-card-title">' + escapeHtml(s.label) + "</div>" +
      '<div class="cap-card-tagline">' + escapeHtml(s.tagline) + "</div>" +
      '<div class="cap-card-meta">' + s.concept_count + " concepts · " +
      s.mentions + " total mentions · " + s.org_player_count +
      " orgs active</div>" +
      "</div>" +
      '<div class="cap-card-body">' +
      "<div><h4>Leading concepts</h4>" +
      (concepts.length
        ? '<ul class="cap-list">' +
          concepts
            .map(
              (c) =>
                '<li data-focus="' + escapeHtml(c.name) + '"><span>' +
                escapeHtml(c.name) + spikeBadge(c.id) + "</span><em>" +
                c.mentions + "</em></li>"
            )
            .join("") +
          "</ul>"
        : '<p class="mini-spark-note">No live nodes in this subfield yet.</p>') +
      "</div>" +
      "<div><h4>Top organizations</h4>" +
      (orgs.length
        ? '<ul class="cap-list">' +
          orgs
            .map(
              (o) =>
                '<li data-focus="' + escapeHtml(o.name) + '"><span>' +
                escapeHtml(o.name) + spikeBadge(o.id) + "</span><em>" +
                o.weight + "</em></li>"
            )
            .join("") +
          "</ul>"
        : '<p class="mini-spark-note">No org links yet.</p>') +
      "</div>" +
      "</div>" +
      '<div class="cap-card-actions">' +
      '<button data-cap-brief="' + escapeHtml(s.id) +
      '" data-cap-label="' + escapeHtml(s.label) +
      '">What\'s happening here</button>' +
      '<button class="ghost" data-cap-focus="' + escapeHtml(s.label) +
      '">Focus graph</button>' +
      "</div>" +
      "</div>";
  }
  html += "</div>";
  openModal(html);
  wireFocusClicks();
  // "What's happening here" → subfield-scoped briefing (item #7).
  $("modal-body")
    .querySelectorAll("[data-cap-brief]")
    .forEach((btn) =>
      btn.addEventListener("click", () => {
        closeModal();
        showBriefing({
          days: BRIEFING_DAYS,
          subfield: btn.dataset.capBrief,
          subfieldLabel: btn.dataset.capLabel,
        });
      })
    );
  // "Focus graph" → graph subgraph anchored on the subfield label.
  // Single-concept focus is simpler than multi-anchor focus and the user
  // can drill from there using the graph itself.
  $("modal-body")
    .querySelectorAll("[data-cap-focus]")
    .forEach((btn) =>
      btn.addEventListener("click", () => {
        closeModal();
        loadGraph(Object.assign(readFilters(), { focus: btn.dataset.capFocus }));
      })
    );
}

// ---- trajectory modal (corpus over many months) ---------------------------

// ---- Pipeline modal (ingest run history) -----------------------------------
//
// Reads /api/history and renders a Trajectory-style readout for daily
// scrape runs: summary stats up top, a per-run bar (added vs filtered),
// a per-feed health callout (any feed currently erroring), and a tail
// table of recent runs with timing + error rows. Same idiom as the
// existing Dashboard / Trends / Trajectory modals so the operator
// learns one pattern.

function _pipelineTimeAgo(iso) {
  if (!iso) return "(unknown)";
  const t = new Date(iso);
  if (isNaN(t)) return iso;
  const s = (Date.now() - t.getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " d ago";
}

async function showPipeline() {
  let data;
  try {
    data = await api("/api/history?limit=30");
  } catch (e) {
    setMessage("Pipeline unavailable: " + e.message);
    return;
  }
  const runs = data.runs || [];
  if (!runs.length) {
    openModal(
      '<h2 class="modal-title">Pipeline</h2>' +
      '<p class="muted">No runs recorded yet. The daily scrape (or' +
      ' <code>ailandscape run</code>) writes a record on each invocation.</p>'
    );
    return;
  }
  const latest = runs[runs.length - 1];
  const totalAdded = runs.reduce((a, r) => a + (r.added || 0), 0);
  const totalFiltered = runs.reduce((a, r) => a + (r.filtered_non_ai || 0), 0);
  const totalFetched = runs.reduce((a, r) => a + (r.fetched || 0), 0);
  const peak = Math.max(
    1,
    ...runs.map((r) => Math.max(r.added || 0, r.filtered_non_ai || 0))
  );

  const stat = (k, v) =>
    '<div class="dash-stat"><div class="v">' + v + '</div>' +
    '<div class="k">' + k + "</div></div>";

  let html =
    '<h2 class="modal-title">Pipeline ingest history</h2>' +
    '<p class="mini-spark-note">One record per <code>ailandscape run</code>' +
    ' invocation. Sourced from snapshots/run-history.jsonl.</p>' +
    '<div class="dash-grid">' +
    stat("Latest run", _pipelineTimeAgo(latest.finished_at)) +
    stat("Runs in window", runs.length + " of " + data.total) +
    stat("Articles added", totalAdded.toLocaleString()) +
    stat("Filtered (non-AI)", totalFiltered.toLocaleString()) +
    "</div>";

  // Broken feeds callout (anything that errored in any visible run).
  const broken = data.broken_feeds || [];
  if (broken.length) {
    html += "<h3>Currently failing feeds</h3>" +
      '<ul class="click-list">' +
      broken.slice(0, 10).map((f) =>
        '<li><span>' + escapeHtml(f.name) + '</span>' +
        '<em>' + f.runs_failing + " run(s) — " + escapeHtml(f.last_error) +
        "</em></li>"
      ).join("") +
      "</ul>";
  }

  // Per-run mini bars: added (green) vs filtered_non_ai (orange).
  // Most-recent first reads more naturally for a returning user.
  const ordered = runs.slice().reverse();
  html += "<h3>Recent runs (added vs filtered)</h3>" +
    '<div class="pipeline-runs">' +
    ordered.map((r) => {
      const added = r.added || 0;
      const filt = r.filtered_non_ai || 0;
      const fetched = r.fetched || 0;
      const finished = (r.finished_at || "").slice(0, 16).replace("T", " ");
      const errors = Object.entries(r.feeds || {})
        .filter(([_, info]) => info && info.error)
        .map(([name]) => name);
      const errBadge = errors.length
        ? ' <span class="pipeline-err" title="' +
          escapeHtml(errors.join(", ")) + '">⚠ ' + errors.length +
          "</span>"
        : "";
      return (
        '<div class="pipeline-row">' +
          '<div class="pipeline-when">' + escapeHtml(finished) + errBadge + "</div>" +
          '<div class="pipeline-bar-stack">' +
            '<span class="pipeline-bar pipeline-bar-added" ' +
            'style="width:' + Math.min(100, Math.round((100 * added) / peak)) + '%" ' +
            'title="' + added + ' articles added"></span>' +
            '<span class="pipeline-bar pipeline-bar-filt" ' +
            'style="width:' + Math.min(100, Math.round((100 * filt) / peak)) + '%" ' +
            'title="' + filt + ' filtered as non-AI"></span>' +
          "</div>" +
          '<div class="pipeline-counts">' +
            '<span class="pc-added" title="added">+' + added + "</span>" +
            '<span class="pc-filt" title="filtered as non-AI">−' + filt + "</span>" +
            '<span class="pc-fetch" title="fetched (deduped against existing)">' + fetched + "↓</span>" +
          "</div>" +
        "</div>"
      );
    }).join("") +
    "</div>";

  // Throughput numbers in a tidy bottom block.
  const totalScrapeS = runs.reduce((a, r) => a + (r.scrape_seconds || 0), 0);
  const totalRebuildS = runs.reduce((a, r) => a + (r.rebuild_seconds || 0), 0);
  html += "<h3>Throughput</h3>" +
    '<div class="stat"><span class="label">Total fetched (visible window)</span>' +
    '<span class="value">' + totalFetched.toLocaleString() + "</span></div>" +
    '<div class="stat"><span class="label">Total scrape time</span>' +
    '<span class="value">' + Math.round(totalScrapeS) + " s</span></div>" +
    '<div class="stat"><span class="label">Total rebuild time</span>' +
    '<span class="value">' + Math.round(totalRebuildS) + " s</span></div>" +
    '<div class="stat"><span class="label">Avg added per run</span>' +
    '<span class="value">' + (totalAdded / runs.length).toFixed(1) + "</span></div>";

  openModal(html);
}

async function showTrajectory() {
  let data;
  try {
    data = await api("/api/trajectory?months=12");
  } catch (e) {
    setMessage("Trajectory unavailable: " + e.message);
    return;
  }
  const months = data.months || [];
  // Find peak for normalized bar widths.
  const peak = Math.max(
    1,
    ...months.map((m) => Math.max(m.documents, m.new_entities, m.typed_relations))
  );
  const rowHtml = (m) => {
    const mini = (label, value) =>
      '<div class="td td-mini-bar"><span class="bar-track"><span class="bar-fill" style="width:' +
      Math.min(100, Math.round((100 * value) / peak)) + '%"></span></span>' +
      '<span class="val">' + value + "</span></div>";
    return (
      '<div class="traj-row">' +
      '<div class="td month">' + escapeHtml(m.month) + "</div>" +
      mini("docs", m.documents) +
      mini("new entities", m.new_entities) +
      mini("typed rels", m.typed_relations) +
      "</div>"
    );
  };
  // Most-recent first reads more naturally for a returning user.
  const ordered = months.slice().reverse();
  // Collect entity-type totals across the window so the legend is faithful
  // to what shows up.
  const typeTotals = {};
  for (const m of months) {
    for (const [type, count] of Object.entries(m.entity_type_counts || {})) {
      typeTotals[type] = (typeTotals[type] || 0) + count;
    }
  }
  const typeChips = Object.entries(typeTotals)
    .sort((a, b) => b[1] - a[1])
    .map(
      ([type, count]) =>
        '<span class="legend-chip"><span class="dot" style="background:' +
        (TYPE_COLORS[type] || DEFAULT_COLOR) + '"></span>' +
        escapeHtml(type) + " — " + count + " new</span>"
    )
    .join("");
  let html =
    '<h2 class="modal-title">Trajectory</h2>' +
    '<p>The last 12 months of the landscape at a glance — how much was ' +
    "happening, what was new, and where stated relationships landed.</p>" +
    '<div class="traj-table">' +
    '<div class="th">month</div>' +
    '<div class="th">documents</div>' +
    '<div class="th">new entities</div>' +
    '<div class="th">typed rels</div>' +
    ordered.map(rowHtml).join("") +
    "</div>" +
    (typeChips ? '<div class="traj-types"><h3>New entities by type</h3>' +
      typeChips + "</div>" : "");
  openModal(html);
}

// ---- Claude-powered syntheses (cache-first) --------------------------------
//
// Both `Today's spotlight` (the hype read) and the briefing's analyst
// narrative are now served from a daily-generated sidecar snapshot the
// pipeline writes to snapshots/syntheses/YYYY-MM-DD.json. The server
// returns the cached text + a freshness signal, so the modal can render
// instantly for every visitor (no API key required to read). When the
// server reports `can_refresh: true` (an ANTHROPIC_API_KEY is set on
// the server) a Refresh button is shown that hits `?refresh=1` to
// regenerate today's snapshot in place.

// Human-readable "N minutes/hours/days ago" from an age in seconds.
function ageString(seconds) {
  if (seconds == null) return "unknown age";
  if (seconds < 90) return "just now";
  if (seconds < 3600) return Math.round(seconds / 60) + " min ago";
  if (seconds < 86400) return Math.round(seconds / 3600) + " h ago";
  return Math.round(seconds / 86400) + " d ago";
}

// Render the metadata footer + Refresh button under a synthesis section.
// Shared by the hype modal and the briefing narrative box. `r` is the
// cache response from /api/hype or /api/briefing/narrative.
function synthesisFooterHtml(r, options) {
  options = options || {};
  const docsLabel = options.docsLabel || "documents";
  const parts = [];
  if (r.generated_at) {
    parts.push("Generated " + ageString(r.age_seconds));
  }
  if (r.documents_used) {
    parts.push("from " + r.documents_used + " " + docsLabel);
  }
  if (r.window_days) {
    parts.push(r.window_days + "-day window");
  }
  const footer = parts.length
    ? '<p class="mini-spark-note synthesis-meta">' +
      escapeHtml(parts.join(" · ")) + "</p>"
    : "";
  const staleNote = r.is_stale
    ? '<p class="narrative-note synthesis-stale">' +
      "Cached snapshot is past its freshness window — consider refreshing." +
      "</p>"
    : "";
  const refreshBtn = r.can_refresh
    ? '<button class="ghost synthesis-refresh" data-target="' +
      escapeHtml(options.refreshTarget || "") + '">Refresh</button>'
    : "";
  return staleNote + footer + (refreshBtn ? '<div class="row">' + refreshBtn + "</div>" : "");
}

// Render the body (text / error / unavailable note) for a synthesis section.
function synthesisBodyHtml(r) {
  if (r.text) {
    return '<div class="narrative">' + escapeHtml(r.text) + "</div>";
  }
  if (r.error) {
    return '<p class="narrative-note">Synthesis failed: ' +
      escapeHtml(r.error) + "</p>";
  }
  if (r.message) {
    return '<p class="narrative-note">' + escapeHtml(r.message) + "</p>";
  }
  return '<p class="narrative-note">No synthesis available yet.</p>';
}

async function showHype(opts) {
  opts = opts || {};
  if (!opts.skipModalOpen) {
    openModal(
      '<h2 class="modal-title">Today\'s spotlight</h2>' +
      '<p class="mini-spark-note">A 30-second hype read of the most recent ' +
      "news, served from the daily synthesis snapshot.</p>" +
      '<div id="hype-box"><p class="muted">Loading…</p></div>'
    );
  } else {
    $("hype-box").innerHTML = '<p class="muted">Refreshing…</p>';
  }
  let r;
  const url = opts.refresh ? "/api/hype?refresh=1" : "/api/hype";
  try {
    r = await api(url);
  } catch (e) {
    $("hype-box").innerHTML =
      '<p class="narrative-note">Could not load: ' +
      escapeHtml(e.message) + "</p>";
    return;
  }
  $("hype-box").innerHTML =
    synthesisBodyHtml(r) +
    synthesisFooterHtml(r, {
      docsLabel: "recent documents",
      refreshTarget: "hype",
    });
  const btn = $("hype-box").querySelector(".synthesis-refresh");
  if (btn) {
    btn.addEventListener("click", () => showHype({ refresh: true, skipModalOpen: true }));
  }
}

// ---- surprise me (serendipity) ---------------------------------------------

async function surpriseMe() {
  // Pick a high-mention entity the user hasn't focused on yet. Pull the
  // overview's top_by_mentions as a pool; filter out anything we've
  // recorded as seen. Falls through to a random pick when the pool is
  // exhausted (which is a real "you've seen everything noteworthy" moment).
  let ov;
  try { ov = await api("/api/overview"); } catch (e) {
    setMessage("Couldn't reach the graph: " + e.message);
    return;
  }
  let pool = (ov.top_by_mentions || []).concat(
    (ov.most_connected || []).map((r) => ({ canonical_name: r[0], type: r[1] }))
  );
  // Filter to entities not yet seen.
  let candidates = pool.filter((n) => !lsHasSeen("ail_seen_entities", n.canonical_name));
  if (!candidates.length) {
    candidates = pool;
    setMessage("Refreshed — every notable entity has been visited at least once.");
  }
  const pick = candidates[Math.floor(Math.random() * candidates.length)];
  if (!pick) {
    setMessage("Nothing to surprise you with — the graph looks empty.");
    return;
  }
  lsAddSeen("ail_seen_entities", pick.canonical_name);
  loadGraph(Object.assign(readFilters(), { focus: pick.canonical_name }));
  setMessage("Surprise: " + pick.canonical_name + " (click for details)");
}

// ---- pulse strip (always-visible header) -----------------------------------

async function refreshPulse() {
  let p;
  try {
    p = await api("/api/pulse?days=7");
  } catch (e) { return; }
  if (!p) return;
  $("pulse").hidden = false;
  $("pulse-window").textContent = (p.new_entities_window_days || 7) + "d";
  $("pulse-new").textContent = Number(p.new_entities || 0).toLocaleString();
  if (p.top_spike) {
    $("pulse-spike").textContent = p.top_spike.name;
    $("pulse-spike-sub").textContent = "↑ " + p.top_spike.ratio + "×";
  } else {
    $("pulse-spike").textContent = "—";
    $("pulse-spike-sub").textContent = "no spikes";
  }
  const sbir = Number(p.sbir_total_amount || 0);
  $("pulse-sbir").textContent = sbir
    ? "$" + sbir.toLocaleString()
    : "—";
  // Card click handlers — each opens the most relevant view.
  $("pulse").querySelectorAll("[data-pulse]").forEach((card) => {
    if (card._wired) return;
    card._wired = true;
    card.addEventListener("click", () => {
      const which = card.dataset.pulse;
      if (which === "recent") showTrends();
      else if (which === "spike" && p.top_spike) {
        loadGraph(Object.assign(readFilters(), { focus: p.top_spike.name }));
      } else if (which === "sbir") showBriefing();
    });
  });
}

async function refreshSpikes() {
  try {
    const sp = await api("/api/spikes?limit=20");
    SPIKE_IDS = new Set((sp.spikes || []).map((s) => Number(s.id)));
    SPIKE_BY_ID = {};
    for (const s of (sp.spikes || [])) SPIKE_BY_ID[Number(s.id)] = s;
  } catch (e) { /* leave defaults */ }
}

// ---- welcome overlay (first-visit) -----------------------------------------

function showWelcome() {
  $("welcome").hidden = false;
}
function hideWelcome() {
  $("welcome").hidden = true;
  lsSet("ail_welcome_seen", "1");
}

// ---- 3-step tutorial -------------------------------------------------------

const TUTORIAL_STEPS = [
  "<b>Click any node</b> to see its connections, the evidence behind each " +
    "relationship, and the source articles.",
  "<b>Hover an edge</b> to see what the relationship means in plain " +
    "language. Blue arrows are stated relationships; grey lines are " +
    "co-occurrence.",
  "<b>Try a Story tour</b> in the sidebar or <b>Today's briefing</b> in the " +
    "header for a guided read of what's happening right now.",
];
let tutorialStep = 0;
function startTutorial() {
  tutorialStep = 0;
  showTutorialStep();
}
function showTutorialStep() {
  const step = TUTORIAL_STEPS[tutorialStep];
  if (!step) {
    endTutorial();
    return;
  }
  $("tutorial").hidden = false;
  $("tutorial-step").innerHTML =
    "Step " + (tutorialStep + 1) + " of " + TUTORIAL_STEPS.length +
    ": " + step;
  const nextBtn = $("tutorial-next");
  nextBtn.textContent =
    tutorialStep === TUTORIAL_STEPS.length - 1 ? "Got it" : "Next";
}
function nextTutorialStep() {
  tutorialStep += 1;
  showTutorialStep();
}
function endTutorial() {
  $("tutorial").hidden = true;
  lsSet("ail_seen_tutorial", "1");
}

// ---- data loading ----------------------------------------------------------

function readFilters() {
  // Sliders are 0..100 percent in the UI but the API takes 0..1 floats.
  const conf = $("f-min-conf") ? Number($("f-min-conf").value) / 100 : 0;
  const strength = $("f-min-strength") ? Number($("f-min-strength").value) / 100 : 0;
  return {
    type: $("f-type").value,
    src_type: $("f-src-type") ? $("f-src-type").value : "",
    dst_type: $("f-dst-type") ? $("f-dst-type").value : "",
    min_mentions: $("f-min-mentions").value,
    min_weight: $("f-min-weight").value,
    max_nodes: $("f-max-nodes").value,
    relations_only: $("f-relations-only").checked ? "1" : "",
    min_confidence: conf ? conf.toFixed(2) : "",
    min_strength: strength ? strength.toFixed(2) : "",
  };
}

function wireSliderReadouts() {
  const conf = $("f-min-conf");
  const confOut = $("f-min-conf-out");
  if (conf && confOut) {
    const update = () => { confOut.textContent = conf.value + "%"; };
    conf.addEventListener("input", update);
    update();
  }
  const str = $("f-min-strength");
  const strOut = $("f-min-strength-out");
  if (str && strOut) {
    const update = () => {
      strOut.textContent = (Number(str.value) / 100).toFixed(2);
    };
    str.addEventListener("input", update);
    update();
  }
}

async function loadGraph(params, options) {
  currentParams = params || {};
  options = options || {};
  // A user-initiated load (apply/reset/focus) resets the zoom-density
  // ladder so we start lean again; a load triggered by the semantic-zoom
  // densifier explicitly preserves it.
  if (!options.preserveDensity) currentDensityIndex = 0;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(currentParams)) {
    if (value !== "" && value != null) query.set(key, value);
  }
  let graph;
  try {
    graph = await api("/api/graph?" + query.toString());
  } catch (e) {
    setMessage("Could not load graph: " + e.message);
    return;
  }
  renderGraph(graph);
  // Don't clobber an open detail panel when the load was a passive
  // densification — keep the user's selection visible.
  if (!options.preserveDensity) $("detail-panel").hidden = true;
  const shown = graph.nodes.length;
  $("stats").textContent =
    "showing " + shown + " of " + totalNodes + " entities · " +
    graph.edges.length + " relationships" +
    (currentParams.focus ? ' · focus: "' + currentParams.focus + '"' : "");
  writeUrlState(currentParams);
}

// ---- URL state -------------------------------------------------------------
//
// The current view is encoded in the URL hash so links are shareable and the
// back button restores prior state. We re-use the same key names the API
// takes so the hash reads like a query string.

function writeUrlState(params) {
  const out = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== "" && value != null && value !== "0" && value !== false) {
      out.set(key, value);
    }
  }
  const hash = out.toString();
  // Use replaceState rather than location.hash to avoid scrolling and to keep
  // history clean — each filter tweak shouldn't add a back-stack entry.
  const target = hash ? "#" + hash : window.location.pathname + window.location.search;
  if (window.location.hash.slice(1) !== hash) {
    history.replaceState(null, "", target);
  }
}

function readUrlState() {
  const hash = window.location.hash.slice(1);
  if (!hash) return {};
  const out = {};
  for (const [key, value] of new URLSearchParams(hash)) out[key] = value;
  return out;
}

function applyUrlStateToControls(state) {
  if (state.type != null) $("f-type").value = state.type;
  if (state.src_type != null && $("f-src-type")) $("f-src-type").value = state.src_type;
  if (state.dst_type != null && $("f-dst-type")) $("f-dst-type").value = state.dst_type;
  if (state.min_mentions != null) $("f-min-mentions").value = state.min_mentions;
  if (state.min_weight != null) $("f-min-weight").value = state.min_weight;
  if (state.max_nodes != null) $("f-max-nodes").value = state.max_nodes;
  if (state.relations_only != null) {
    $("f-relations-only").checked = state.relations_only === "1";
  }
  if (state.min_confidence != null && $("f-min-conf")) {
    $("f-min-conf").value = Math.round(Number(state.min_confidence) * 100);
  }
  if (state.min_strength != null && $("f-min-strength")) {
    $("f-min-strength").value = Math.round(Number(state.min_strength) * 100);
  }
  if (state.focus) {
    $("search").value = state.focus;
  }
  // Slider read-outs need to recompute.
  if ($("f-min-conf")) {
    $("f-min-conf-out").textContent = $("f-min-conf").value + "%";
  }
  if ($("f-min-strength")) {
    $("f-min-strength-out").textContent =
      (Number($("f-min-strength").value) / 100).toFixed(2);
  }
  // If the user has set a non-trivial type pair, reveal the pair-filter row.
  const pair = document.querySelector("details.pair-filter");
  if (pair && (state.src_type || state.dst_type)) pair.open = true;
}

// ---- story tours ----------------------------------------------------------

let TOURS_CACHE = null;

async function refreshTours() {
  let data;
  try {
    data = await api("/api/tours");
  } catch (e) {
    $("tour-list").innerHTML = '<div class="muted">unavailable</div>';
    return;
  }
  TOURS_CACHE = data.tours || [];
  $("tour-list").innerHTML = TOURS_CACHE
    .map(
      (t) =>
        '<button class="tour-card" data-tour="' + escapeHtml(t.id) + '">' +
        '<div class="tour-title">' + escapeHtml(t.title) + "</div>" +
        '<div class="tour-tagline">' + escapeHtml(t.tagline) + "</div>" +
        "</button>"
    )
    .join("");
  $("tour-list").querySelectorAll("[data-tour]").forEach((btn) =>
    btn.addEventListener("click", () => openTour(btn.dataset.tour))
  );
}

function openTour(tourId) {
  const tour = (TOURS_CACHE || []).find((t) => t.id === tourId);
  if (!tour) return;
  let html =
    '<h2 class="modal-title">' + escapeHtml(tour.title) + "</h2>" +
    '<p class="tour-tagline-modal">' + escapeHtml(tour.tagline) + "</p>" +
    '<ol class="tour-stops">' +
    tour.stops
      .map(
        (s, i) =>
          '<li><div class="tour-step-head" data-focus="' +
          escapeHtml(s.entity) + '"><span class="tour-num">' + (i + 1) +
          '</span><span class="tour-entity">' + escapeHtml(s.entity) +
          "</span></div>" +
          '<div class="tour-card-body">' + escapeHtml(s.card) +
          "</div></li>"
      )
      .join("") +
    "</ol>";
  openModal(html);
  wireFocusClicks();
}

// ---- what-changed-this-week sidebar ----------------------------------------

async function refreshRecent() {
  // Anchor "what's changed" to the user's last visit if we have one, falling
  // back to a 7-day window for a first visit. Updating the stamp here means
  // each session sees only deltas since the last one.
  let since = null;
  try { since = localStorage.getItem("ail_last_visit"); } catch (e) {}
  const params = new URLSearchParams();
  if (since) {
    params.set("since", since.slice(0, 10));
  } else {
    params.set("days", "7");
  }
  let data;
  try {
    data = await api("/api/recent?" + params.toString());
  } catch (e) {
    $("recent").innerHTML = '<div class="muted">unavailable</div>';
    return;
  }
  $("recent-since").textContent = "since " + data.since;
  const newEnt = data.new_entities || [];
  const activeEnt = data.active_entities || [];
  const docs = data.documents || [];
  let html = "";
  if (!newEnt.length && !docs.length && !activeEnt.length) {
    html = '<div class="muted">No changes in this window.</div>';
  } else {
    const small = (xs, total, label) =>
      total
        ? '<div class="recent-section"><div class="recent-head">' +
          label + " (" + total + ")</div><ul class=\"click-list compact\">" +
          xs
            .slice(0, 6)
            .map(
              (n) =>
                '<li data-focus="' + escapeHtml(n.name) +
                '"><span>' + escapeHtml(n.name) + "</span><em>" +
                escapeHtml(n.type) + "</em></li>"
            )
            .join("") +
          "</ul></div>"
        : "";
    html += small(newEnt, data.new_entity_total, "New entities");
    html += small(activeEnt, data.active_entity_total, "Active entities");
    if (docs.length) {
      html +=
        '<div class="recent-section"><div class="recent-head">' +
        "New articles (" + data.document_total + ")</div>" +
        '<ul class="articles compact">' +
        docs
          .slice(0, 5)
          .map(
            (d) =>
              '<li><a href="' + encodeURI(d.url) +
              '" target="_blank" rel="noopener noreferrer">' +
              escapeHtml(d.title || d.url) + "</a>" +
              '<div class="src">' + escapeHtml(d.source || "") +
              (d.date ? " · " + escapeHtml(d.date) : "") +
              "</div></li>"
          )
          .join("") +
        "</ul></div>";
    }
  }
  $("recent").innerHTML = html;
  $("recent")
    .querySelectorAll("[data-focus]")
    .forEach((el) =>
      el.addEventListener("click", () =>
        loadGraph(Object.assign(readFilters(), { focus: el.dataset.focus }))
      )
    );
  // Stamp the new visit time only after the panel has rendered, so a quick
  // page reload still shows the previous window's deltas.
  try {
    localStorage.setItem("ail_last_visit", new Date().toISOString());
  } catch (e) { /* ignore */ }
}

function renderOverviewPanel(ov) {
  const f = ov.funnel;
  const s = ov.scrape;
  const recency =
    s.hours_since == null
      ? "no data"
      : s.within_24h
      ? "within 24h"
      : "over 24h ago";
  const stat = (label, value) =>
    '<div class="stat"><span class="label">' + label +
    '</span><span class="value">' + value + "</span></div>";
  $("overview").innerHTML =
    stat("Corpus documents", f.documents.toLocaleString()) +
    stat("Graph entities", f.nodes.toLocaleString()) +
    stat("Relationships", f.edges.toLocaleString()) +
    stat("Last scrape", recency) +
    stat(
      "Single-mention",
      Math.round(ov.quality.singleton_pct) + "% of nodes"
    );
}

async function refreshMeta() {
  try {
    const overview = await api("/api/overview");
    totalNodes = overview.funnel.nodes;
    renderOverviewPanel(overview);
  } catch (e) {
    $("overview").textContent = "unavailable";
  }
  try {
    const data = await api("/api/types");
    const typeSelects = [
      $("f-type"),
      $("f-src-type"),
      $("f-dst-type"),
    ].filter(Boolean);
    const placeholderText = [
      "all types",
      "any source type",
      "any target type",
    ];
    typeSelects.forEach((sel, i) => {
      sel.innerHTML =
        '<option value="">' + placeholderText[i] + "</option>";
    });
    const legend = $("legend");
    legend.innerHTML = "";
    for (const row of data.types) {
      typeSelects.forEach((sel) => {
        const opt = document.createElement("option");
        opt.value = row.type;
        opt.textContent = row.type + " (" + row.count + ")";
        sel.appendChild(opt);
      });

      const li = document.createElement("li");
      li.innerHTML =
        '<span class="dot" style="background:' +
        (TYPE_COLORS[row.type] || DEFAULT_COLOR) + '"></span>' +
        escapeHtml(row.type) +
        '<span class="count">' + row.count + "</span>";
      legend.appendChild(li);
    }
  } catch (e) {
    /* ignore */
  }
}

// ---- search ----------------------------------------------------------------

let searchTimer = null;
function onSearchInput() {
  clearTimeout(searchTimer);
  const q = $("search").value.trim();
  if (q.length < 2) {
    $("search-results").innerHTML = "";
    return;
  }
  const since = $("search-since") ? $("search-since").value : "";
  const relation = $("search-relation") ? $("search-relation").value : "";
  const params = new URLSearchParams();
  params.set("q", q);
  if (since) params.set("since", since);
  if (relation) params.set("relation", relation);
  searchTimer = setTimeout(async () => {
    let data;
    try {
      data = await api("/api/search?" + params.toString());
    } catch (e) {
      return;
    }
    const ents = data.entities || [];
    const docs = data.documents || [];
    let html = "";
    if (ents.length) {
      html +=
        '<li class="sr-head">Entities</li>' +
        ents
          .map(
            (r) =>
              '<li data-name="' + escapeHtml(r.label) + '"><span>' +
              escapeHtml(r.label) + "</span><em>" + r.mentions + "</em></li>"
          )
          .join("");
    }
    if (docs.length) {
      html +=
        '<li class="sr-head">Articles</li>' +
        docs
          .map(
            (d) =>
              '<li class="sr-doc"><a href="' + encodeURI(d.url) +
              '" target="_blank" rel="noopener noreferrer">' +
              escapeHtml(d.title || d.url) + "</a></li>"
          )
          .join("");
    }
    $("search-results").innerHTML =
      html || '<li class="sr-head">No matches</li>';
    $("search-results")
      .querySelectorAll("li[data-name]")
      .forEach((li) =>
        li.addEventListener("click", () => {
          $("search").value = li.dataset.name;
          $("search-results").innerHTML = "";
          loadGraph(Object.assign(readFilters(), { focus: li.dataset.name }));
        })
      );
  }, 220);
}

// ---- wiring ----------------------------------------------------------------

function init() {
  $("search").addEventListener("input", onSearchInput);
  if ($("search-since")) $("search-since").addEventListener("change", onSearchInput);
  if ($("search-relation")) $("search-relation").addEventListener("change", onSearchInput);
  $("apply").addEventListener("click", () => loadGraph(readFilters()));
  $("open-dashboard").addEventListener("click", showDashboard);
  $("open-briefing").addEventListener("click", () => showBriefing());
  $("open-trends").addEventListener("click", showTrends);
  $("open-guide").addEventListener("click", showGuide);
  if ($("open-capabilities")) {
    $("open-capabilities").addEventListener("click", showCapabilities);
  }
  if ($("open-trajectory")) {
    $("open-trajectory").addEventListener("click", showTrajectory);
  }
  if ($("open-pipeline")) {
    $("open-pipeline").addEventListener("click", showPipeline);
  }
  if ($("surprise-me")) {
    $("surprise-me").addEventListener("click", surpriseMe);
  }
  if ($("open-hype")) {
    $("open-hype").addEventListener("click", showHype);
  }
  $("find-path").addEventListener("click", findPath);
  const starterGrid = $("starter-grid");
  for (const key of Object.keys(STARTER_VIEWS)) {
    const button = document.createElement("button");
    button.textContent = STARTER_VIEWS[key].label;
    button.addEventListener("click", () => applyStarter(key));
    starterGrid.appendChild(button);
  }
  $("modal-close").addEventListener("click", closeModal);
  $("modal-backdrop").addEventListener("click", closeModal);
  $("article-close").addEventListener("click", closeArticle);
  // Close X on the entity detail panel. Same effect as clicking on the
  // empty graph area (clearSelection drops the highlight + hides the
  // panel), but explicit so the affordance is discoverable.
  const detailClose = document.querySelector('#detail-close');
  if (detailClose) {
    detailClose.addEventListener('click', clearSelection);
  }
  // Delegated click on any "article-open" link → drawer instead of new tab.
  document.addEventListener("click", (e) => {
    const link = e.target.closest && e.target.closest(".article-open");
    if (link && link.dataset.hash) {
      e.preventDefault();
      openArticle(link.dataset.hash);
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("article-drawer").hidden) closeArticle();
      else closeModal();
    }
  });
  window.addEventListener("resize", () => {
    if (cy) {
      cy.resize();
      cy.fit(undefined, 45);
    }
  });
  $("reset").addEventListener("click", resetView);
  $("topbar-reset").addEventListener("click", resetView);
  // Welcome overlay buttons (first-visit choice card). Each picks the
  // path the user committed to — anything from a tour to "just show me
  // the graph." Dismissed-via-X also closes without a follow-up.
  if ($("welcome-close")) {
    $("welcome-close").addEventListener("click", hideWelcome);
  }
  if ($("welcome-backdrop")) {
    $("welcome-backdrop").addEventListener("click", hideWelcome);
  }
  document.querySelectorAll(".welcome-choice").forEach((btn) =>
    btn.addEventListener("click", () => {
      const go = btn.dataset.go;
      hideWelcome();
      if (go === "tour") {
        // Open the first tour, the most accessible introductory thread.
        const t = (TOURS_CACHE && TOURS_CACHE[0]) || null;
        if (t) openTour(t.id);
        else setMessage("Tours still loading — try Story tours in the sidebar.");
      } else if (go === "capabilities") {
        showCapabilities();
      } else if (go === "briefing") {
        showBriefing();
      } else if (go === "hype") {
        showHype();
      } else if (go === "explore") {
        // Trigger the 3-step tutorial so a fresh visitor isn't lost on
        // the bare graph. The tutorial is dismissable.
        if (!lsGet("ail_seen_tutorial")) startTutorial();
      }
    })
  );
  // Tutorial controls.
  if ($("tutorial-next")) {
    $("tutorial-next").addEventListener("click", nextTutorialStep);
  }
  if ($("tutorial-skip")) {
    $("tutorial-skip").addEventListener("click", endTutorial);
  }
  wireSliderReadouts();
  refreshTours();
  refreshRecent();
  // The pulse strip + spike set are global signals every list reads from,
  // so they're fetched once on init alongside the overview.
  refreshSpikes();
  refreshPulse();
  refreshMeta().then(() => {
    // Hydrate from URL hash if present (shareable / bookmarkable views).
    const initial = readUrlState();
    if (Object.keys(initial).length) {
      applyUrlStateToControls(initial);
      loadGraph(Object.assign(readFilters(), initial));
    } else {
      loadGraph(readFilters());
    }
  });
  window.addEventListener("hashchange", () => {
    const state = readUrlState();
    if (Object.keys(state).length) {
      applyUrlStateToControls(state);
      loadGraph(Object.assign(readFilters(), state));
    }
  });
  // First-visit landing: the welcome card offers four on-ramps (tour,
  // capabilities, briefing, dive into graph) rather than throwing a
  // briefing wall at a brand-new visitor. Returning visitors who have
  // already dismissed it don't see it again. The older guide auto-open
  // is preserved for users who saw the v2 briefing flow but never got
  // the guide.
  if (!lsGet("ail_welcome_seen")) {
    showWelcome();
  } else if (!lsGet("ail_seen_guide")) {
    showGuide();
    lsSet("ail_seen_guide", "1");
  }
}

document.addEventListener("DOMContentLoaded", init);
