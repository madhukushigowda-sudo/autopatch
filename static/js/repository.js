(function () {
  "use strict";
  const { $, el, api, badge, tile, table, when } = AP;
  const root = $("#root"), pid = root.dataset.projectId;
  let project = null; const tabs = {};

  function select(id) { for (const [k, v] of Object.entries(tabs)) { v.b.classList.toggle("active", k === id); v.pane.classList.toggle("hidden", k !== id); } }

  async function init() {
    try { project = await api("/api/projects/" + pid); } catch (e) { root.replaceChildren(el("div", { class: "alert bad", text: e.message }), el("p", {}, el("a", { href: "/app/repositories", text: "Back to repositories" }))); return; }
    $("#title").textContent = project.name; $("#scan-btn").href = "/app/scan?project=" + pid;
    $("#crumb").replaceChildren(el("a", { href: "/app/repositories", text: "Repositories" }), " / ", project.name);
    const p = project, tb = el("div", { class: "tabs", role: "tablist" }), panes = el("div");
    for (const [id, label] of [["files", "Files"], ["scans", "Scan history"], ["repairs", "Repairs"], ["deps", "Dependencies"]]) {
      const b = el("button", { class: "tab", type: "button", role: "tab", text: label, onclick: () => select(id) });
      tabs[id] = { b, pane: el("div", { class: "hidden", role: "tabpanel" }) }; tb.append(b); panes.append(tabs[id].pane);
    }
    root.replaceChildren(
      el("div", { class: "tiles" }, tile(p.python_file_count, "Python files", "cyan"), tile(p.loc, "Lines of code"), tile(p.function_count, "Functions"), tile(p.test_file_count, "Test files"), tile(p.syntax_error_count, "Parse errors", p.syntax_error_count ? "bad" : "")),
      el("p", { class: "muted small mono", text: "uploaded " + when(p.created_at) + " · sha256 " + p.source_sha256.slice(0, 16) + " · " + (p.kind === "zip" ? "ZIP archive" : "loose files") }),
      p.warnings.length ? el("div", { class: "alert warn" }, el("ul", { class: "small", style: "margin:0;padding-left:1.1rem" }, p.warnings.map((w) => el("li", { text: w })))) : el("span"),
      tb, panes);
    files(); deps(); scans(); AP.mountPatchHistory(tabs.repairs.pane, pid); select("files");
  }

  function files() {
    const m = project.map, pane = tabs.files.pane, viewer = el("div");
    const list = el("div", { class: "filelist" });
    let q = "";
    const draw = () => {
      list.replaceChildren(...m.files.filter((f) => f.path.toLowerCase().includes(q)).map((f) => {
        const row = el("div", { class: "f", tabindex: "0", role: "button", onclick: () => open(f.path, row), onkeydown: (e) => { if (e.key === "Enter") open(f.path, row); } },
          el("span", { text: f.path }), el("span", { class: "muted", text: f.error ? "⚠" : f.is_test ? "test" : String(f.loc) }));
        return row;
      }));
      if (!list.children.length) list.append(el("p", { class: "muted small", text: "No files match." }));
    };
    async function open(path, row) {
      list.querySelectorAll(".f.on").forEach((n) => n.classList.remove("on")); if (row) row.classList.add("on");
      viewer.replaceChildren(el("div", { class: "skel" }));
      try {
        const f = await api("/api/projects/" + pid + "/file?path=" + encodeURIComponent(path)), info = m.files.find((x) => x.path === path);
        viewer.replaceChildren(el("div", { class: "chips", style: "margin-bottom:.5rem" }, info && info.is_test ? badge("test file") : null, m.entry_points.includes(path) ? badge("entry point", "ok") : null,
          info && info.error ? badge("syntax error line " + info.error.line + ": " + info.error.msg, "bad") : null, info ? badge(info.functions.length + " function(s)") : null),
          AP.codePanel(path, { start: 1, line: 0, lines: f.content.split("\n") }));
      } catch (e) { viewer.replaceChildren(el("div", { class: "alert bad", text: e.message })); }
    }
    const search = el("input", { type: "search", placeholder: "Filter files…", "aria-label": "Filter files", style: "width:100%;margin-bottom:.6rem", oninput: (e) => { q = e.target.value.toLowerCase(); draw(); } });
    draw();
    pane.append(el("div", { class: "split" }, el("section", { class: "card" }, el("h2", { text: m.files.length + " file(s)" }), search, list), el("section", { class: "card" }, el("h2", { text: "File viewer" }), viewer)));
    viewer.append(el("p", { class: "muted", text: "Select a file to view it." }));
    if (m.files.length) { const first = m.files.find((f) => !f.is_test) || m.files[0]; const rows = Array.from(list.querySelectorAll(".f")); open(first.path, rows[m.files.indexOf(first)] || rows[0]); }
  }

  function deps() {
    const m = project.map, tp = m.third_party.map((t) => el("tr", {}, el("td", { class: "mono", text: t.name }), el("td", { text: t.used_in.length + " file(s)" }),
      el("td", {}, t.installed_here === null ? "unknown" : t.installed_here ? badge("installed", "ok") : badge("not installed", "warn"))));
    tabs.deps.pane.append(el("section", { class: "card" }, el("h2", { text: "Third-party imports" }),
      tp.length ? table(["Package", "Used in", "This machine"], tp) : el("p", { class: "muted", text: "None: standard library and project modules only." }),
      m.graph.cycles.length ? el("p", { class: "small" }, badge("circular imports", "warn"), " " + m.graph.cycles.map((c) => c.join(" ↔ ")).join("; ")) : null));
  }

  async function scans() {
    const { runs } = await api("/api/projects/" + pid + "/runs");
    tabs.scans.pane.replaceChildren(el("section", { class: "card" }, runs.length ? table(["Started", "Status", "Tests", "Findings"], runs.map((r) => el("tr", { class: "row", onclick: () => { location.href = "/app/scan?project=" + pid + "&run=" + r._id; } },
      el("td", { text: when(r.created_at) }), el("td", {}, badge(r.status, r.status === "completed" ? "ok" : r.status === "failed" ? "bad" : "warn")),
      el("td", { text: r.pytest ? r.pytest.passed + " passed, " + r.pytest.failed + " failed" : "-" }), el("td", { text: r.findings_summary ? String(r.findings_summary.total) : "-" }))))
      : el("div", { class: "empty" }, el("div", { class: "big", text: "No scans yet" }), el("a", { class: "btn primary", href: "/app/scan?project=" + pid, text: "Run the first scan" }))));
  }
  init();
})();
