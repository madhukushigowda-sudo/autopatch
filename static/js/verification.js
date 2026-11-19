(function () {
  "use strict";
  const { $, el, api, badge, table, tile, when } = AP;
  const VK = { target_resolved: ["Verified against available tests", "ok"], regression: ["Regression found", "bad"], target_not_resolved: ["Target test still failing", "bad"], no_relevant_test: ["No test covers this bug", "warn"], no_tests: ["No tests to run", "warn"], inconclusive: ["Inconclusive", "warn"] };
  const ST = { proposed: "Proposed", applied: "Applied", rejected: "Rejected", rolled_back: "Rolled back", reverted_regression: "Reverted", invalid: "Rejected by validation", applying: "Applying" };
  const counts = (s) => s ? s.passed + " passed / " + (s.failed + (s.errors || 0)) + " failed" : "-";

  async function load() {
    const d = await api("/api/verification"), c = d.counts;
    const ranTests = d.runs.filter((r) => r.pytest && r.status === "completed");
    const passed = ranTests.reduce((a, r) => a + (r.pytest.passed || 0), 0), failed = ranTests.reduce((a, r) => a + (r.pytest.failed || 0) + (r.pytest.errors || 0) + (r.pytest.collection_errors || 0), 0);
    $("#tiles").replaceChildren(tile(c.verified_applied, "Repairs verified", "good"), tile(Math.max(0, c.applied - c.verified_applied), "Applied, not verified"), tile(c.reverted_regression, "Auto-reverted (regression)", c.reverted_regression ? "bad" : ""),
      tile(passed, "Tests passed (listed scans)", "cyan"), tile(failed, "Tests failed (listed scans)", failed ? "bad" : ""));
    $("#patches").replaceChildren(d.patches.length ? table(["Patch", "Bug", "Repository", "Status", "Result", "Before → after"], d.patches.map((p) => {
      const v = VK[p.verdict];
      return el("tr", { class: "row", onclick: () => { location.href = "/app/studio/" + p.finding_id + "?patch=" + p._id; } },
        el("td", { text: "#" + p.patch_no }), el("td", { class: "wrap", text: (p.finding_ref || "") + " " + (p.finding_title || "") }), el("td", { text: p.project_name || "" }), el("td", {}, badge(ST[p.status] || p.status, p.status === "applied" ? "ok" : p.status === "reverted_regression" || p.status === "invalid" ? "bad" : "")),
        el("td", { class: "wrap" }, v ? badge(v[0], v[1]) : badge("Not verified"), p.newly_failing.length ? el("div", { class: "small mono", style: "color:var(--bad)", text: "newly failing: " + p.newly_failing.slice(0, 3).join(", ") }) : null),
        el("td", { class: "small", text: p.baseline ? counts(p.baseline) + " → " + counts(p.after) : "-" }));
    })) : el("div", { class: "empty" }, el("div", { class: "big", text: "No repairs yet" }), el("a", { class: "btn primary", href: "/app/studio", text: "Open the Repair Studio" })));
    $("#runs").replaceChildren(d.runs.length ? table(["Repository", "Started", "Status", "pytest", "Tests", "Ruff", "Bandit", "Findings"], d.runs.map((r) => el("tr", { class: "row", onclick: () => { location.href = "/app/scan?project=" + r.project_id + "&run=" + r._id; } },
      el("td", { class: "wrap", text: r.project_name || "" }), el("td", { class: "small", text: when(r.created_at) }), el("td", {}, badge(r.status, r.status === "completed" ? "ok" : r.status === "failed" ? "bad" : "warn")),
      el("td", { text: r.pytest_status || "-" }), el("td", { class: "small", text: r.pytest ? r.pytest.passed + " passed, " + (r.pytest.failed + (r.pytest.errors || 0)) + " failed" : "-" }),
      el("td", { text: r.tools.ruff || "-" }), el("td", { text: r.tools.bandit || "-" }), el("td", { text: r.findings_total === null || r.findings_total === undefined ? "-" : String(r.findings_total) }))))
      : el("div", { class: "empty" }, el("div", { class: "big", text: "No scans yet" }), el("a", { class: "btn primary", href: "/app/scan", text: "Go to Scan Center" })));
  }
  load().catch((e) => { $("#patches").replaceChildren(el("div", { class: "alert bad", text: e.message })); });
})();
