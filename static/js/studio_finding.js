(function () {
  "use strict";
  const { $, el, api, sev, badge, confBadge, codePanel } = AP;
  const root = $("#root");

  // The patch workflow itself (generate, review, approve/reject, verify, roll back) is patches.js, unchanged.
  // Here we only default its diff to the side-by-side "original vs proposed" view.
  function preferSideBySide() {
    const seen = new WeakSet();
    const apply = () => root.querySelectorAll(".panel .ph .btn.link").forEach((b) => {
      const panel = b.closest(".panel"); if (b.textContent.startsWith("Original") && !seen.has(panel)) { seen.add(panel); b.click(); }
    });
    new MutationObserver(apply).observe(root, { childList: true, subtree: true }); apply();
  }

  async function load() {
    let f;
    try { f = await api("/api/findings/" + root.dataset.findingId); } catch (e) { root.replaceChildren(el("div", { class: "alert bad", text: e.message })); return; }
    $("#title").textContent = f.title; $("#intel").href = "/app/findings/" + f._id;
    $("#crumb").replaceChildren(el("a", { href: "/app/studio", text: "Repair Studio" }), " / ", el("a", { href: "/app/repositories/" + f.project_id, text: f.project_name }), " / " + f.finding_id);
    const ev = f.evidence || {};
    root.replaceChildren(
      el("section", { class: "card" }, el("div", { class: "chips", style: "margin-bottom:.6rem" }, sev(f.severity), badge("Confidence: " + f.confidence), confBadge(f.confirmation), badge(f.category_label)),
        el("p", { style: "margin:.2rem 0", text: f.description }), el("p", { class: "small muted", text: "Root cause (hypothesis): " + f.root_cause }),
        ev.snippet ? codePanel("Original code at " + f.file + ":" + f.line, ev.snippet, "highlighted line is where the issue was detected") : null),
      el("div", { id: "patch-host" }));
    preferSideBySide();
    AP.mountPatches($("#patch-host"), f, new URLSearchParams(location.search).get("patch"));
  }
  load();
})();
