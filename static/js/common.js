// Shared helpers. Everything from uploaded projects is inserted with textContent only (never innerHTML).
window.AP = (function () {
  "use strict";
  const $ = (sel, root) => (root || document).querySelector(sel);

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  async function api(path, options) {
    const opts = Object.assign({}, options);
    if (opts.json !== undefined) {
      opts.method = opts.method || "POST";
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(opts.json);
    }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (_) { /* non-JSON error body */ }
    if (!res.ok) throw new Error((data && data.error) || "Request failed (" + res.status + ")");
    return data;
  }

  const SEV_COLOR = { Critical: "#F87171", High: "#FB923C", Medium: "#FBBF24", Low: "#94A3B8" };
  const badge = (text, kind) => el("span", { class: "badge " + (kind || ""), text });
  const sev = (s) => el("span", { class: "sev " + s, text: s });
  const confBadge = (c) => badge(c === "runtime" ? "Runtime-confirmed" : "Static (not confirmed)", c);
  const tile = (n, label, kind) => el("div", { class: "tile " + (kind || "") }, el("div", { class: "n", text: String(n) }), el("div", { class: "l", text: label }));

  function table(headers, rows) {
    return el("div", { class: "table-wrap" }, el("table", { class: "t" },
      el("thead", {}, el("tr", {}, headers.map((h) => el("th", { text: h })))), el("tbody", {}, rows)));
  }
  function bars(rows, total) {
    return rows.map((r) => el("div", { class: "bar-row" }, el("span", { text: r.label }),
      el("div", { class: "bar" }, el("span", { style: "width:" + (total ? Math.round(100 * r.value / total) : 0) + "%;background:" + r.color })),
      el("span", { class: "muted", text: String(r.value) })));
  }
  function when(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString();
  }
  function codePanel(title, snippet, note) {
    const hit = snippet.line;
    return el("div", { class: "panel" },
      el("div", { class: "ph" }, el("span", { text: title }), note ? el("span", { class: "muted", text: note }) : null),
      el("div", { class: "code" }, snippet.lines.map((text, i) => {
        const n = snippet.start + i;
        return el("div", { class: "ln" + (n === hit ? " hit" : "") }, el("span", { text: String(n) }), el("span", { text }));
      })));
  }

  async function health() {
    try {
      const h = await api("/api/health");
      const chip = $("#storage-chip");
      if (chip) chip.textContent = "storage: " + h.storage.backend + (h.storage.fallback_reason ? " (fallback)" : "");
      return h;
    } catch (_) { const chip = $("#storage-chip"); if (chip) chip.textContent = "storage: unavailable"; return null; }
  }
  health();
  return { $, el, api, badge, sev, confBadge, tile, table, bars, when, codePanel, SEV_COLOR, health };
})();
