"""Structured findings.

Every finding says HOW it was found and WHETHER it was confirmed at runtime:

* confirmation == "runtime": the behaviour was actually observed while running tests or scripts.
* confirmation == "static":  found by reading the code (parser, Ruff, Bandit, AST patterns).
                             It is NOT confirmed at runtime, whatever its severity.

Severity is impact if the finding is real. Confidence is how likely it is real. They are
heuristic labels, not calibrated probabilities.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..utils import utcnow_iso

SEVERITIES = ("Critical", "High", "Medium", "Low")
CONFIDENCES = ("High", "Medium", "Low")
CONFIRMATIONS = ("runtime", "static")

CATEGORY_LABELS = {
    "syntax_error": "Syntax error",
    "test_failure": "Test failure",
    "runtime_exception": "Runtime exception",
    "collection_error": "Test collection error",
    "test_timeout": "Test timeout",
    "likely_runtime_error": "Likely runtime error",
    "logic_suspect": "Suspicious logic",
    "error_handling": "Error handling",
    "code_quality": "Code quality",
    "security": "Security",
    "environment": "Environment / dependency",
}
CONFIRMATION_LABELS = {
    "runtime": "Confirmed at runtime",
    "static": "Detected statically (not confirmed at runtime)",
}

# ---------------------------------------------------------------- rule knowledge base
# category, severity, confidence, title, root_cause, trigger
_R = lambda c, s, f, t, r, g: {"category": c, "severity": s, "confidence": f, "title": t, "root_cause": r, "trigger": g}  # noqa: E731

RULES: dict[str, dict] = {
    "F821": _R("likely_runtime_error", "High", "Medium", "Undefined name",
               "The name is used but never defined, imported or assigned in any visible scope, so Python raises NameError when this line runs.",
               "Any execution that reaches this line. Ruff cannot see names created dynamically (globals(), star imports), so this can be a false positive."),
    "F822": _R("likely_runtime_error", "Medium", "Medium", "Undefined name in __all__",
               "__all__ lists a name that the module does not define, so 'from module import *' raises AttributeError.",
               "Importing the module with a star import."),
    "F823": _R("likely_runtime_error", "High", "Medium", "Local variable referenced before assignment",
               "A local variable is read before any assignment in the same function, which raises UnboundLocalError.",
               "Calling the function along the path that reads the variable first."),
    "F5": _R("likely_runtime_error", "High", "High", "String formatting mismatch",
             "The format string and its arguments do not match (wrong count, missing key or bad specifier), which raises TypeError/ValueError/KeyError when the line executes.",
             "Any execution that reaches this formatting expression."),
    "F7": _R("likely_runtime_error", "High", "High", "Statement outside its valid context",
             "A statement such as return, break, continue or yield is used where Python does not allow it, which is rejected when the module is compiled.",
             "Importing or running this module."),
    "F811": _R("logic_suspect", "Medium", "Low", "Name redefined without being used",
               "A function, class or import is redefined before the first definition is used, so the first definition is dead code and may hide a mistake.",
               "Any call after the second definition uses the later one."),
    "F841": _R("code_quality", "Low", "Low", "Unused local variable",
               "A value is assigned but never used. This is often harmless, but it can also mean a result was meant to be returned or checked.",
               "Not triggered at runtime; informational."),
    "F631": _R("logic_suspect", "Medium", "High", "Assertion on a tuple",
               "assert (condition, message) asserts a non-empty tuple, which is always true, so the check never fails.",
               "Always; the assertion can never fail."),
    "F632": _R("logic_suspect", "Medium", "Medium", "Identity comparison with a literal",
               "'is' compares object identity, not value. Comparing to a literal works or fails depending on interpreter caching.",
               "Inputs whose objects are equal but not identical."),
    "F63": _R("logic_suspect", "Medium", "Medium", "Suspicious comparison or tuple condition",
              "The expression has a form that is almost always a mistake (for example a condition that is always true).",
              "Any execution that reaches this line."),
    "E711": _R("code_quality", "Low", "Medium", "Comparison to None with ==",
               "== None can be overridden by custom __eq__; the correct test is 'is None'.",
               "Objects that define a custom __eq__."),
    "E712": _R("code_quality", "Low", "Medium", "Comparison to True/False with ==",
               "Comparing to True/False with == matches 1/0 and other equal values; use the value directly or 'is'.",
               "Truthy values such as 1 or non-empty containers."),
    "E721": _R("code_quality", "Low", "Medium", "Type comparison with ==",
               "Comparing types with == ignores subclasses; isinstance() is the correct check.",
               "Subclass instances."),
    "E722": _R("error_handling", "Medium", "Medium", "Bare except",
               "A bare 'except:' also catches KeyboardInterrupt and SystemExit and can hide the real error.",
               "Any exception raised inside the try block."),
    "B002": _R("logic_suspect", "Medium", "High", "Increment/decrement operator has no effect",
               "Python has no ++ or --; '++x' is just +(+x), so the value never changes.",
               "Always; the statement does nothing."),
    "B006": _R("logic_suspect", "Medium", "High", "Mutable default argument",
               "A mutable default (list, dict, set) is created once at definition time and shared by every call, so changes leak between calls.",
               "Calling the function more than once without passing that argument, after it was modified."),
    "B012": _R("logic_suspect", "Medium", "High", "return/break/continue inside finally",
               "A jump statement in a finally block discards any exception that is propagating and overrides earlier returns, hiding real failures.",
               "Any execution where the try body raises or returns."),
    "B014": _R("code_quality", "Low", "High", "Redundant exception types in handler",
               "The except clause lists an exception together with its own subclass, or the same type twice. One of them has no effect.",
               "Any exception raised in the try block."),
    "B015": _R("logic_suspect", "Medium", "Medium", "Comparison result discarded",
               "A comparison is used as a statement, so its result is thrown away. An assignment or assert was probably intended.",
               "Always; the line has no effect."),
    "B018": _R("code_quality", "Low", "Low", "Useless expression",
               "An expression statement has no effect. It may be a leftover, or a call/assignment that was meant to happen.",
               "Always; the line has no effect."),
    "B023": _R("logic_suspect", "Medium", "High", "Closure captures loop variable",
               "A function defined in a loop uses the loop variable by name, so every function sees the variable's final value when called later.",
               "Calling the created functions after the loop has finished."),
    "B025": _R("logic_suspect", "Medium", "High", "Duplicate exception handler",
               "The same exception type is handled twice in one try statement; the second handler can never run.",
               "Raising that exception; only the first handler runs."),
    "B033": _R("code_quality", "Low", "Medium", "Duplicate value in set",
               "A set literal repeats a value, which is almost certainly a typo.",
               "Always; the duplicate is ignored."),
    "PLE": _R("likely_runtime_error", "Medium", "Medium", "Likely runtime error (Pylint error rule)",
              "A Pylint error-class rule matched, meaning this code is very likely to fail or misbehave when executed.",
              "Any execution that reaches this line."),
    "RET503": _R("code_quality", "Low", "Low", "Function may implicitly return None",
                 "Some paths return a value and others fall off the end, returning None, which callers may not expect.",
                 "Inputs that take the path without an explicit return."),
    # AutoPatch's own AST patterns
    "AP001": _R("error_handling", "Medium", "Low", "Exception silently swallowed",
                "The except block only contains 'pass', so any matching error disappears without a log or re-raise and failures go unnoticed.",
                "Any exception of the handled type raised inside the try block."),
    "AP002": _R("likely_runtime_error", "High", "High", "Division by literal zero",
                "The right-hand side is the constant 0, so this expression raises ZeroDivisionError every time it runs.",
                "Any execution that reaches this line."),
    "AP004": _R("likely_runtime_error", "Medium", "High", "Result of string method discarded",
                "Strings are immutable. Calling strip(), lower() and similar as a bare statement builds a new string and throws it away, so the value is unchanged.",
                "Any execution that reaches this line; later code sees the unmodified string."),
    "AP005": _R("likely_runtime_error", "Medium", "High", "In-place sort/reverse result used",
                "list.sort() and list.reverse() change the list in place and return None. Using their return value yields None, not the sorted list.",
                "Any execution that reaches this line; the name or return value is None."),
    "AP003": _R("logic_suspect", "Low", "Medium", "Unreachable code",
                "Statements follow a return/raise/break/continue in the same block, so they can never run. A misplaced return is a common cause.",
                "Never executes."),
}
_DEFAULT_RULE = _R("code_quality", "Low", "Low", "Static analysis note",
                   "Reported by the static analyser; see the message for details.", "Depends on the reported code path.")


def lookup_rule(code: str | None) -> dict:
    if not code:
        return _DEFAULT_RULE
    if code in RULES:
        return RULES[code]
    for length in range(len(code) - 1, 0, -1):  # longest matching prefix: F501 -> F5
        if code[:length] in RULES:
            return RULES[code[:length]]
    return _DEFAULT_RULE


# ---------------------------------------------------------------- helpers
@dataclass
class Ctx:
    """What finding builders need to know about the project."""
    original_dir: Path
    project_files: set[str]
    test_files: set[str]
    imported_files: set[str] = field(default_factory=set)  # files imported by other project modules
    entry_files: set[str] = field(default_factory=set)
    module_names: set[str] = field(default_factory=set)
    python_version: str = ""
    # name -> (file, line) for functions with a project-unique name (outside tests)
    functions: dict[str, tuple[str, int]] = field(default_factory=dict)
    # file -> [(start_line, end_line, qualname)] for every function, to find the enclosing function of a line
    func_ranges: dict[str, list[tuple[int, int, str]]] = field(default_factory=dict)


def make_snippet(original_dir: Path, rel: str | None, line: int | None, context: int = 3) -> dict | None:
    """A few source lines around ``line``, read from the (unchanged) original copy."""
    if not rel or not line:
        return None
    try:
        base = Path(os.path.abspath(original_dir))
        path = Path(os.path.abspath(base / rel))
        path.relative_to(base)  # must stay inside the project
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (ValueError, OSError):
        return None
    if not 1 <= int(line) <= len(lines):
        return None
    start = max(1, int(line) - context)
    end = min(len(lines), int(line) + context)
    return {"start": start, "line": int(line), "lines": [ln[:300] for ln in lines[start - 1:end]]}


def new_finding(*, category, severity, confidence, confirmation, title, description, root_cause, trigger,
                detection_method, evidence, file=None, line=None, rule=None, function=None, **extra) -> dict:
    assert severity in SEVERITIES, severity
    assert confidence in CONFIDENCES, confidence
    assert confirmation in CONFIRMATIONS, confirmation
    assert category in CATEGORY_LABELS, category
    evidence = dict(evidence)
    evidence["type"] = confirmation  # evidence type can never disagree with the confirmation label
    return {
        "category": category, "category_label": CATEGORY_LABELS[category],
        "severity": severity, "confidence": confidence,
        "confirmation": confirmation, "confirmation_label": CONFIRMATION_LABELS[confirmation],
        "title": title, "description": description, "root_cause": root_cause, "trigger": trigger,
        "detection_method": detection_method, "evidence": evidence,
        "file": file, "line": line, "function": function, "rule": rule, "related": [],
        **extra,
    }


def _sort_key(f: dict):
    return (SEVERITIES.index(f["severity"]), CONFIDENCES.index(f["confidence"]),
            0 if f["confirmation"] == "runtime" else 1, f["file"] or "", f["line"] or 0, f["title"])


def finalize(findings: list[dict], run_id: str, project_id: str) -> list[dict]:
    """Sort by importance, assign unique ids, cross-link runtime/static findings at the same place."""
    findings = sorted(findings, key=_sort_key)
    now = utcnow_iso()
    for n, f in enumerate(findings, start=1):
        f["_id"] = uuid.uuid4().hex
        f["finding_id"] = f"{run_id[:6]}-F{n:03d}"
        f["run_id"], f["project_id"], f["created_at"] = run_id, project_id, now
    by_place: dict[tuple, list[dict]] = {}
    for f in findings:
        if f["file"] and f["line"]:
            by_place.setdefault((f["file"], f["line"]), []).append(f)
    for group in by_place.values():
        kinds = {g["confirmation"] for g in group}
        if len(group) > 1 and len(kinds) > 1:  # a static and a runtime finding at the same line support each other
            for g in group:
                g["related"] = [o["finding_id"] for o in group if o is not g]
    return findings


def summarize(findings: list[dict]) -> dict:
    by_sev = dict.fromkeys(SEVERITIES, 0)
    by_cat: dict[str, int] = {}
    by_conf = dict.fromkeys(CONFIRMATIONS, 0)
    for f in findings:
        by_sev[f["severity"]] += 1
        by_cat[f["category"]] = by_cat.get(f["category"], 0) + 1
        by_conf[f["confirmation"]] += 1
    return {"total": len(findings), "by_severity": by_sev, "by_category": by_cat, "by_confirmation": by_conf}


# ---------------------------------------------------------------- builders: static
def from_syntax_error(err: dict, rel: str, ctx: Ctx) -> dict:
    blocking = rel in ctx.imported_files or rel in ctx.entry_files or rel in ctx.test_files
    return new_finding(
        category="syntax_error", severity="Critical" if blocking else "High", confidence="High", confirmation="static",
        title="Python syntax error",
        description=f"{err['msg']} (line {err['line']}).",
        root_cause=("The file cannot be parsed, so importing or running it fails with SyntaxError before any code executes. "
                    f"AutoPatch parsed it with Python {ctx.python_version}; if the project targets another Python version the syntax may be valid there."),
        trigger="Any import or execution of this module."
                + (" It is imported by other project files, a test, or is an entry point." if blocking else ""),
        detection_method="Python ast.parse (static parse; the code was not executed)",
        evidence={"summary": f"ast.parse rejected the file: {err['msg']} at line {err['line']}.", "snippet": make_snippet(ctx.original_dir, rel, err["line"])},
        file=rel, line=err["line"], rule="syntax",
    )


def from_ruff(item: dict, ctx: Ctx) -> dict:
    rule = lookup_rule(item["code"])
    return new_finding(
        category=rule["category"], severity=rule["severity"],
        confidence=_lower_in_tests(rule["confidence"], item["file"], ctx), confirmation="static",
        title=rule["title"], description=item["message"], root_cause=rule["root_cause"], trigger=rule["trigger"],
        detection_method=f"Ruff {item['code']} (static analysis)",
        evidence={"summary": f"Ruff rule {item['code']} matched: {item['message']}", "tool_message": item["message"],
                  "snippet": make_snippet(ctx.original_dir, item["file"], item["line"]), "url": item.get("url")},
        file=item["file"], line=item["line"], rule=item["code"],
    )


def _lower_in_tests(confidence: str, file, ctx: Ctx) -> str:
    """Static findings inside test files are less likely to matter (deliberate bad inputs, fixtures): lower confidence one step."""
    if file in getattr(ctx, "test_files", ()) and confidence != "Low":
        return {"High": "Medium", "Medium": "Low"}.get(confidence, confidence)
    return confidence


def from_pattern(item: dict, ctx: Ctx) -> dict:
    rule = lookup_rule(item["code"])
    return new_finding(
        category=rule["category"], severity=item.get("severity") or rule["severity"],
        confidence=_lower_in_tests(item.get("confidence") or rule["confidence"], item["file"], ctx), confirmation="static",
        title=rule["title"], description=item["message"], root_cause=rule["root_cause"], trigger=rule["trigger"],
        detection_method=f"AutoPatch AST pattern {item['code']} (static analysis)",
        evidence={"summary": item["message"], "snippet": make_snippet(ctx.original_dir, item["file"], item["line"])},
        file=item["file"], line=item["line"], rule=item["code"], function=item.get("function"),
    )


_BANDIT_SEV = {"HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"}
_BANDIT_CONF = {"HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"}


def from_bandit(item: dict, ctx: Ctx) -> dict:
    sev = _BANDIT_SEV.get(item["bandit_severity"], "Low")
    conf = _BANDIT_CONF.get(item["bandit_confidence"], "Low")
    if item["bandit_severity"] == "HIGH" and item["bandit_confidence"] == "HIGH":
        sev = "Critical"
    cwe = f" (CWE-{item['cwe']})" if item.get("cwe") else ""
    return new_finding(
        category="security", severity=sev, confidence=conf, confirmation="static",
        title=f"Security: {item.get('test_name') or item['code']}",
        description=f"{item['message']}{cwe}",
        root_cause=f"Bandit rule {item['code']} ({item.get('test_name')}) flags a pattern commonly linked to security weaknesses.",
        trigger="Depends on whether untrusted data can reach this call. Bandit matches patterns; it does not prove the code is reachable or exploitable.",
        detection_method=f"Bandit {item['code']} (static security analysis)",
        evidence={"summary": f"Bandit severity {item['bandit_severity']}, confidence {item['bandit_confidence']}: {item['message']}",
                  "tool_message": item["message"], "snippet": make_snippet(ctx.original_dir, item["file"], item["line"]),
                  "url": item.get("url"), "code_excerpt": item.get("code_excerpt")},
        file=item["file"], line=item["line"], rule=item["code"],
    )
