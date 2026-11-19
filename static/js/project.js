(function () {
  "use strict";
  const { $, el, api, badge, sev, confBadge, tile, table, when, bars, SEV_COLOR } = AP;
  const root = $("#proj-root");
  const pid = root.dataset.projectId;
  let project = null, currentRun = null, poll = null, filters = { severity: "", category: "", confirmation: "", q: "" };
  const tabs = {};

  async function init() {
    try { project = await api("/api/projects/" + pid); }
    catch (e) { root.replaceChildren(el("div", { class: "alert bad", text: e.message })); return; }
    $("#proj-title").textContent = project.name; $("#crumb").replaceChildren(el("a", { href: "/app/analyze", text: "Analyze" }), " / ", project.name);
    const t = el("div", { class: "tabs" });
    const panes = el("div");
    for (const [id, label] of [["analysis", "Analysis"], ["structure", "Project structure"], ["runs", "Past runs"], ["patches", "Patch history"]]) {
      const b = el("button", { class: "tab", type: "button", text: label, onclick: () => select(id) });
      tabs[id] = { b, pane: el("div", { class: "hidden" }) };
      t.append(b); panes.append(tabs[id].pane);
    }
    root.replaceChildren(summaryCard(), t, panes);
    buildAnalysisPane(); renderStructure(); loadRuns(); AP.mountPatchHistory(tabs.patches.pane, pid);
    select("analysis");
    const want = new URLSearchParams(location.search).get("run") || (project.last_run && project.last_run.id);
    if (want) openRun(want);
  }
  function select(id) {
    for (const [k, v] of Object.entries(tabs)) { v.b.classList.toggle("active", k === id); v.pane.classList.toggle("hidden", k !== id); }
  }

  function summaryCard() {
    const p = project;
    return el("section", { class: "card" },
      el("div", { class: "tiles" },
        tile(p.python_file_count, "Python files"), tile(p.loc, "Lines of code"), tile(p.function_count, "Functions"),
        tile(p.test_file_count, "Test files"), tile(p.syntax_error_count, "Parse errors", p.syntax_error_count ? "bad" : "")),
      el("p", { class: "muted small mono", text: "uploaded " + when(p.created_at) + " · sha256 " + p.source_sha256.slice(0, 16) + " · " + (p.kind === "zip" ? "ZIP archive" : "loose files") }),
      p.warnings.length ? el("ul", { class: "small", style: "margin:.5rem 0 0" }, p.warnings.map((w) => el("li", { text: w }))) : null);
  }

  // ------------------------------------------------------------ analysis tab
  let startBtn, entryBox, startMsg, progressBox, resultBox;
  function buildAnalysisPane() {
    entryBox = el("input", { type: "checkbox", id: "opt-entry", checked: "checked" });
    startBtn = el("button", { class: "btn primary", type: "button", text: "Run analysis", onclick: startRun });
    startMsg = el("div", { class: "alert bad hidden" });
    progressBox = el("div", { class: "hidden" });
    resultBox = el("div");
    tabs.analysis.pane.append(
      el("section", { class: "card" }, el("h2", { text: "Run an analysis" }),
        el("p", { class: "small" }, "This runs the project's own pytest tests in a temporary copy on this computer, plus Ruff, Bandit and AST checks. ",
          "Your original files are never modified. This is restricted local execution with timeouts, ", el("strong", { text: "not a hardened sandbox" }), "."),
        el("label", { class: "small" }, entryBox, " Also run detected entry points once (no arguments, no input)"),
        el("div", { style: "margin-top:.9rem" }, startBtn), startMsg),
      progressBox, resultBox);
  }

  async function startRun() {
    startMsg.classList.add("hidden");
    startBtn.disabled = true;
    try {
      const run = await api("/api/projects/" + pid + "/runs", { json: { run_entrypoints: entryBox.checked } });
      watch(run._id);
    } catch (e) { startMsg.textContent = e.message; startMsg.classList.remove("hidden"); startBtn.disabled = false; }
  }

  function watch(runId) {
    clearInterval(poll);
    const tick = async () => {
      try {
        const run = await api("/api/runs/" + runId);
        renderProgress(run);
        if (run.status === "completed" || run.status === "failed") { clearInterval(poll); startBtn.disabled = false; await showResults(run); loadRuns(); }
      } catch (e) { clearInterval(poll); startBtn.disabled = false; startMsg.textContent = e.message; startMsg.classList.remove("hidden"); }
    };
    poll = setInterval(tick, 700); tick();
  }
  function openRun(id) { clearInterval(poll); api("/api/runs/" + id).then((run) => { if (run.status === "running" || run.status === "queued") watch(id); else { renderProgress(run); showResults(run); } select("analysis"); }).catch(() => {}); }

  function renderProgress(run) {
    const done = run.stages.filter((s) => s.status === "done" || s.status === "skipped" || s.status === "failed").length;
    progressBox.classList.remove("hidden");
    progressBox.replaceChildren(el("section", { class: "card" },
      el("h2", { text: "Analysis progress · " + run.status }),
      el("div", { class: "progress" }, el("span", { style: "width:" + Math.round(100 * done / run.stages.length) + "%" })),
      el("ul", { class: "steps" }, run.stages.map((s) => el("li", {},
        el("span", { class: "dot " + s.status }), el("span", {}, el("strong", { text: s.label }), el("div", { class: "muted small", text: s.detail || "" })),
        el("span", { class: "muted small", text: s.duration_s !== null && s.duration_s !== undefined ? s.duration_s + " s" : s.status })))),
      run.error ? el("div", { class: "alert bad", text: run.error }) : null));
  }

  async function showResults(run) {
    currentRun = run;
    if (run.status === "failed") { resultBox.replaceChildren(); return; }
    const pt = (run.summary || {}).pytest || {}, ps = pt.summary || {};
    const integ = run.integrity || {};
    const tool = (name, t) => el("tr", {}, el("td", { text: name }),
      el("td", {}, t ? badge(t.status, t.status === "ok" ? "ok" : t.status === "unavailable" ? "warn" : "bad") : badge("not run", "")),
      el("td", { text: t ? (t.status === "ok" ? t.count + " item(s)" : t.error || "") : "" }),
      el("td", { class: "muted", text: t && t.version ? "v" + t.version : "" }));
    const eps = (run.summary || {}).entrypoints || [];
    resultBox.replaceChildren(
      el("section", { class: "card" }, el("h2", { text: "Test baseline (pytest)" }),
        pt.status === "no_tests" ? el("p", { text: "No tests were found in this project, so there is no runtime baseline from tests." }) : null,
        el("div", { class: "tiles" }, tile(ps.passed || 0, "Passed", "good"), tile(ps.failed || 0, "Failed", ps.failed ? "bad" : ""),
          tile(ps.errors || 0, "Errors", ps.errors ? "bad" : ""), tile(ps.skipped || 0, "Skipped"), tile(ps.collection_errors || 0, "Collection errors", ps.collection_errors ? "bad" : "")),
        el("p", { class: "muted small", text: "pytest status: " + (pt.status || "n/a") + (pt.timed_out ? " (timed out and was stopped)" : "") + (pt.duration_s ? " · " + pt.duration_s + " s" : "") })),
      el("section", { class: "card" }, el("h2", { text: "Static and security tools" }),
        table(["Tool", "Status", "Result", "Version"], [tool("Ruff (static)", run.summary.ruff), tool("Bandit (security, static)", run.summary.bandit),
          tool("AST pattern checks", run.summary.ast_patterns ? { status: "ok", count: run.summary.ast_patterns.count } : null)]),
        run.summary.bandit && run.summary.bandit.status === "unavailable" ? el("p", { class: "small muted", text: "Bandit is not installed on this machine, so no security scan ran. Install it with: pip install bandit" }) : null,
        eps.length ? el("div", {}, el("div", { class: "section-t", text: "Entry points run once" }), table(["File", "Outcome", "Exit code"], eps.map((e) => el("tr", {},
          el("td", { class: "mono", text: e.file }), el("td", {}, badge(e.status.replace("_", " "), e.status === "ok" ? "ok" : e.status === "exception" ? "bad" : "warn")), el("td", { text: e.exit_code === null ? "-" : String(e.exit_code) }))))) : null),
      el("section", { class: "card" }, el("h2", { text: "Integrity and safety" }),
        el("p", { class: "small", text: integ.original_unchanged ? "Your original upload was verified unchanged after the run (SHA-256 of all files before and after)." : "WARNING: the original upload changed during the run. Do not trust this run." }),
        el("p", { class: "small muted", text: "Temporary working copy removed: " + (integ.workspace_removed ? "yes" : "no") + ". " + (run.execution_note || "") }),
        (run.warnings || []).length ? el("ul", { class: "small" }, run.warnings.map((w) => el("li", { text: w }))) : null),
      el("section", { class: "card", id: "findings-card" }, el("h2", { text: "Findings" }), el("div", { id: "findings-box", text: "Loading…" })));
    await loadFindings();
  }

  async function loadFindings() {
    if (!currentRun) return;
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(filters)) if (v) qs.set(k, v);
    const data = await api("/api/runs/" + currentRun._id + "/findings?" + qs);
    const box = $("#findings-box");
    const sel = (name, label, options) => {
      const s = el("select", { "aria-label": label, onchange: (e) => { filters[name] = e.target.value; loadFindings(); } },
        el("option", { value: "", text: label }), options.map(([v, t]) => el("option", { value: v, text: t })));
      s.value = filters[name]; return s;
    };
    const f = data.facets;
    const search = el("input", { type: "search", placeholder: "Search title, file, rule…", value: filters.q, "aria-label": "Search findings" });
    search.addEventListener("input", () => { clearTimeout(search._t); search._t = setTimeout(() => { filters.q = search.value; loadFindings().then(() => { const s = $("#findings-box input[type=search]"); if (s) { s.focus(); s.setSelectionRange(99, 99); } }); }, 250); });
    const bar = el("div", { class: "filters" }, search,
      sel("severity", "All severities", Object.entries(f.severity).filter(([, n]) => n).map(([s, n]) => [s, s + " (" + n + ")"])),
      sel("category", "All categories", Object.entries(f.category).map(([c, o]) => [c, o.label + " (" + o.count + ")"])),
      sel("confirmation", "Static + runtime", [["runtime", "Runtime-confirmed (" + f.confirmation.runtime + ")"], ["static", "Static, not confirmed (" + f.confirmation.static + ")"]]));
    const rows = data.findings.map((x) => el("tr", { class: "row sevrow " + x.severity, onclick: () => { location.href = "/app/findings/" + x._id; } },
      el("td", { class: "mono", text: x.finding_id }), el("td", {}, sev(x.severity)), el("td", { text: x.confidence }),
      el("td", { class: "wrap" }, el("strong", { text: x.title }), x.occurrences > 1 ? el("span", { class: "muted", text: " ×" + x.occurrences }) : null),
      el("td", { text: x.category_label }), el("td", { class: "mono wrap", text: x.file ? x.file + (x.line ? ":" + x.line : "") : "-" }), el("td", {}, confBadge(x.confirmation))));
    box.replaceChildren(bar, rows.length ? table(["ID", "Severity", "Confidence", "Finding", "Category", "Location", "Detected by"], rows)
      : el("p", { class: "muted", text: data.total === 0 && !filters.q && !filters.severity && !filters.category && !filters.confirmation
          ? "No findings. That means these checks found nothing, not that the code is bug-free." : "No findings match these filters." }),
      el("p", { class: "small muted", text: data.total + " finding(s) shown. Severity, confidence and detection method are reported separately; confidence is a heuristic, not a calibrated probability." }));
  }

  // ------------------------------------------------------------ structure + runs tabs
  function renderStructure() {
    const m = project.map, pane = tabs.structure.pane;
    const viewer = el("div", { class: "hidden" });
    const rows = m.files.map((f) => {
      const tr = el("tr", { class: "row", onclick: () => showFile(f.path, tr, viewer) },
        el("td", { class: "mono wrap", text: f.path }), el("td", { text: String(f.loc) }), el("td", { text: String(f.functions.length) }),
        el("td", {}, f.is_test ? badge("test") : null, " ", m.entry_points.includes(f.path) ? badge("entry point", "ok") : null, " ", f.error ? badge("line " + f.error.line + ": " + f.error.msg, "bad") : null));
      return tr;
    });
    const tp = m.third_party.map((t) => el("tr", {}, el("td", { class: "mono", text: t.name }), el("td", { text: t.used_in.length + " file(s)" }),
      el("td", {}, t.installed_here === null ? "unknown" : t.installed_here ? badge("installed", "ok") : badge("not installed", "warn"))));
    pane.append(el("section", { class: "card" }, el("h2", { text: "Files" }), table(["Path", "LOC", "Functions", "Notes"], rows), viewer),
      el("section", { class: "card" }, el("h2", { text: "Third-party imports" }),
        tp.length ? table(["Package", "Used in", "This machine"], tp) : el("p", { class: "muted", text: "None: standard library and project modules only." }),
        m.graph.cycles.length ? el("p", { class: "small" }, badge("circular imports", "warn"), " " + m.graph.cycles.map((c) => c.join(" ↔ ")).join("; ")) : null));
  }
  async function showFile(path, row, viewer) {
    viewer.classList.remove("hidden"); viewer.replaceChildren(el("p", { class: "muted", text: "Loading…" }));
    try {
      const f = await api("/api/projects/" + pid + "/file?path=" + encodeURIComponent(path));
      const lines = f.content.split("\n");
      viewer.replaceChildren(AP.codePanel(path, { start: 1, line: 0, lines }));
    } catch (e) { viewer.replaceChildren(el("div", { class: "alert bad", text: e.message })); }
  }
  async function loadRuns() {
    const { runs } = await api("/api/projects/" + pid + "/runs");
    tabs.runs.pane.replaceChildren(el("section", { class: "card" }, runs.length ? table(["Started", "Status", "Tests", "Findings"], runs.map((r) => el("tr", { class: "row", onclick: () => { openRun(r._id); } },
      el("td", { text: when(r.created_at) }), el("td", {}, badge(r.status, r.status === "completed" ? "ok" : r.status === "failed" ? "bad" : "warn")),
      el("td", { text: r.pytest ? r.pytest.passed + " passed, " + r.pytest.failed + " failed" : "-" }),
      el("td", { text: r.findings_summary ? String(r.findings_summary.total) : "-" })))) : el("p", { class: "muted", text: "No analyses yet." })));
  }
  init();
})();
