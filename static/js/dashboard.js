(function () {
  "use strict";
  const { $, el, api, when, bars, SEV_COLOR } = AP;
  const NS = "http://www.w3.org/2000/svg";
  const svg = (tag, attrs, ...kids) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v); n.append(...kids); return n; };

  const LABEL = { scan_started: "Scan started", scan_completed: "Scan completed", scan_failed: "Scan failed", proposed: "Repair proposed", accepted: "Repair approved and applied", rejected: "Repair rejected",
    rejected_by_validation: "Repair rejected by validation", rolled_back: "Repair rolled back", auto_reverted: "Repair auto-reverted (regression)", applying: "Applying repair", apply_failed: "Apply failed", stale: "Repair is out of date", warning: "Warning", recovered: "Recovered after restart" };
  const KIND = { scan_completed: "ok", accepted: "ok", scan_failed: "bad", auto_reverted: "bad", apply_failed: "bad", rejected_by_validation: "bad", rejected: "warn", rolled_back: "warn", stale: "warn" };

  function tile(label, value, sub, kind) {
    const n = el("div", { class: "n", text: "0" });
    const t = el("div", { class: "tile " + (kind || "") }, n, el("div", { class: "l", text: label }), sub ? el("div", { class: "s", text: sub }) : null);
    if (value === null) n.textContent = "—"; else APUI.countUp(n, value);
    return t;
  }

  function gauge(score) {
    const R = 62, C = 2 * Math.PI * R;
    const fg = svg("circle", { class: "fg", cx: 75, cy: 75, r: R, "stroke-dasharray": C, "stroke-dashoffset": C });
    const ring = el("div", { class: "ring", role: "img", "aria-label": score === null ? "No health score yet" : "Health score " + score + " out of 100" },
      svg("svg", { viewBox: "0 0 150 150" }, svg("defs", {}, svg("linearGradient", { id: "g1", x1: 0, y1: 0, x2: 1, y2: 1 }, svg("stop", { offset: "0", "stop-color": "#2563EB" }), svg("stop", { offset: ".6", "stop-color": "#20C5E8" }), svg("stop", { offset: "1", "stop-color": "#7865F5" }))),
        svg("circle", { class: "bg", cx: 75, cy: 75, r: R }), fg),
      el("div", { class: "val" }, el("div", {}, score === null ? el("span", { style: "font-size:1.05rem", text: "No data yet" }) : el("span", { text: String(score) }), score === null ? null : el("small", { text: "out of 100" }))));
    if (score !== null) requestAnimationFrame(() => requestAnimationFrame(() => fg.setAttribute("stroke-dashoffset", String(C * (1 - score / 100)))));
    return ring;
  }

  async function load() {
    const d = await api("/api/dashboard");
    const p = d.patches;
    $("#basis").textContent = d.repositories_analyzed ? d.basis + " Nothing here is estimated: every number is read from stored scan and repair records."
      : d.repositories ? "Repositories are uploaded but none has been scanned yet. Open the Scan Center to run a scan." : "No repositories yet. Upload a ZIP in Repositories to get started.";
    const unverified = Math.max(0, p.applied - p.verified_applied);
    $("#tiles").replaceChildren(
      tile("Repositories", d.repositories, d.repositories_analyzed + " scanned"), tile("Files scanned", d.files_scanned, "Python source files", "cyan"),
      tile("Bugs detected", d.bugs_detected, "latest scan of each repository", d.bugs_detected ? "bad" : ""),
      tile("Bugs fixed (verified)", p.verified_applied, "applied and verified against available tests", "good"),
      tile("Fixed, not verified", unverified, "applied without a passing relevant test"));
    const h = d.health;
    $("#health").replaceChildren(
      el("div", { class: "gauge" }, gauge(h.score),
        el("div", { style: "flex:1;min-width:220px" },
          h.projects.length ? el("div", {}, h.projects.map((x) => el("div", { class: "bar-row", style: "grid-template-columns:minmax(0,1fr) 120px 36px" }, el("a", { href: "/app/repositories/" + x.project_id, text: x.name || x.project_id }),
            el("div", { class: "bar" }, el("span", { style: "width:" + x.score + "%;color:#20C5E8;background:linear-gradient(90deg,#2563EB,#20C5E8)" })), el("span", { class: "muted", text: String(x.score) }))))
            : el("p", { class: "muted", text: "Run a scan to compute a score. Until then no value is shown." }))),
      el("p", { class: "formula", text: "How it is calculated: " + h.formula }));
    $("#sev").replaceChildren(...bars(["Critical", "High", "Medium", "Low"].map((s) => ({ label: s, value: d.by_severity[s] || 0, color: SEV_COLOR[s] })), d.bugs_detected));
    $("#conf").replaceChildren(...bars([{ label: "Runtime", value: d.by_confirmation.runtime || 0, color: "#F87171" }, { label: "Static", value: d.by_confirmation.static || 0, color: "#3B82F6" }], d.bugs_detected));
    const box = $("#activity");
    if (!d.recent_activity.length) box.replaceChildren(el("div", { class: "empty" }, el("div", { class: "big", text: "No activity yet" }), "Scans and repairs will appear here as they happen."));
    else box.replaceChildren(...d.recent_activity.map((a) => el("div", { class: "act" }, el("i", { class: KIND[a.event] || "" }),
      el("div", {}, el("strong", { text: LABEL[a.event] || a.event }), " · ", el("a", { href: "/app/repositories/" + a.project_id, text: a.project_name || "repository" }),
        a.finding_ref ? el("span", { class: "muted", text: " · " + a.finding_ref }) : null, a.detail ? el("div", { class: "muted small", text: a.detail }) : null),
      el("span", { class: "muted small", text: when(a.at) }))));
  }
  load().catch((e) => { $("#basis").textContent = "Could not load the dashboard: " + e.message; });
})();
