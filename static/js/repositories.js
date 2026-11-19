(function () {
  "use strict";
  const { $, el, api, badge, when } = AP;
  const input = $("#file-input"), drop = $("#drop");

  async function limits() { const h = await AP.health(); if (h) $("#limits-text").textContent = "max " + h.limits.max_upload_mb + " MB, " + h.limits.max_files + " files"; }
  function picked() { const n = input.files.length; $("#picked").textContent = n ? Array.from(input.files).map((f) => f.name).join(", ") : ""; }
  input.addEventListener("change", picked);
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer && e.dataTransfer.files.length) { input.files = e.dataTransfer.files; picked(); } });

  async function load() {
    const box = $("#list");
    try {
      const { projects } = await api("/api/projects");
      if (!projects.length) { box.replaceChildren(el("div", { class: "empty" }, el("div", { class: "big", text: "No repositories yet" }), "Upload a ZIP above to index your first project.")); return; }
      box.replaceChildren(el("div", { class: "table-wrap" }, el("table", { class: "t" },
        el("thead", {}, el("tr", {}, ["Repository", "Python files", "Lines", "Tests", "Last scan", ""].map((h) => el("th", { text: h })))),
        el("tbody", {}, projects.map((p) => {
          const lr = p.last_run;
          return el("tr", { class: "row", onclick: (e) => { if (!e.target.closest("button,a")) location.href = "/app/repositories/" + p._id; } },
            el("td", { class: "wrap" }, el("a", { href: "/app/repositories/" + p._id, text: p.name }), el("div", { class: "muted small", text: "uploaded " + when(p.created_at) })),
            el("td", { text: String(p.python_file_count) }), el("td", { text: String(p.loc) }), el("td", { text: String(p.test_file_count) }),
            el("td", {}, lr ? [badge(lr.status, lr.status === "completed" ? "ok" : lr.status === "failed" ? "bad" : "warn"), " ", el("span", { class: "muted small", text: (lr.findings ? lr.findings.total : 0) + " finding(s)" })] : badge("not scanned")),
            el("td", { style: "white-space:nowrap" }, el("a", { class: "btn", href: "/app/scan?project=" + p._id, text: "Scan" }), " ", deleteButton(p)));
        })))));
    } catch (e) { box.replaceChildren(el("div", { class: "alert bad", text: "Could not load repositories: " + e.message })); }
  }

  // two-step inline confirmation instead of a native dialog
  function deleteButton(p) {
    const b = el("button", { class: "btn danger", type: "button", text: "Delete" });
    let armed = false, t;
    b.addEventListener("click", async () => {
      if (!armed) { armed = true; b.textContent = "Click again to delete"; t = setTimeout(() => { armed = false; b.textContent = "Delete"; }, 3500); return; }
      clearTimeout(t); b.disabled = true;
      try { await api("/api/projects/" + p._id, { method: "DELETE" }); APUI.toast("Deleted " + p.name); load(); } catch (e) { APUI.toast(e.message); b.disabled = false; }
    });
    return b;
  }

  $("#upload-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const err = $("#upload-error"); err.classList.add("hidden");
    if (!input.files.length) { err.textContent = "Choose a .zip or .py file first."; err.classList.remove("hidden"); return; }
    const body = new FormData(); for (const f of input.files) body.append("files", f);
    const btn = $("#upload-btn"); btn.disabled = true; btn.textContent = "Uploading…";
    try { const p = await api("/api/projects", { method: "POST", body }); location.href = "/app/repositories/" + p._id; }
    catch (e) { err.textContent = e.message; err.classList.remove("hidden"); btn.disabled = false; btn.textContent = "Upload and index"; }
  });
  limits(); load();
})();
