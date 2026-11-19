(function () {
  "use strict";
  const { $, el, api, badge, sev, confBadge, codePanel, when } = AP;
  const root = $("#f-root");
  const section = (title, ...kids) => [el("div", { class: "section-t", text: title }), ...kids];
  const prose = (text) => el("p", { style: "margin:.2rem 0", text });

  async function aiCard(f) {
    let patches = [];
    try { patches = (await api("/api/findings/" + f._id + "/patches")).patches; } catch (_) { /* optional section */ }
    const card = el("section", { class: "card" }, el("h2", { text: "AI explanation and suggested solution" }));
    const latest = patches.filter((p) => p.status !== "invalid").sort((a, b) => (a.created_at || "") < (b.created_at || "") ? 1 : -1)[0];
    if (!latest) {
      card.append(el("p", { class: "muted", text: "No AI repair has been generated for this bug yet. The explanation and suggested change are produced together with a proposed patch." }),
        el("a", { class: "btn primary", href: "/app/studio/" + f._id, text: "Generate a repair in the Repair Studio" }));
      return card;
    }
    try {
      const p = await api("/api/patches/" + latest._id);
      card.append(el("div", { class: "chips", style: "margin-bottom:.6rem" }, badge("patch #" + p.patch_no, "viol"), badge(p.status.replace("_", " "), p.status === "applied" ? "ok" : "warn"), p.verification_label ? badge("verified against available tests", "ok") : badge("not verified")),
        ...section("Explanation (AI-generated, review before trusting)", prose(p.explanation || "")), ...section("Why the change addresses it", prose(p.why_it_fixes || "")), ...section("Possible side effects", prose(p.side_effects || "")),
        el("div", { style: "margin-top:1rem" }, el("a", { class: "btn primary", href: "/app/studio/" + f._id, text: "Review in the Repair Studio" })));
    } catch (e) { card.append(el("div", { class: "alert bad", text: e.message })); }
    return card;
  }

  async function load() {
    let f;
    try { f = await api("/api/findings/" + root.dataset.findingId); } catch (e) { root.replaceChildren(el("div", { class: "alert bad", text: e.message })); return; }
    $("#f-title").textContent = f.title;
    $("#crumb").replaceChildren(el("a", { href: "/app/issues", text: "Bug Intelligence" }), " / ", el("a", { href: "/app/repositories/" + f.project_id, text: f.project_name }), " / " + f.finding_id);
    $("#f-studio").href = "/app/studio/" + f._id;
    const ev = f.evidence || {};
    const meta = el("dl", { class: "meta" }, ...[["Finding ID", f.finding_id], ["Repository", f.project_name], ["Category", f.category_label], ["Location", f.file ? f.file + (f.line ? ":" + f.line : "") + (f.function ? " in " + f.function + "()" : "") : "not file-specific"],
      ["Rule", f.rule || "-"], ["Detected by", f.detection_method], ["Found", when(f.created_at)]].flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })]));
    const banner = f.confirmation === "runtime"
      ? el("div", { class: "note" }, el("strong", { text: "Runtime-confirmed. " }), "This behavior was observed while the code ran (a test or entry point). The traceback below was captured from that run.")
      : el("div", { class: "alert warn" }, el("strong", { text: "Static finding, not confirmed at runtime. " }), "This comes from reading the code. No execution was observed, so it may or may not occur in practice.");
    const frames = (ev.traceback || []).map((fr) => el("div", { class: "frame" }, el("div", { class: "where mono", text: fr.path + ":" + fr.lineno }), el("div", { class: "mono", style: "white-space:pre-wrap", text: (fr.lines || []).join("\n") })));
    const head = [el("div", { class: "chips", style: "margin-bottom:.9rem" }, sev(f.severity), badge("Confidence: " + f.confidence), confBadge(f.confirmation), f.occurrences > 1 ? badge(f.occurrences + " occurrences") : null), banner];
    const main = [
      el("section", { class: "card" }, el("h2", { text: "Summary" }), prose(f.description), ...section("Root cause (hypothesis)", prose(f.root_cause)), ...section("Triggering condition", prose(f.trigger))),
      el("section", { class: "card" }, el("h2", { text: "Evidence" }), ev.summary ? prose(ev.summary) : null,
        ev.exception ? el("p", { class: "mono small", text: ev.exception.type + ": " + ev.exception.message }) : null, ev.tool_message ? el("p", { class: "mono small", text: ev.tool_message }) : null,
        ev.snippet ? codePanel("Code at " + f.file + ":" + f.line, ev.snippet, "highlighted line is where the issue was detected") : null,
        frames.length ? el("div", { class: "panel" }, el("div", { class: "ph", text: "Traceback (call order, oldest first)" }), el("div", { class: "frames" }, frames)) : null,
        ev.traceback_text ? el("div", { class: "panel" }, el("div", { class: "ph", text: "Raw pytest traceback" }), el("pre", { class: "tb", text: ev.traceback_text })) : null,
        ev.stderr_tail ? el("div", { class: "panel" }, el("div", { class: "ph", text: "Captured stderr" }), el("pre", { class: "tb", text: ev.stderr_tail })) : null,
        ev.code_under_test ? el("p", { class: "small" }, badge("heuristic"), " Likely code under test: ", el("span", { class: "mono", text: ev.code_under_test.function + "() at " + ev.code_under_test.file + ":" + ev.code_under_test.line }), ". This is a guess from the failing statement, not proven.") : null,
        ev.url ? el("p", { class: "small" }, "Rule reference: ", el("a", { href: ev.url, rel: "noopener noreferrer", target: "_blank", text: ev.url })) : null,
        (f.tests || []).length ? el("div", {}, el("div", { class: "section-t", text: "Related tests" }), el("ul", { class: "small mono" }, f.tests.map((t) => el("li", { text: t })))) : null)];
    const aside = el("aside", { class: "issue-side" }, el("section", { class: "card" }, el("h2", { text: "Details" }), meta),
      el("section", { class: "card" }, el("h2", { text: "Scoring" }), el("p", { class: "small muted", style: "margin:0 0 .4rem", text: "Kept separate on purpose:" }),
        el("dl", { class: "meta" }, el("dt", { text: "Severity" }), el("dd", { text: f.severity }), el("dt", { text: "Confidence" }), el("dd", { text: f.confidence + " (heuristic)" }),
          el("dt", { text: "Evidence" }), el("dd", { text: f.confirmation === "runtime" ? "Observed at runtime" : "Static analysis only" }))));
    const mainBox = el("div", { class: "issue-main" }, ...main);
    root.replaceChildren(...head, el("div", { class: "issue" }, mainBox, aside));
    mainBox.append(await aiCard(f));
  }
  load();
})();
