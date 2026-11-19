"""AI patch lifecycle: generate -> review -> accept/reject -> apply to a working copy -> verify -> rollback.

Safety model
* The uploaded original (``projects/<id>/original``) is never written. Accepted patches are applied to a
  separate working copy (``projects/<id>/working``); its digest and the original's digest are recorded.
* Nothing is applied without an explicit accept. Gemini is only called from ``generate_patch``.
* The model's diff is validated by ``patch_diff`` (no new/deleted files, no traversal, no test files...).
* Every apply takes a pre-patch snapshot of the touched files, so rollback restores them byte for byte
  and is checked against the recorded pre-patch digest.
* pytest runs in a throw-away copy (a restricted local run, NOT a hardened sandbox), never in the working copy.
* Verification wording never says "fixed" or "correct": it reports what the available tests showed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path

from ..utils import utcnow_iso
from . import analysis as analysis_svc
from .analysis import remove_tree, tree_digest
from .gemini_client import AIError
from .patch_diff import Limits, PatchError, plan_patch, sha256_bytes
from .patch_prompt import build_prompt, parse_ai_response, sanitize_instruction
from .projects import original_dir, project_root
from .pytest_runner import run_pytest

CAVEAT = ("Passing tests do not prove a fix is correct. This result only describes what the project's "
          "available tests showed.")
_op_lock = threading.Lock()  # one apply/verify/rollback at a time (pytest runs are heavy and share the working copy)


class PatchServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- paths
def working_dir(cfg, project_id: str) -> Path:
    return project_root(cfg, project_id) / "working"


def snapshot_dir(cfg, project_id: str, patch_id: str) -> Path:
    return project_root(cfg, project_id) / "snapshots" / patch_id


def current_root(cfg, project_id: str) -> Path:
    w = working_dir(cfg, project_id)
    return w if w.is_dir() else original_dir(cfg, project_id)


def _copy_tree(src: Path, dst: Path) -> None:
    # copyfile (no metadata): avoids Windows attribute/permission errors and does not carry read-only flags over
    shutil.copytree(src, dst, copy_function=shutil.copyfile)


def ensure_working(cfg, project_id: str) -> Path:
    """Create the working copy once. It is built beside the target and renamed into place, so a failed copy
    never leaves a half-filled ``working`` folder that later looks valid."""
    w = working_dir(cfg, project_id)
    if w.is_dir():
        return w
    tmp = w.with_name("working.tmp")
    remove_tree(tmp)
    try:
        _copy_tree(original_dir(cfg, project_id), tmp)
        os.replace(tmp, w)
    except OSError as exc:
        remove_tree(tmp)
        raise PatchServiceError(f"Could not create the working copy ({exc.__class__.__name__}: {getattr(exc, 'filename', None) or 'unknown file'}).", 500) from None
    return w


def _safe_rel(rel: str) -> str:
    if not rel or rel.startswith("/") or "\\" in rel or ".." in Path(rel).parts or ":" in rel.split("/")[0]:
        raise PatchServiceError("Unsafe file path.", 400)
    return rel


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".ap-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _event(doc: dict, event: str, detail: str = "") -> list[dict]:
    return list(doc.get("events") or []) + [{"at": utcnow_iso(), "event": event, "detail": detail}]


# ---------------------------------------------------------------- lookups
def _get_patch(store, patch_id: str) -> dict:
    doc = store.get("patches", patch_id)
    if not doc:
        raise PatchServiceError("Patch not found.", 404)
    return doc


def _test_files(project: dict) -> set[str]:
    return set(project["map"]["tests"]["files"]) | {p for p in project["map"]["all_files"] if Path(p).name == "conftest.py"}


def _limits(cfg) -> Limits:
    return Limits(int(cfg.PATCH_MAX_FILES), int(cfg.PATCH_MAX_CHANGED_LINES), int(cfg.PATCH_MAX_DIFF_KB) * 1024)


def public_summary(doc: dict) -> dict:
    v = doc.get("verification") or {}
    return {k: doc.get(k) for k in ("_id", "patch_no", "finding_id", "finding_ref", "finding_title", "project_id", "status", "created_at",
                                    "model", "files", "patch_hash", "added", "removed", "risk", "confidence", "decision", "error")} | {
        "verdict": v.get("verdict"), "verification_label": v.get("label"), "applied_at": (doc.get("applied") or {}).get("at"),
        "rolled_back_at": (doc.get("rollback") or {}).get("at")}


# ---------------------------------------------------------------- generation
def heuristic_risk(plan, finding: dict) -> dict:
    reasons, level = [], 0
    n = plan.added + plan.removed
    if n > 80:
        level, _ = 2, reasons.append(f"large change ({n} lines)")
    elif n > 30:
        level, _ = max(level, 1), reasons.append(f"moderate change ({n} lines)")
    if len(plan.changes) > 1:
        level = max(level, 1)
        reasons.append(f"touches {len(plan.changes)} files")
    if any(line[1:].lstrip().startswith(("def ", "class ")) for line in plan.diff.splitlines()
           if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))):
        level = max(level, 1)
        reasons.append("changes a function or class definition line")
    if not (finding.get("tests") or (finding.get("evidence") or {}).get("nodeid")):
        reasons.append("no test directly exercises this finding, so the result will be hard to verify")
    return {"level": ("low", "medium", "high")[level], "reasons": reasons or ["small, localised change"]}


def _combine(ai: str, heuristic: str) -> str:
    order = ("low", "medium", "high")
    return order[max(order.index(ai), order.index(heuristic))]


def _diagnostics(files: dict[str, bytes]) -> dict:
    """Facts about the files the model was given; helps explain a rejected proposal."""
    return {p: {"bytes": len(b), "crlf": b.count(b"\r\n"), "lf_only": b.count(b"\n") - b.count(b"\r\n"), "bom": b.startswith(b"\xef\xbb\xbf"),
                "sha256": sha256_bytes(b)} for p, b in files.items()}


def generate_patch(store, cfg, llm, finding_id: str, instruction=None) -> dict:
    finding = store.get("findings", finding_id)
    if not finding:
        raise PatchServiceError("Finding not found.", 404)
    project = store.get("projects", finding["project_id"])
    if not project or not original_dir(cfg, project["_id"]).is_dir():
        raise PatchServiceError("The project's files are no longer on disk. Upload the project again.", 404)
    try:
        instruction = sanitize_instruction(instruction)
    except PatchError as exc:
        raise PatchServiceError(str(exc), 400) from None
    root = current_root(cfg, project["_id"])
    tests = _test_files(project)
    try:
        built = build_prompt(finding, project, root, tests, int(cfg.PATCH_MAX_FILES), int(cfg.PATCH_MAX_CONTEXT_KB) * 1024, instruction)
    except PatchError as exc:
        raise PatchServiceError(str(exc), 422) from None

    try:  # the only place AutoPatch talks to Gemini
        raw = llm.generate(built["system"], built["prompt"])
    except AIError as exc:
        raise PatchServiceError(str(exc), exc.status) from None

    n = len(store.find("patches", {"finding_id": finding["_id"]})) + 1
    base = {
        "finding_id": finding["_id"], "finding_ref": finding["finding_id"], "finding_title": finding["title"],
        "project_id": project["_id"], "run_id": finding["run_id"], "patch_no": n, "created_at": utcnow_iso(),
        "model": cfg.GEMINI_MODEL, "instruction": instruction, "instruction_chars": len(instruction or ""), "prompt_sha256": hashlib.sha256(built["prompt"].encode()).hexdigest(),
        "decision": None, "baseline": None, "verification": None, "applied": None, "rollback": None,
    }
    try:
        resp = parse_ai_response(raw)
        plan = plan_patch(resp["diff"], built["files"], built["allowed"], tests, _limits(cfg))
    except PatchError as exc:
        doc = {**base, "status": "invalid", "error": str(exc), "raw_response_excerpt": str(raw)[:2000], "file_diagnostics": _diagnostics(built["files"]),
               "events": [{"at": utcnow_iso(), "event": "rejected_by_validation", "detail": str(exc)}]}
        doc["_id"] = store.insert("patches", doc)
        raise PatchServiceError(f"The AI's proposal was rejected by validation: {exc}", 422) from None

    warnings = list(plan.warnings)
    declared = set(resp["affected_files"])
    if declared and declared != {c.path for c in plan.changes}:
        warnings.append("The files the AI listed differ from the files its diff changes; the diff was used.")
    h = heuristic_risk(plan, finding)
    doc = {
        **base, "status": "proposed", "error": None,
        "explanation": resp["explanation"], "why_it_fixes": resp["why_it_fixes"], "side_effects": resp["side_effects"],
        "suggested_tests": resp["suggested_tests"],
        "files": [{"path": c.path, "added": c.added, "removed": c.removed, "before_sha256": c.before_sha256, "after_sha256": c.after_sha256}
                  for c in plan.changes],
        "diff": plan.diff, "patch_hash": plan.sha256, "added": plan.added, "removed": plan.removed, "warnings": warnings,
        "risk": {"ai_reported": resp["risk"], "heuristic": h["level"], "level": _combine(resp["risk"], h["level"]), "reasons": h["reasons"]},
        "confidence": {"ai_reported": resp["confidence"],
                       "note": "A heuristic (the model's own estimate), not a calibrated probability."},
        "events": [{"at": utcnow_iso(), "event": "proposed", "detail": f"Generated by {cfg.GEMINI_MODEL}" + (f" with a reviewer instruction ({len(instruction)} characters)" if instruction else "") + "; passed validation"}],
    }
    doc["_id"] = store.insert("patches", doc)
    return doc


# ---------------------------------------------------------------- verification helpers
def _run_tests(cfg, source: Path, label: str) -> dict:
    run_dir = Path(cfg.DATA_DIR) / "runs" / f"pv-{label}"
    remove_tree(run_dir)
    work = run_dir / "work"
    try:
        run_dir.mkdir(parents=True)
        _copy_tree(source, work)
        return run_pytest(work, run_dir, cfg.PYTEST_TIMEOUT_SECONDS, int(cfg.MAX_OUTPUT_KB) * 1024)
    finally:
        remove_tree(run_dir)


def _brief(res: dict) -> dict:
    return {"status": res["status"], "summary": res["summary"], "timed_out": res.get("timed_out", False), "duration_s": res.get("duration_s"),
            "tests": {t["nodeid"]: t["outcome"] for t in res.get("tests", [])}, "collection_errors": len(res.get("collect_errors", [])),
            "note": res.get("note"), "stdout_tail": res.get("stdout_tail", "")}


BAD = {"failed", "error"}


def compare(base: dict, after: dict, relevant: list[str]) -> dict:
    b, a = base["tests"], after["tests"]
    newly_failing = sorted(n for n, o in a.items() if o in BAD and b.get(n) not in BAD and n in b)
    new_tests_failing = sorted(n for n, o in a.items() if o in BAD and n not in b)
    newly_passing = sorted(n for n, o in a.items() if o == "passed" and b.get(n) in BAD)
    still_failing = sorted(n for n, o in a.items() if o in BAD and b.get(n) in BAD)
    disappeared = sorted(n for n in b if n not in a)
    rel_failing_before = [n for n in relevant if b.get(n) in BAD]
    return {
        "newly_failing": newly_failing + new_tests_failing, "newly_passing": newly_passing, "still_failing": still_failing,
        "missing_after": disappeared, "new_collection_errors": after["collection_errors"] > base["collection_errors"],
        "relevant_tests": relevant, "relevant_failing_before": rel_failing_before,
        "relevant_now_passing": [n for n in rel_failing_before if a.get(n) == "passed"],
        "relevant_still_failing": [n for n in rel_failing_before if a.get(n) != "passed"],
    }


def _counts(s: dict) -> str:
    return f"{s['passed']} passed, {s['failed'] + s['errors']} failed, {s['skipped']} skipped"


def judge(base: dict, after: dict, cmp: dict) -> tuple[str, str]:
    """-> (verdict, label). Deliberately never says 'fixed' or 'correct'."""
    bs, as_ = base["status"], after["status"]
    if cmp["newly_failing"] or cmp["new_collection_errors"] or cmp["missing_after"]:
        bits = cmp["newly_failing"][:3] or (["a collection error"] if cmp["new_collection_errors"] else cmp["missing_after"][:3])
        return "regression", "Regression detected: " + ", ".join(bits) + " no longer passes after the patch."
    if as_ in ("timeout", "error", "not_run") and bs in ("passed", "failed", "no_tests"):
        return "regression", f"pytest could not complete after the patch (status: {as_}), although it ran before."
    if as_ in ("timeout", "error", "not_run"):
        return "inconclusive", f"pytest did not complete (status: {as_}), so nothing could be verified."
    if as_ == "no_tests":
        return "no_tests", "The project has no tests, so this patch is unverified."
    base_txt = f"baseline: {_counts(base['summary'])}"
    if cmp["relevant_failing_before"]:
        if not cmp["relevant_still_failing"]:
            return "target_resolved", f"Verified against the available test suite: {_counts(after['summary'])} ({base_txt}). The originally failing test(s) now pass."
        return "target_not_resolved", f"The originally failing test(s) still fail ({', '.join(cmp['relevant_still_failing'][:3])}). {_counts(after['summary'])} ({base_txt})."
    return "no_relevant_test", (f"No regression in the available tests ({_counts(after['summary'])}; {base_txt}), but no test exercises this finding, "
                                "so the fix remains insufficiently verified.")


# ---------------------------------------------------------------- accept / apply / verify
def _guard_busy() -> None:
    if analysis_svc._active:
        raise PatchServiceError("An analysis is running. Wait for it to finish.", 409)


def _restore(cfg, project_id: str, patch_id: str, files: list[str]) -> None:
    snap, work = snapshot_dir(cfg, project_id, patch_id), working_dir(cfg, project_id)
    for rel in files:
        _write_atomic(work / _safe_rel(rel), (snap / "files" / rel).read_bytes())


def accept_patch(store, cfg, patch_id: str) -> dict:
    doc = _get_patch(store, patch_id)
    if doc["status"] != "proposed":
        raise PatchServiceError(f"This patch is '{doc['status']}' and cannot be accepted.", 409)
    _guard_busy()
    if not _op_lock.acquire(blocking=False):
        raise PatchServiceError("Another patch operation is running. Try again in a moment.", 409)
    try:
        return _accept_locked(store, cfg, doc)
    finally:
        _op_lock.release()


def _accept_locked(store, cfg, doc: dict) -> dict:
    pid, patch_id = doc["project_id"], doc["_id"]
    project = store.get("projects", pid)
    finding = store.get("findings", doc["finding_id"])
    if not project or not finding or not original_dir(cfg, pid).is_dir():
        raise PatchServiceError("The project or finding no longer exists.", 404)
    orig = original_dir(cfg, pid)
    orig_before = tree_digest(orig)
    work = ensure_working(cfg, pid)

    paths = [f["path"] for f in doc["files"]]
    current = {p: (work / _safe_rel(p)).read_bytes() for p in paths if (work / p).is_file()}
    for f in doc["files"]:
        if f["path"] not in current or sha256_bytes(current[f["path"]]) != f["before_sha256"]:
            store.update("patches", patch_id, {"events": _event(doc, "stale", f"{f['path']} changed since this patch was generated")})
            raise PatchServiceError(f"{f['path']} has changed since this patch was generated (another patch may be applied). "
                                    "Roll that back or generate a new patch.", 409)
    try:
        plan = plan_patch(doc["diff"], current, set(paths), _test_files(project), _limits(cfg))
    except PatchError as exc:
        raise PatchServiceError(f"The patch no longer validates: {exc}", 409) from None
    if plan.sha256 != doc["patch_hash"]:
        raise PatchServiceError("The patch did not reproduce exactly; refusing to apply it.", 409)

    relevant = [n for n in dict.fromkeys((finding.get("tests") or []) + ([finding["evidence"]["nodeid"]] if (finding.get("evidence") or {}).get("nodeid") else []))]
    baseline = _brief(_run_tests(cfg, work, f"{patch_id}-base"))           # 1. baseline BEFORE applying

    pre_digest = tree_digest(work)                                        # 2. snapshot
    snap = snapshot_dir(cfg, pid, patch_id)
    remove_tree(snap)
    for c in plan.changes:
        dest = snap / "files" / c.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(c.before)
    (snap / "manifest.json").write_text(json.dumps({"patch_id": patch_id, "pre_digest": pre_digest, "files": paths}), encoding="utf-8")

    store.update("patches", patch_id, {"status": "applying", "events": _event(doc, "applying", "snapshot taken")})
    try:                                                                  # 3. apply to the working copy only
        for c in plan.changes:
            _write_atomic(work / c.path, c.after)
    except OSError as exc:
        _restore(cfg, pid, patch_id, paths)
        store.update("patches", patch_id, {"status": "proposed", "error": f"Could not write the working copy: {exc.__class__.__name__}",
                                           "events": _event(doc, "apply_failed", exc.__class__.__name__)})
        raise PatchServiceError("Could not apply the patch to the working copy; it was restored.", 500) from None
    post_digest = tree_digest(work)

    after_res = _run_tests(cfg, work, f"{patch_id}-after")                # 4. verify
    after = _brief(after_res)
    cmp = compare(baseline, after, relevant)
    verdict, label = judge(baseline, after, cmp)

    status, events = "applied", _event(doc, "accepted", "developer accepted; applied to the working copy")
    rollback = None
    if verdict == "regression":                                           # put the working copy back exactly
        _restore(cfg, pid, patch_id, paths)
        exact = tree_digest(work) == pre_digest
        status = "reverted_regression"
        rollback = {"at": utcnow_iso(), "reason": "automatic: regression detected by verification", "verified_exact": exact, "digest": pre_digest}
        events = events + [{"at": utcnow_iso(), "event": "auto_reverted", "detail": label}]

    verification = {
        "verdict": verdict, "label": label, "caveat": CAVEAT, "baseline": {k: v for k, v in baseline.items() if k not in ("tests", "stdout_tail")},
        "after": {k: v for k, v in after.items() if k not in ("tests", "stdout_tail")}, "comparison": cmp,
        "after_failures": [{"nodeid": f["nodeid"], "exception": f.get("exc"), "traceback": ((f.get("crash") or {}).get("text") or "")[:3000]}
                           for f in after_res.get("failures", [])][:10],
        "pytest_output_tail": after["stdout_tail"][-4000:], "baseline_output_tail": baseline["stdout_tail"][-2000:],
        "verified_at": utcnow_iso(),
    }
    orig_after = tree_digest(orig)
    update = {
        "status": status, "decision": {"action": "accept", "at": utcnow_iso()}, "baseline": verification["baseline"], "verification": verification,
        "applied": {"at": utcnow_iso(), "seq": time.time_ns(), "changed_files": [{"path": c.path, "before_sha256": c.before_sha256, "after_sha256": c.after_sha256} for c in plan.changes],
                    "patch_hash": plan.sha256, "pre_digest": pre_digest, "post_digest": post_digest, "snapshot": f"snapshots/{patch_id}"},
        "rollback": rollback, "original_unchanged": orig_before == orig_after, "events": events,
    }
    if orig_before != orig_after:
        update["events"] = update["events"] + [{"at": utcnow_iso(), "event": "warning", "detail": "The original upload changed during verification."}]
    store.update("patches", patch_id, update)
    return store.get("patches", patch_id)


def reject_patch(store, cfg, patch_id: str, reason: str = "") -> dict:
    doc = _get_patch(store, patch_id)
    if doc["status"] != "proposed":
        raise PatchServiceError(f"This patch is '{doc['status']}' and cannot be rejected.", 409)
    reason = (reason or "")[:500]
    store.update("patches", patch_id, {"status": "rejected", "decision": {"action": "reject", "at": utcnow_iso(), "reason": reason},
                                       "events": _event(doc, "rejected", reason or "developer rejected")})
    return store.get("patches", patch_id)


# ---------------------------------------------------------------- rollback
def rollback_patch(store, cfg, patch_id: str) -> dict:
    doc = _get_patch(store, patch_id)
    if doc["status"] != "applied":
        raise PatchServiceError(f"Only an applied patch can be rolled back (this one is '{doc['status']}').", 409)
    pid = doc["project_id"]
    later = [p for p in store.find("patches", {"project_id": pid})
             if p["status"] == "applied" and p["_id"] != patch_id and (p.get("applied") or {}).get("seq", 0) > doc["applied"].get("seq", 0)]
    if later:
        raise PatchServiceError("Roll back the more recently applied patch first (patch "
                                f"#{later[0]['patch_no']} of finding {later[0]['finding_ref']}), so the working copy returns to each snapshot exactly.", 409)
    if not _op_lock.acquire(blocking=False):
        raise PatchServiceError("Another patch operation is running. Try again in a moment.", 409)
    try:
        work = working_dir(cfg, pid)
        snap = snapshot_dir(cfg, pid, patch_id)
        if not work.is_dir() or not (snap / "manifest.json").is_file():
            raise PatchServiceError("The snapshot for this patch is missing, so it cannot be rolled back exactly.", 409)
        manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
        for f in doc["applied"]["changed_files"]:
            p = work / _safe_rel(f["path"])
            if not p.is_file() or sha256_bytes(p.read_bytes()) != f["after_sha256"]:
                raise PatchServiceError(f"{f['path']} was modified after the patch was applied; refusing to overwrite it.", 409)
        orig = original_dir(cfg, pid)
        orig_before = tree_digest(orig)
        _restore(cfg, pid, patch_id, manifest["files"])
        digest = tree_digest(work)
        exact = digest == manifest["pre_digest"]
        rb = {"at": utcnow_iso(), "reason": "developer requested", "verified_exact": exact, "digest": digest,
              "restored_files": manifest["files"], "original_unchanged": orig_before == tree_digest(orig)}
        store.update("patches", patch_id, {"status": "rolled_back", "rollback": rb,
                                           "events": _event(doc, "rolled_back", "working copy restored" + ("" if exact else " (digest mismatch!)"))})
        return store.get("patches", patch_id)
    finally:
        _op_lock.release()


def recover_interrupted_patches(store, cfg) -> int:
    """After a crash mid-apply: restore the snapshot so the working copy is never left half-patched."""
    n = 0
    for doc in store.find("patches", {"status": "applying"}):
        try:
            manifest = json.loads((snapshot_dir(cfg, doc["project_id"], doc["_id"]) / "manifest.json").read_text(encoding="utf-8"))
            _restore(cfg, doc["project_id"], doc["_id"], manifest["files"])
            detail = "interrupted while applying; working copy restored from the snapshot"
        except (OSError, ValueError, KeyError, PatchServiceError):
            detail = "interrupted while applying; snapshot could not be restored, discard the working copy"
        store.update("patches", doc["_id"], {"status": "proposed", "error": detail, "events": _event(doc, "recovered", detail)})
        n += 1
    return n
