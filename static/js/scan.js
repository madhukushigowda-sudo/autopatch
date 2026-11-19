(function () {
  "use strict";
  const { $, el, api, badge, tile, table } = AP;
  const params = new URLSearchParams(location.search);
  let poll = null, current = null;

  async function init() {
    let projects = [];
    try { projects = (await api("/api/projects")).projects; } catch (e) { $("#start-msg").textContent = e.message; $("#start-msg").classList.remove("hidden"); }
    const sel = $("#repo-select");
    sel.replaceChildren(...(projects.length ? projects.map((p) => el("option", { value: p._id, text: p.name })) : [el("option", { value: "", text: "No repositories uploaded" })]));
    if (params.get("project")) sel.value = params.get("project");
    $("#start").disabled = !projects.length;
    $("#start").addEventListener("click", start);
    const run = params.get("run");
    if (run) watch(run);
    else if (sel.value && params.get("project")) { // show latest run of the chosen repository if there is one
      const p = projects.find((x) => x._id === sel.value);
      if (p && p.last_run) watch(p.last_run.id);
    }
  }

  async function start() {
    const msg = $("#start-msg"); msg.classList.add("hidden");
    const pid = $("#repo-select").value; if (!pid) return;
    $("#start").disabled = true;
    try { const run = await api("/api/projects/" + pid + "/runs", { json: { run_entrypoints: $("#opt-entry").checked } }); $("#results").replaceChildren(); watch(run._id); }
    catch (e) { msg.textContent = e.message; msg.classList.remove("hidden"); $("#start").disabled = false; }
  }

  function watch(id) {
    clearInterval(poll);
    const tick = async () => {
      try {
        const run = await api("/api/runs/" + id); current = run; render(run);
        if (run.status === "completed" || run.status === "failed") { clearInterval(poll); $("#start").disabled = false; if (run.status === "completed") results(run); }
      } catch (e) { clearInterval(poll); $("#start").disabled = false; $("#run-box").replaceChildren(el("div", { class: "alert bad", text: e.message })); }
    };
    poll = setInterval(tick, 700); tick();
  }

  function render(run) {
    const total = run.stages.length, finished = run.stages.filter((s) => ["done", "skipped", "failed"].includes(s.status)).length;
    const pct = run.status === "completed" ? 100 : Math.round(100 * finished / total);
    $("#run-title").textContent = (run.project_name || "Scan") + " · " + run.status;
    $("#run-box").replaceChildren(
      el("div", { class: "pipe" }, run.stages.map((s) => el("div", { class: "pn " + s.status }, el("b", { text: s.label }), el("span", { text: s.status === "done" && s.duration_s !== null && s.duration_s !== undefined ? s.detail ? s.detail + " · " + s.duration_s + " s" : s.duration_s + " s" : s.detail || s.status })))),
      el("div", { class: "progress", role: "progressbar", "aria-valuenow": String(pct), "aria-valuemin": "0", "aria-valuemax": "100" }, el("span", { style: "width:" + pct + "%" })),
      el("p", { class: "muted small", text: pct + "% · " + finished + " of " + total + " stages finished" }),
      run.error ? el("div", { class: "alert bad", text: run.error }) : el("span"));
  }

  function results(run) {
    const pt = (run.summary || {}).pytest || {}, ps = pt.summary || {}, integ = run.integrity || {}, fs = run.findings_summary || { total: 0, by_severity: {} };
    const tool = (name, t) => el("tr", {}, el("td", { text: name }), el("td", {}, t ? badge(t.status, t.status === "ok" ? "ok" : t.status === "unavailable" ? "warn" : "bad") : badge("not run")),
      el("td", { text: t ? (t.status === "ok" ? t.count + " item(s)" : t.error || "") : "" }), el("td", { class: "muted", text: t && t.version ? "v" + t.version : "" }));
    const eps = (run.summary || {}).entrypoints || [];
    $("#results").replaceChildren(
      el("section", { class: "card" }, el("h2", { text: "Result" }),
        el("div", { class: "tiles" }, tile(fs.total, "Findings", fs.total ? "bad" : "good"), tile(fs.by_severity.Critical || 0, "Critical", fs.by_severity.Critical ? "bad" : ""), tile(fs.by_severity.High || 0, "High", fs.by_severity.High ? "bad" : ""),
          tile(ps.passed || 0, "Tests passed", "good"), tile((ps.failed || 0) + (ps.errors || 0) + (ps.collection_errors || 0), "Tests failing / errors", (ps.failed || ps.errors || ps.collection_errors) ? "bad" : "")),
        pt.status === "no_tests" ? el("p", { class: "muted", text: "No tests were found, so there is no test baseline. Findings come from static checks and entry points only." }) : null,
        el("div", { class: "chips" }, el("a", { class: "btn primary", href: "/app/issues?project_id=" + run.project_id, text: "Open Bug Intelligence (" + fs.total + ")" }), el("a", { class: "btn", href: "/app/repositories/" + run.project_id, text: "Repository" }), el("a", { class: "btn", href: "/app/reports?project=" + run.project_id, text: "Report" })),
        fs.total === 0 ? el("p", { class: "muted small", text: "No findings means these checks found nothing, not that the code is bug-free." }) : null),
      el("section", { class: "card" }, el("h2", { text: "Static and security tools" }),
        table(["Tool", "Status", "Result", "Version"], [tool("Ruff (static)", run.summary.ruff), tool("Bandit (security, static)", run.summary.bandit), tool("AST pattern checks", run.summary.ast_patterns ? { status: "ok", count: run.summary.ast_patterns.count } : null)]),
        run.summary.bandit && run.summary.bandit.status === "unavailable" ? el("p", { class: "small muted", text: "Bandit is not installed on this machine, so no security scan ran. Install it with: pip install bandit" }) : null,
        eps.length ? el("div", {}, el("div", { class: "section-t", text: "Entry points run once" }), table(["File", "Outcome", "Exit code"], eps.map((e) => el("tr", {}, el("td", { class: "mono", text: e.file }),
          el("td", {}, badge(e.status.replace("_", " "), e.status === "ok" ? "ok" : e.status === "exception" ? "bad" : "warn")), el("td", { text: e.exit_code === null ? "-" : String(e.exit_code) }))))) : null),
      el("section", { class: "card" }, el("h2", { text: "Integrity and safety" }),
        el("p", { class: "small", text: integ.original_unchanged ? "Your original upload was verified unchanged after the run (SHA-256 of all files before and after)." : "WARNING: the original upload changed during the run. Do not trust this run." }),
        el("p", { class: "small muted", text: "Temporary working copy removed: " + (integ.workspace_removed ? "yes" : "no") + ". " + (run.execution_note || "") }),
        (run.warnings || []).length ? el("ul", { class: "small" }, run.warnings.map((w) => el("li", { text: w }))) : null));
  }
  init();
})();
