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

let cy = null;
let currentParams = {};
let totalNodes = 0;

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
    elements.push({
      data: {
        id: "e" + e.id,
        source: String(e.source),
        target: String(e.target),
        w: Math.min(9, 1 + e.weight / 3),
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
      color: "#dfe3e8",
      "font-size": 11,
      "text-outline-color": "#11151c",
      "text-outline-width": 2,
      "text-valign": "bottom",
      "text-margin-y": 3,
    },
  },
  {
    selector: "edge",
    style: {
      width: "data(w)",
      "line-color": "#39455a",
      "curve-style": "haystack",
      opacity: 0.5,
    },
  },
  { selector: "node:selected", style: { "border-width": 3, "border-color": "#fff" } },
  { selector: ".dim", style: { opacity: 0.1, "text-opacity": 0.1 } },
];

function renderGraph(graph) {
  if (cy) cy.destroy();
  cy = cytoscape({
    container: $("cy"),
    elements: toElements(graph),
    style: CY_STYLE,
    layout: {
      name: "cose",
      animate: false,
      fit: true,
      padding: 45,
      nodeRepulsion: 24000,
      idealEdgeLength: 95,
      gravity: 0.3,
      componentSpacing: 130,
    },
    minZoom: 0.08,
    maxZoom: 3.5,
    wheelSensitivity: 0.25,
  });
  cy.on("tap", "node", (evt) => selectNode(evt.target.id()));
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
  renderDetail(data);
  highlightNeighborhood(id);
}

function renderDetail(data) {
  const n = data.node;
  const color = TYPE_COLORS[n.type] || DEFAULT_COLOR;
  const neighbors = data.neighbors.slice(0, 14);
  $("detail").innerHTML =
    '<div class="entity-name">' + escapeHtml(n.label) + "</div>" +
    '<div class="entity-meta">' +
    '<span class="badge" style="background:' + color + '">' + escapeHtml(n.type) +
    "</span>" + n.mentions + " mentions · " + n.documents + " documents</div>" +
    "<h3>Top connections (" + data.neighbors.length + ")</h3>" +
    '<ul class="neighbors">' +
    neighbors
      .map(
        (x) =>
          '<li data-id="' + x.id + '"><span>' + escapeHtml(x.label) +
          "</span><em>" + x.weight + "</em></li>"
      )
      .join("") +
    "</ul>" +
    '<div class="row"><button data-act="focus">Focus here</button>' +
    '<button data-act="ignore" class="ghost">Ignore</button></div>' +
    '<div class="row"><button data-act="merge" class="ghost">Merge into…</button></div>';
  $("detail-panel").hidden = false;

  $("detail").querySelectorAll(".neighbors li").forEach((li) =>
    li.addEventListener("click", () => selectNode(li.dataset.id))
  );
  $("detail").querySelector('[data-act="focus"]').addEventListener("click", () =>
    loadGraph({ focus: n.label })
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

// ---- data loading ----------------------------------------------------------

function readFilters() {
  return {
    type: $("f-type").value,
    min_mentions: $("f-min-mentions").value,
    min_weight: $("f-min-weight").value,
    max_nodes: $("f-max-nodes").value,
  };
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
}

async function refreshMeta() {
  try {
    const overview = await api("/api/overview");
    totalNodes = overview.funnel.nodes;
  } catch (e) {
    /* leave totalNodes as-is */
  }
  try {
    const data = await api("/api/types");
    const typeSelect = $("f-type");
    typeSelect.innerHTML = '<option value="">all types</option>';
    const legend = $("legend");
    legend.innerHTML = "";
    for (const row of data.types) {
      const opt = document.createElement("option");
      opt.value = row.type;
      opt.textContent = row.type + " (" + row.count + ")";
      typeSelect.appendChild(opt);

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
  searchTimer = setTimeout(async () => {
    let data;
    try {
      data = await api("/api/search?q=" + encodeURIComponent(q));
    } catch (e) {
      return;
    }
    $("search-results").innerHTML = data.results
      .map(
        (r) =>
          '<li data-name="' + escapeHtml(r.label) + '"><span>' +
          escapeHtml(r.label) + "</span><em>" + r.mentions + "</em></li>"
      )
      .join("");
    $("search-results").querySelectorAll("li").forEach((li) =>
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
  $("apply").addEventListener("click", () => loadGraph(readFilters()));
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
    $("f-min-mentions").value = "0";
    $("f-min-weight").value = "3";
    $("f-max-nodes").value = "70";
    loadGraph(readFilters());
  });
  refreshMeta().then(() => loadGraph(readFilters()));
}

document.addEventListener("DOMContentLoaded", init);
