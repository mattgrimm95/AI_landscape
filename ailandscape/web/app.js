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

function layoutOptions(name) {
  if (name === "fcose") {
    return {
      name: "fcose",
      quality: "proof",
      animate: false,
      randomize: true,
      fit: true,
      padding: 55,
      nodeRepulsion: 14000,
      idealEdgeLength: 90,      // connected nodes (a grouping) stay close
      edgeElasticity: 0.4,
      nodeSeparation: 150,      // push everything else well apart
      gravity: 0.18,
      gravityRange: 4.0,
      numIter: 2600,
      packComponents: true,
      tile: true,
    };
  }
  return {
    name: "cose",
    animate: false,
    fit: true,
    padding: 45,
    nodeRepulsion: 26000,
    idealEdgeLength: 95,
    gravity: 0.25,
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
    elements.push({
      data: {
        id: String(n.id),
        label: n.label,
        type: n.type,
        mentions: n.mentions,
        documents: n.documents,
        color: TYPE_COLORS[n.type] || DEFAULT_COLOR,
        size: 16 + 56 * Math.sqrt(n.mentions / maxMentions),
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
      width: "data(size)",
      height: "data(size)",
      label: "data(label)",
      color: "#eef2f7",
      // Prominent nodes get larger labels; small ones hide when zoomed out
      // so the default view stays uncluttered.
      "font-size": "mapData(size, 16, 72, 7, 19)",
      "min-zoomed-font-size": 11,
      "text-outline-color": "#11151c",
      "text-outline-width": 2.4,
      "text-valign": "bottom",
      "text-margin-y": 3,
    },
  },
  {
    selector: "edge",
    style: {
      width: "data(w)",
      "line-color": "#36425a",
      "curve-style": "bezier",
      opacity: 0.3,
    },
  },
  {
    // Typed semantic relationships stand out: bright, arrowed, labelled.
    selector: 'edge[relation != "co_occurs_with"]',
    style: {
      width: 2.6,
      "line-color": "#5e9bff",
      opacity: 0.95,
      "target-arrow-shape": "triangle",
      "target-arrow-color": "#5e9bff",
      "arrow-scale": 1.1,
      label: "data(relLabel)",
      "font-size": 9,
      "min-zoomed-font-size": 9,
      color: "#a8c8ff",
      "text-rotation": "autorotate",
      "text-background-color": "#11151c",
      "text-background-opacity": 0.78,
      "text-background-padding": 2,
    },
  },
  { selector: "node:selected", style: { "border-width": 3, "border-color": "#fff" } },
  { selector: ".dim", style: { opacity: 0.1, "text-opacity": 0.1 } },
];

function renderGraph(graph) {
  if (cy) cy.destroy();
  const build = (layoutName) =>
    cytoscape({
      container: $("cy"),
      elements: toElements(graph),
      style: CY_STYLE,
      layout: layoutOptions(layoutName),
      minZoom: 0.08,
      maxZoom: 3.5,
      wheelSensitivity: 0.25,
    });
  try {
    cy = build(LAYOUT_NAME);
  } catch (e) {
    cy = build("cose"); // fall back if the fcose extension is unavailable
  }
  cy.on("tap", "node", (evt) => selectNode(evt.target.id()));
  cy.on("tap", "edge", (evt) => {
    const ev = evt.target.data("evidence");
    if (ev) setMessage("“" + ev + "”");
  });
  // Hover an edge to see what its relation means, in plain language. This
  // lifts the glossary out of the Guide modal so a learner discovers it
  // mid-exploration without a context switch.
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
  renderDetail(data, docs);
  highlightNeighborhood(id);
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

function renderDetail(data, docs) {
  const n = data.node;
  const color = TYPE_COLORS[n.type] || DEFAULT_COLOR;
  const neighbors = data.neighbors.slice(0, 14);
  $("detail").innerHTML =
    '<div class="entity-name">' + escapeHtml(n.label) + "</div>" +
    '<div class="entity-meta">' +
    '<span class="badge" style="background:' + color + '">' + escapeHtml(n.type) +
    "</span>" + n.mentions + " mentions · " + n.documents + " documents</div>" +
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
    articlesHtml(docs) +
    '<div class="row"><button data-act="focus">Focus here</button>' +
    '<button data-act="dossier" class="ghost">Dossier</button></div>' +
    '<div class="row"><button data-act="ignore" class="ghost">Ignore</button>' +
    '<button data-act="merge" class="ghost">Merge into…</button></div>';
  $("detail-panel").hidden = false;

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
  return (
    '<ul class="click-list">' +
    rows
      .map(
        (r) =>
          '<li data-focus="' + escapeHtml(r[0]) + '"><span>' +
          escapeHtml(r[0]) + "</span><em>" + escapeHtml(String(r[1])) +
          "</em></li>"
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
  let html =
    '<h2 class="modal-title">Trends over time</h2>' +
    "<h3>Document volume by month</h3>" +
    (t.document_volume.length
      ? barChart(t.document_volume.map((v) => [v.month, v.count]))
      : "<p>no dated documents</p>") +
    "<h3>Newly appeared entities</h3>" +
    clickList(
      t.new_entities.map((n) => [n.name, "first seen " + n.first_seen])
    ) +
    "<h3>Most recently active entities</h3>" +
    clickList(
      t.recent_entities.map((n) => [n.name, "last seen " + n.last_seen])
    );
  openModal(html);
  wireFocusClicks();
}

function applyStarter(key) {
  const view = STARTER_VIEWS[key];
  if (!view) return;
  const p = view.params;
  $("f-type").value = p.type || "";
  $("f-relations-only").checked = !!p.relations_only;
  $("f-min-mentions").value = "0";
  $("f-min-weight").value = "8";
  $("f-max-nodes").value = "70";
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

async function showBriefing() {
  let b;
  try {
    b = await api("/api/briefing");
  } catch (e) {
    setMessage("Briefing unavailable: " + e.message);
    return;
  }
  const t = b.totals;
  let html =
    '<h2 class="modal-title">Landscape briefing</h2>' +
    '<div class="dossier-dates">' + b.window_days + "-day window · " +
    t.documents + " documents · " + t.entities + " entities · " +
    t.typed_relations + " typed relationships</div>";
  if (b.sbir_funding && b.sbir_funding.awards) {
    html +=
      '<div class="dossier-dates">SBIR/STTR funding: ' +
      b.sbir_funding.awards + " AI-related awards · $" +
      Number(b.sbir_funding.total_amount).toLocaleString() + " total</div>";
  }
  html +=
    '<div id="narrative-box"><button id="gen-narrative" class="ghost">' +
    "Generate analyst narrative</button></div>";
  if (b.trending_topics.length) {
    html += "<h3>Trending AI topics</h3>" +
      barChart(b.trending_topics.map((c) => [c.name, c.mentions]));
  }
  html += "<h3>Most active entities</h3>" +
    clickList(b.top_entities.map((n) => [n.name, n.mentions + " mentions"]));
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
  $("gen-narrative").addEventListener("click", async () => {
    const btn = $("gen-narrative");
    btn.textContent = "Generating…";
    btn.disabled = true;
    let r;
    try {
      r = await api("/api/briefing/narrative");
    } catch (e) {
      $("narrative-box").innerHTML =
        '<p class="narrative-note">Narrative unavailable: ' +
        escapeHtml(e.message) + "</p>";
      return;
    }
    if (!r.available) {
      $("narrative-box").innerHTML =
        '<p class="narrative-note">' + escapeHtml(r.message) + "</p>";
    } else if (r.error) {
      $("narrative-box").innerHTML =
        '<p class="narrative-note">Synthesis failed: ' +
        escapeHtml(r.error) + "</p>";
    } else {
      $("narrative-box").innerHTML =
        '<div class="narrative">' + escapeHtml(r.narrative) + "</div>";
    }
  });
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

async function loadGraph(params) {
  currentParams = params || {};
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
  $("detail-panel").hidden = true;
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
  $("open-briefing").addEventListener("click", showBriefing);
  $("open-trends").addEventListener("click", showTrends);
  $("open-guide").addEventListener("click", showGuide);
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
  $("reset").addEventListener("click", () => {
    $("search").value = "";
    $("search-results").innerHTML = "";
    $("f-type").value = "";
    if ($("f-src-type")) $("f-src-type").value = "";
    if ($("f-dst-type")) $("f-dst-type").value = "";
    $("f-min-mentions").value = "0";
    $("f-min-weight").value = "8";
    $("f-max-nodes").value = "70";
    $("f-relations-only").checked = false;
    if ($("f-min-conf")) {
      $("f-min-conf").value = "0";
      $("f-min-conf-out").textContent = "0%";
    }
    if ($("f-min-strength")) {
      $("f-min-strength").value = "0";
      $("f-min-strength-out").textContent = "0.00";
    }
    loadGraph(readFilters());
  });
  wireSliderReadouts();
  refreshTours();
  refreshRecent();
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
  // First-visit landing: the briefing is the highest-density read-only
  // surface, so a newcomer sees the synthesis before the graph. The guide
  // continues to auto-open on a *fresh* first visit (newer flag) so users
  // who already saw the prior guide don't get it again.
  try {
    if (!localStorage.getItem("ail_seen_landing_v2")) {
      showBriefing();
      localStorage.setItem("ail_seen_landing_v2", "1");
    } else if (!localStorage.getItem("ail_seen_guide")) {
      showGuide();
      localStorage.setItem("ail_seen_guide", "1");
    }
  } catch (e) {
    /* localStorage unavailable — skip the one-time landing */
  }
}

document.addEventListener("DOMContentLoaded", init);
