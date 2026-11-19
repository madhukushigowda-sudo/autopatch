(function () {
  "use strict";
  const { $, el, api, sev, confBadge, badge, table } = AP;
  const params = new URLSearchParams(location.search);
  const state = { project_id: params.get("project_id") || "", severity: params.get("severity") || "", category: "", confirmation: "", q: "" };
  let projects = [], timer;

  const opts = (sel, first, items, value) => { sel.replaceChildren(el("option", { value: "", text: first }), ...items.map(([v, t]) => el("option", { value: v, text: t }))); sel.value = value || ""; };

  async function load() {
    const qs = new URLSearchParams(); for (const [k, v] of Object.entries(state)) if (v) qs.set(k, v);
    let d;
    try { d = await api("/api/issues?" + qs); } catch (e) { $("#list").replaceChildren(el("div", { class: "alert bad", text: e.message })); return; }
    const f = d.facets, any = Object.values(f.severity).reduce((a, b) => a + b, 0);
    $("#basis").textContent = any ? "Findings from the latest completed scan of each repository. Severity, confidence and evidence are reported separately; confidence is a heuristic, not a probability."
      : "No findings yet. Run a scan from the Scan Center to populate Bug Intelligence.";
    $("#tiles").replaceChildren(...["Critical", "High", "Medium", "Low"].map((s) => {
      const t = AP.tile(f.severity[s], s, s === "Critical" || s === "High" ? (f.severity[s] ? "bad" : "") : "");
      t.style.cursor = "pointer"; t.tabIndex = 0; t.setAttribute("role", "button"); t.setAttribute("aria-label", "Filter by " + s);
      const go = () => { state.severity = state.severity === s ? "" : s; sync(); load(); }; t.onclick = go; t.onkeydown = (e) => { if (e.key === "Enter") go(); };
      if (state.severity === s) t.style.borderColor = "var(--cyan)"; return t;
    }));
    opts($("#f-project"), "All repositories", projects.map((p) => [p._id, p.name]), state.project_id);
    opts($("#f-severity"), "All severities", Object.entries(f.severity).filter(([, n]) => n).map(([s, n]) => [s, s + " (" + n + ")"]), state.severity);
    opts($("#f-category"), "All categories", Object.entries(f.category).map(([c, o]) => [c, o.label + " (" + o.count + ")"]), state.category);
    opts($("#f-conf"), "Static + runtime", [["runtime", "Runtime-confirmed (" + f.confirmation.runtime + ")"], ["static", "Static, not confirmed (" + f.confirmation.static + ")"]], state.confirmation);
    const box = $("#list");
    if (!d.issues.length) { box.replaceChildren(el("div", { class: "empty" }, el("div", { class: "big", text: any ? "No bugs match these filters" : "Nothing to show yet" }), any ? "Clear a filter to see more." : el("a", { class: "btn primary", href: "/app/scan", text: "Go to Scan Center" }))); return; }
    box.replaceChildren(table(["ID", "Severity", "Bug", "Location", "Repository", "Evidence", "Repair"], d.issues.map((x) => el("tr", { class: "row sevrow " + x.severity, onclick: () => { location.href = "/app/findings/" + x._id; } },
      el("td", { class: "mono", text: x.finding_id }), el("td", {}, sev(x.severity), el("div", { class: "muted small", text: "conf. " + x.confidence })),
      el("td", { class: "wrap" }, el("strong", { text: x.title }), el("div", { class: "muted small", text: x.category_label })),
      el("td", { class: "mono wrap", text: x.file ? x.file + (x.line ? ":" + x.line : "") : "-" }), el("td", { class: "wrap", text: x.project_name || "" }), el("td", {}, confBadge(x.confirmation)),
      el("td", {}, x.latest_patch ? badge("patch " + x.latest_patch.status.replace("_", " "), x.latest_patch.status === "applied" ? "ok" : x.latest_patch.status === "proposed" ? "warn" : "") : el("a", { href: "/app/studio/" + x._id, text: "Repair →" }))))),
      el("p", { class: "small muted", text: d.total + " bug(s) shown." }));
  }
  function sync() { const u = new URL(location.href); for (const k of ["project_id", "severity"]) { if (state[k]) u.searchParams.set(k, state[k]); else u.searchParams.delete(k); } history.replaceState(null, "", u); }

  async function init() {
    try { projects = (await api("/api/projects")).projects; } catch (_) { /* filter just stays empty */ }
    for (const [id, key] of [["f-project", "project_id"], ["f-severity", "severity"], ["f-category", "category"], ["f-conf", "confirmation"]]) $("#" + id).addEventListener("change", (e) => { state[key] = e.target.value; sync(); load(); });
    $("#q").addEventListener("input", (e) => { clearTimeout(timer); timer = setTimeout(() => { state.q = e.target.value; load(); }, 250); });
    load();
  }
  init();
})();
