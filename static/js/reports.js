(function () {
  "use strict";
  const { $, el, api, badge, table, tile, sev, when } = AP;
  const sel = $("#repo"), box = $("#report"), dl = $("#dl");

  async function init() {
    const { projects } = await api("/api/projects");
    sel.replaceChildren(...(projects.length ? projects.map((p) => el("option", { value: p._id, text: p.name })) : [el("option", { value: "", text: "No repositories" })]));
    const want = new URLSearchParams(location.search).get("project"); if (want) sel.value = want;
    sel.addEventListener("change", show); $("#print").addEventListener("click", () => window.print());
    show();
  }

  async function show() {
    const id = sel.value;
    if (!id) { dl.removeAttribute("href"); box.replaceChildren(el("div", { class: "empty" }, el("div", { class: "big", text: "No repositories yet" }), el("a", { class: "btn primary", href: "/app/repositories", text: "Upload one" }))); return; }
    dl.href = "/api/reports/" + id + "?format=md"; dl.removeAttribute("aria-disabled");
    box.replaceChildren(el("div", { class: "skel" }));
    let r; try { r = await api("/api/reports/" + id); } catch (e) { box.replaceChildren(el("div", { class: "alert bad", text: e.message })); return; }
    const a = r.analysis, c = r.patch_counts, p = r.project;
    const out = [el("section", { class: "card" }, el("h2", { text: p.name }), el("p", { class: "muted small mono", text: "uploaded " + when(p.created_at) + " · " + p.python_file_count + " Python files · " + p.loc + " lines · " + p.test_file_count + " test files · sha256 " + String(p.source_sha256).slice(0, 16) }))];
    if (!a) { out.push(el("div", { class: "alert warn", text: "This repository has no completed scan yet, so there is no health summary or detection data." }), el("a", { class: "btn primary", href: "/app/scan?project=" + id, text: "Scan it now" })); box.replaceChildren(...out); return; }
    const f = a.findings || { total: 0, by_severity: {} }, pt = a.pytest || {};
    out.push(el("div", { class: "tiles" }, tile(a.health.score + " / 100", "Health score (heuristic)", "cyan"), tile(f.total, "Bugs detected", f.total ? "bad" : "good"), tile(c.total, "Repairs proposed"), tile(c.verified_applied, "Applied and verified", "good"), tile(c.applied - c.verified_applied, "Applied, not verified")),
      el("section", { class: "card" }, el("h2", { text: "Health summary" }), el("p", { class: "formula", style: "margin-top:0", text: r.health_formula }),
        el("p", { class: "small", text: "Penalty from findings: " + a.health.finding_penalty + ". Penalty from failing tests: " + a.health.test_penalty + ". Last scan finished " + when(a.finished_at) + "." })),
      el("div", { class: "grid2" },
        el("section", { class: "card" }, el("h2", { text: "Detection" }), el("div", { class: "chips" }, ["Critical", "High", "Medium", "Low"].map((s) => el("span", {}, sev(s), " ", String((f.by_severity || {})[s] || 0)))),
          el("p", { class: "small", text: "Tests: " + (pt.passed || 0) + " passed, " + (pt.failed || 0) + " failed, " + (pt.errors || 0) + " errors, " + (pt.skipped || 0) + " skipped (pytest " + (a.pytest_status || "n/a") + ")." }),
          el("p", { class: "small muted", text: "Ruff: " + (a.tools.ruff.status || "not run") + (a.tools.ruff.count != null ? " (" + a.tools.ruff.count + ")" : "") + " · Bandit: " + (a.tools.bandit.status || "not run") + (a.tools.bandit.count != null ? " (" + a.tools.bandit.count + ")" : "") }),
          a.integrity_original_unchanged === false ? el("div", { class: "alert bad", text: "The original upload changed during the scan. Do not trust this run." }) : el("p", { class: "small muted", text: "Original upload verified unchanged after the scan." })),
        el("section", { class: "card" }, el("h2", { text: "Repairs" }), el("p", { class: "small", text: c.total + " proposed · " + c.applied + " applied (" + c.verified_applied + " verified against the available tests) · " + c.reverted_regression + " auto-reverted · " + c.rolled_back + " rolled back · " + c.rejected + " rejected · " + c.invalid + " rejected by validation." }))));
    if (r.top_findings.length) out.push(el("section", { class: "card" }, el("h2", { text: "Findings (most important first)" }), table(["ID", "Severity", "Confidence", "Evidence", "Location", "Title"], r.top_findings.map((t) => el("tr", {},
      el("td", { class: "mono", text: t.finding_id }), el("td", {}, sev(t.severity)), el("td", { text: t.confidence }), el("td", { text: t.confirmation_label }), el("td", { class: "mono wrap", text: t.file ? t.file + (t.line ? ":" + t.line : "") : "-" }), el("td", { class: "wrap", text: t.title }))))));
    if (r.patches.length) out.push(el("section", { class: "card" }, el("h2", { text: "Repair proposals" }), table(["Patch", "Bug", "Status", "Change", "Verification"], r.patches.map((x) => el("tr", {},
      el("td", { text: "#" + x.patch_no }), el("td", { class: "wrap", text: x.finding_ref + " " + (x.finding_title || "") }), el("td", {}, badge(x.status.replace("_", " "), x.status === "applied" ? "ok" : "")), el("td", { class: "small", text: "+" + x.added + " / -" + x.removed }), el("td", { class: "small wrap", text: x.verification_label || "Not verified" }))))));
    out.push(el("section", { class: "card" }, el("h2", { text: "Limitations" }), el("ul", { class: "small" }, r.limitations.map((x) => el("li", { text: x })))));
    box.replaceChildren(...out);
  }
  init().catch((e) => { box.replaceChildren(el("div", { class: "alert bad", text: e.message })); });
})();
