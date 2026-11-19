(function () {
  "use strict";
  const { $, el, api, sev, badge, table, when } = AP;
  const PS = { proposed: ["Proposed", "warn"], applied: ["Applied", "ok"], rejected: ["Rejected", ""], rolled_back: ["Rolled back", ""], reverted_regression: ["Reverted: regression", "bad"], invalid: ["Rejected by validation", "bad"], applying: ["Applying", "warn"] };

  async function load() {
    try {
      const st = await api("/api/ai/status");
      const n = $("#ai-note"), ok = st.configured && st.sdk_installed;
      n.textContent = ok ? "AI repair is ready (model " + st.model + (st.transport === "rest" ? ", built-in HTTPS client, no SDK needed" : "") + "). Source code is sent to the Gemini API only after you confirm on each request. Nothing is applied to your code unless you approve it."
        : "AI repair is not ready: " + (!st.configured ? "no GEMINI_API_KEY is configured" : "the google-genai package is not installed") + ". You can still review every bug here; add the key to your .env file and restart to enable repairs.";
      n.className = ok ? "note" : "alert warn";
    } catch (e) { $("#ai-note").textContent = "Could not check AI status: " + e.message; }
    const [iss, ver] = await Promise.all([api("/api/issues?limit=40"), api("/api/verification?limit=15")]);
    $("#bugs").replaceChildren(iss.issues.length ? table(["Severity", "Bug", "Location", "Repository", "Repair"], iss.issues.map((x) => el("tr", { class: "row sevrow " + x.severity, onclick: () => { location.href = "/app/studio/" + x._id; } },
      el("td", {}, sev(x.severity)), el("td", { class: "wrap" }, el("strong", { text: x.title }), el("div", { class: "muted small", text: x.finding_id })),
      el("td", { class: "mono wrap", text: x.file ? x.file + (x.line ? ":" + x.line : "") : "-" }), el("td", { text: x.project_name || "" }),
      el("td", {}, x.latest_patch ? badge("patch " + (PS[x.latest_patch.status] || [x.latest_patch.status])[0].toLowerCase(), (PS[x.latest_patch.status] || ["", ""])[1]) : el("span", { class: "btn", text: "Open" })))))
      : el("div", { class: "empty" }, el("div", { class: "big", text: "No bugs to repair yet" }), el("a", { class: "btn primary", href: "/app/scan", text: "Run a scan first" })));
    $("#recent").replaceChildren(ver.patches.length ? table(["Patch", "Bug", "Repository", "Status", "Verification", "When"], ver.patches.map((p) => el("tr", { class: "row", onclick: () => { location.href = "/app/studio/" + p.finding_id + "?patch=" + p._id; } },
      el("td", { text: "#" + p.patch_no }), el("td", { class: "wrap", text: (p.finding_ref || "") + " " + (p.finding_title || "") }), el("td", { text: p.project_name || "" }),
      el("td", {}, badge((PS[p.status] || [p.status])[0], (PS[p.status] || ["", ""])[1])), el("td", { class: "small wrap", text: p.label || "not verified" }), el("td", { class: "muted small", text: when(p.created_at) }))))
      : el("p", { class: "muted", text: "No repair has been proposed yet." }));
  }
  load().catch((e) => { $("#bugs").replaceChildren(el("div", { class: "alert bad", text: e.message })); });
})();
