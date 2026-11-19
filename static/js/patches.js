// Stage 3 UI: generate a patch, review the diff, accept/reject, see verification, roll back, patch history.
// All text (including AI output and source code) is inserted with textContent, never innerHTML.
(function () {
  "use strict";
  const { el, api, badge, table, when } = AP;
  // DOM append() would print the text "null" for null children, so skip empty values.
  const put = (node, ...kids) => { for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) node.append(k); return node; };

  const STATUS = {
    proposed: ["Proposed (not applied)", "warn"], rejected: ["Rejected", ""], applied: ["Applied to working copy", "ok"],
    rolled_back: ["Rolled back", ""], reverted_regression: ["Reverted: regression found", "bad"], invalid: ["Rejected by validation", "bad"],
    applying: ["Applying…", "warn"],
  };
  const VERDICT_KIND = { target_resolved: "ok", regression: "bad", target_not_resolved: "bad", no_relevant_test: "warn", no_tests: "warn", inconclusive: "warn" };
  const statusBadge = (s) => badge((STATUS[s] || [s, ""])[0], (STATUS[s] || [s, ""])[1]);

  // ---- diff rendering (from the stored canonical unified diff)
  function parseDiff(text) {
    const files = []; let file = null, hunk = null, o = 0, n = 0;
    for (const line of text.split("\n")) {
      if (line.startsWith("--- ")) { file = { path: line.slice(6), hunks: [] }; files.push(file); hunk = null; }
      else if (line.startsWith("+++ ")) continue;
      else if (line.startsWith("@@")) {
        const m = /^@@ -(\d+)(?:,\d+)? \+(\d+)/.exec(line); o = m ? +m[1] : 1; n = m ? +m[2] : 1;
        hunk = { header: line, rows: [] }; file.hunks.push(hunk);
      } else if (hunk && line.length) {
        const t = line[0], body = line.slice(1);
        if (t === "+") hunk.rows.push({ t, o: null, n: n++, body });
        else if (t === "-") hunk.rows.push({ t, o: o++, n: null, body });
        else hunk.rows.push({ t: " ", o: o++, n: n++, body });
      }
    }
    return files;
  }
  const cell = (num) => el("span", { class: "dn", text: num === null ? "" : String(num) });

  function unifiedView(files) {
    return files.map((f) => el("div", { class: "dv" }, el("div", { class: "dv-h", text: f.path }),
      f.hunks.map((h) => el("div", {}, el("div", { class: "dv-hunk", text: h.header }),
        h.rows.map((r) => el("div", { class: "dv-r " + (r.t === "+" ? "add" : r.t === "-" ? "del" : "") }, cell(r.o), cell(r.n),
          el("span", { class: "dm", text: r.t === " " ? " " : r.t }), el("span", { class: "dc", text: r.body })))))));
  }
  function sideView(files) {
    return files.map((f) => {
      const left = [], right = [];
      for (const h of f.hunks) {
        const rows = h.rows; let i = 0;
        left.push(el("div", { class: "dv-hunk", text: h.header })); right.push(el("div", { class: "dv-hunk", text: "" }));
        while (i < rows.length) {
          if (rows[i].t === " ") { left.push(line(rows[i], rows[i].o, "")); right.push(line(rows[i], rows[i].n, "")); i++; continue; }
          const dels = [], adds = [];
          while (i < rows.length && rows[i].t === "-") dels.push(rows[i++]);
          while (i < rows.length && rows[i].t === "+") adds.push(rows[i++]);
          for (let k = 0; k < Math.max(dels.length, adds.length); k++) {
            left.push(dels[k] ? line(dels[k], dels[k].o, "del") : line(null, null, "pad")); right.push(adds[k] ? line(adds[k], adds[k].n, "add") : line(null, null, "pad"));
          }
        }
      }
      return el("div", { class: "dv" }, el("div", { class: "dv-h", text: f.path }),
        el("div", { class: "dv-side" }, el("div", {}, el("div", { class: "dv-cap", text: "Original" }), left), el("div", {}, el("div", { class: "dv-cap", text: "Proposed" }), right)));
    });
    function line(r, num, cls) { return el("div", { class: "dv-r " + cls }, cell(num), el("span", { class: "dc", text: r ? r.body : "" })); }
  }
  function diffPanel(diff) {
    const files = parseDiff(diff), box = el("div", { class: "panel" });
    let mode = "unified";
    const body = el("div", { class: "dv-body" });
    const draw = () => { body.replaceChildren(...(mode === "unified" ? unifiedView(files) : sideView(files))); };
    const mk = (id, label) => el("button", { type: "button", class: "btn link", text: label, onclick: () => { mode = id; draw(); } });
    put(box, el("div", { class: "ph" }, el("span", { text: "Proposed change (green = added, red = removed)" }), el("span", {}, mk("unified", "Unified"), " · ", mk("side", "Original vs proposed"))), body);
    draw();
    return box;
  }

  // ---- verification panel
  function verification(p) {
    const v = p.verification; if (!v) return null;
    const row = (label, s) => el("tr", {}, el("td", { text: label }), el("td", { text: String(s.summary.passed) }), el("td", { text: String(s.summary.failed + s.summary.errors) }),
      el("td", { text: String(s.summary.skipped) }), el("td", { text: s.status }));
    const list = (title, items) => items && items.length ? el("div", { class: "small" }, el("strong", { text: title + ": " }), el("span", { class: "mono", text: items.join(", ") })) : null;
    const c = v.comparison;
    return el("div", { class: "vbox" },
      el("div", { class: "section-t", text: "Verification" }),
      el("div", { class: "alert " + (VERDICT_KIND[v.verdict] === "ok" ? "" : VERDICT_KIND[v.verdict] === "bad" ? "bad" : "warn"), style: "margin-top:.2rem" }, el("strong", { text: v.label })),
      table(["Run", "Passed", "Failed", "Skipped", "pytest status"], [row("Baseline (before patch)", v.baseline), row("After patch", v.after)]),
      list("Newly failing", c.newly_failing), list("Newly passing", c.newly_passing), list("Still failing", c.still_failing), list("Tests missing after patch", c.missing_after),
      list("Tests that cover this finding", c.relevant_tests),
      c.relevant_tests.length ? null : el("div", { class: "small muted", text: "No test is linked to this finding, so no test can confirm it was addressed." }),
      el("p", { class: "small muted", text: v.caveat }),
      p.original_unchanged === false ? el("div", { class: "alert bad", text: "The original upload changed during verification. Do not trust this result." }) : el("div", { class: "small muted", text: "Original upload verified unchanged. The patch exists only in the working copy." }),
      (v.after_failures || []).length ? el("details", {}, el("summary", { text: "Failing tests after the patch (" + v.after_failures.length + ")" }),
        v.after_failures.map((f) => el("div", {}, el("div", { class: "mono small", text: f.nodeid }), el("pre", { class: "tb", text: f.traceback || "" })))) : null,
      el("details", {}, el("summary", { text: "pytest output after the patch" }), el("pre", { class: "tb", text: v.pytest_output_tail || "(no output)" })));
  }

  // ---- one patch (review + actions)
  function patchView(p, onChange) {
    const box = el("div", { class: "card", style: "margin-top:1rem" });
    const err = el("div", { class: "alert bad hidden" });
    const act = async (what, body, btn, busy) => {
      err.classList.add("hidden"); btn.disabled = true; const old = btn.textContent; btn.textContent = busy;
      try { await api("/api/patches/" + p._id + "/" + what, { json: body || {} }); onChange(p._id); }
      catch (e) { err.textContent = e.message; err.classList.remove("hidden"); btn.disabled = false; btn.textContent = old; }
    };
    put(box, el("div", { style: "display:flex;gap:.6rem;flex-wrap:wrap;align-items:center" },
      el("strong", { text: "Patch #" + p.patch_no }), statusBadge(p.status), el("span", { class: "muted small", text: when(p.created_at) + " · " + (p.model || "") })));
    const ins = p.instruction ? el("div", { class: "small", style: "margin:.4rem 0" }, el("strong", { text: "Your instruction: " }), el("span", { class: "ins-text", text: p.instruction })) : null;
    if (p.status === "invalid") {
      put(box, ins, el("div", { class: "alert bad", text: "The AI's proposal was rejected by AutoPatch's validation and was not applied: " + p.error }),
        el("details", {}, el("summary", { text: "Raw AI response (untrusted, shown as text)" }), el("pre", { class: "tb", text: p.raw_response_excerpt || "" })));
      return box;
    }
    put(box, ins,
      el("div", { class: "section-t", text: "AI explanation" }), el("p", { style: "margin:.2rem 0", text: p.explanation }),
      p.why_it_fixes ? el("p", { class: "small", style: "margin:.2rem 0" }, el("strong", { text: "Why this addresses the bug: " }), p.why_it_fixes) : null,
      el("div", { style: "display:flex;gap:.5rem;flex-wrap:wrap;margin:.6rem 0" },
        badge("Risk: " + p.risk.level, p.risk.level === "low" ? "ok" : p.risk.level === "high" ? "bad" : "warn"),
        badge("AI confidence: " + p.confidence.ai_reported), badge("+" + p.added + " / -" + p.removed + " lines"), badge(p.files.length + " file(s)")),
      el("p", { class: "small muted", style: "margin:0 0 .4rem" }, "Risk and confidence are heuristics, not calibrated probabilities. Risk reasons: " + p.risk.reasons.join("; ") + ". Model's own risk estimate: " + p.risk.ai_reported + "."),
      el("div", { class: "small" }, el("strong", { text: "Affected files: " }), el("span", { class: "mono", text: p.files.map((f) => f.path).join(", ") })),
      (p.warnings || []).length ? el("div", { class: "small muted", text: "Notes: " + p.warnings.join("; ") }) : null,
      diffPanel(p.diff),
      (p.side_effects || []).length ? el("div", {}, el("div", { class: "section-t", text: "Possible side effects" }), el("ul", { class: "small" }, p.side_effects.map((s) => el("li", { text: s })))) : null,
      (p.suggested_tests || []).length ? el("div", {}, el("div", { class: "section-t", text: "Suggested test cases (not run, correctness unverified)" }), el("ul", { class: "small mono" }, p.suggested_tests.map((s) => el("li", { text: s })))) : null);

    if (p.status === "proposed") {
      const accept = el("button", { class: "btn primary", type: "button", text: "Accept patch" });
      accept.addEventListener("click", () => {
        if (window.confirm("Accept this patch?\n\nIt will be applied to a separate working copy (your original upload is never changed), and the project's tests will run before and after. A patch that causes a regression is reverted automatically.")) act("accept", {}, accept, "Applying and running tests…");
      });
      const reject = el("button", { class: "btn", type: "button", text: "Reject patch" });
      reject.addEventListener("click", () => { const r = window.prompt("Optional reason for rejecting:", ""); if (r !== null) act("reject", { reason: r }, reject, "Rejecting…"); });
      put(box, el("div", { class: "section-t", text: "Your decision" }), el("div", { style: "display:flex;gap:.6rem;flex-wrap:wrap" }, accept, reject),
        el("p", { class: "small muted", text: "Nothing is applied unless you accept." }));
    }
    const v = verification(p); if (v) put(box, v);
    if (p.status === "applied") {
      const rb = el("button", { class: "btn", type: "button", text: "Roll back this patch" });
      rb.addEventListener("click", () => { if (window.confirm("Restore the working copy to its exact pre-patch snapshot?")) act("rollback", {}, rb, "Rolling back…"); });
      put(box, el("div", { style: "margin-top:.8rem" }, rb));
    }
    if (p.status === "reverted_regression" || p.status === "rolled_back") {
      const r = p.rollback || {};
      put(box, el("div", { class: "alert " + (r.verified_exact ? "" : "bad"), style: "margin-top:.8rem" },
        (p.status === "reverted_regression" ? "The patch was reverted automatically. " : "The patch was rolled back. ") +
        (r.verified_exact ? "The working copy matches the pre-patch snapshot exactly (digest " + String(r.digest || "").slice(0, 12) + "…)." : "Warning: the restored working copy does not match the snapshot digest.")));
    }
    put(box, err,
      el("details", { style: "margin-top:.8rem" }, el("summary", { text: "History and hashes" }),
        el("div", { class: "small mono", text: "patch hash sha256 " + p.patch_hash }),
        p.applied ? el("div", { class: "small mono", text: "changed: " + p.applied.changed_files.map((c) => c.path + " " + c.before_sha256.slice(0, 8) + "→" + c.after_sha256.slice(0, 8)).join(", ") }) : null,
        el("ul", { class: "small" }, (p.events || []).map((e) => el("li", { text: when(e.at) + " · " + e.event + (e.detail ? ": " + e.detail : "") })))));
    return box;
  }

  // ---- the "AI patch" card on a finding page
  AP.mountPatches = async function (root, finding, focusId) {
    const card = el("section", { class: "card" }, el("h2", { text: "AI patch" }));
    const list = el("div"), msg = el("div", { class: "alert bad hidden" });
    let st = null;
    try { st = await api("/api/ai/status"); } catch (_) { /* leave null */ }
    const ready = st && st.configured && st.sdk_installed;
    const confirm = el("input", { type: "checkbox", id: "send-ok" });
    const btn = el("button", { class: "btn primary", type: "button", text: "Generate patch", disabled: "disabled" });
    const MAXI = 500;
    const instr = el("textarea", { id: "reviewer-instruction", class: "ins-box", rows: "3", maxlength: String(MAXI),
      placeholder: "e.g. Raise ValueError('Cannot divide by zero') instead of returning None." });
    const counter = el("span", { class: "small muted", text: "0 / " + MAXI });
    instr.addEventListener("input", () => { counter.textContent = instr.value.length + " / " + MAXI; });
    const sync = () => { if (ready && confirm.checked) btn.removeAttribute("disabled"); else btn.setAttribute("disabled", "disabled"); };
    confirm.addEventListener("change", sync);

    put(card, 
      el("p", { class: "small" }, "Ask Gemini to propose a fix. The proposal is only a suggestion: it is validated, shown for your review, and never applied unless you accept it."),
      st && !st.configured ? el("div", { class: "alert warn" }, "Gemini is not configured. Add GEMINI_API_KEY to your .env file and restart AutoPatch.") : null,
      st && st.configured && !st.sdk_installed ? el("div", { class: "alert warn" }, "The Gemini SDK is not installed. Run: pip install google-genai") : null,
      !st ? el("div", { class: "alert warn", text: "Could not read the AI status." }) : null,
      el("label", { class: "small" }, confirm, " I understand this sends this finding, the relevant source files, related test code and a project outline to the Gemini API (model: " + (st ? st.model : "?") + "). Nothing is sent during scanning."),
      el("div", { style: "margin:.7rem 0 .2rem" }, el("label", { class: "small", for: "reviewer-instruction" }, el("strong", { text: "Reviewer instruction (optional)" })), " ", counter),
      instr,
      el("p", { class: "small muted", style: "margin:.2rem 0" }, "Optional. This is a preference sent to Gemini together with the code. It does not change what is allowed: patches that edit tests, create files or fail validation are still rejected. Do not paste secrets."),
      el("div", { style: "margin:.7rem 0" }, btn), msg, list);

    async function refresh(focus) {
      const { patches } = await api("/api/findings/" + finding._id + "/patches");
      list.replaceChildren();
      if (!patches.length) { list.append(el("p", { class: "muted small", text: "No patches yet for this finding." })); return; }
      list.append(el("div", { class: "section-t", text: "Patch candidates (" + patches.length + ")" }));
      const rows = patches.slice().reverse();
      for (const s of rows) {
        const holder = el("div"), open = focus ? s._id === focus : s === rows[0];
        const head = el("button", { class: "btn", type: "button", style: "width:100%;text-align:left;margin-top:.5rem", onclick: async () => {
          if (holder.childElementCount) { holder.replaceChildren(); return; }
          holder.append(patchView(await api("/api/patches/" + s._id), (id) => refresh(id)));
        } }, "Patch #" + s.patch_no + " · " + (STATUS[s.status] || [s.status])[0] + (s.verdict ? " · " + s.verdict.replace(/_/g, " ") : "") + " · " + when(s.created_at));
        list.append(head, holder);
        if (open) api("/api/patches/" + s._id).then((p) => holder.append(patchView(p, (id) => refresh(id))));
      }
    }
    btn.addEventListener("click", async () => {
      msg.classList.add("hidden"); btn.disabled = true; const old = btn.textContent;
      btn.textContent = "Asking Gemini… (up to " + (st.timeout_seconds || 60) + " s)";
      try { const p = await api("/api/findings/" + finding._id + "/patches", { json: { confirm_send: true, instruction: instr.value } }); await refresh(p._id); }
      catch (e) { msg.textContent = e.message; msg.classList.remove("hidden"); await refresh(); }
      finally { btn.textContent = old; sync(); }
    });
    root.append(card);
    await refresh(focusId);
  };

  // ---- patch history table (project page tab)
  AP.mountPatchHistory = async function (pane, projectId) {
    const { patches } = await api("/api/projects/" + projectId + "/patches");
    pane.replaceChildren(el("section", { class: "card" }, el("h2", { text: "Patch history" }),
      patches.length ? table(["When", "Finding", "Patch", "Status", "Verification", "Change"], patches.map((p) => el("tr", { class: "row", onclick: () => { location.href = "/app/findings/" + p.finding_id + "?patch=" + p._id; } },
        el("td", { class: "muted", text: when(p.created_at) }), el("td", { class: "wrap", text: p.finding_ref + " " + p.finding_title }), el("td", { text: "#" + p.patch_no }),
        el("td", {}, statusBadge(p.status)), el("td", { class: "small", text: p.verification_label || "-" }),
        el("td", { class: "mono small", text: p.added !== undefined && p.added !== null ? "+" + p.added + " -" + p.removed : "-" }))))
        : el("p", { class: "muted", text: "No patches have been generated yet. Open a finding and choose Generate patch." }),
      el("p", { class: "small muted", text: "Rejected and rolled-back patches stay in this history." })));
  };
})();
