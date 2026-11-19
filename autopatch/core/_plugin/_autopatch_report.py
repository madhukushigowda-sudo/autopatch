"""pytest plugin loaded INSIDE the child pytest process (``-p _autopatch_report``).

It appends one JSON object per line to the file named by $AUTOPATCH_EVENTS, flushing
after every event. If a test hangs and the process is killed, the events written so
far still tell us which test was running. Standard library only; never raises.
"""
import json
import os
import re

import pytest

_PATH = os.environ.get("AUTOPATCH_EVENTS")
_MAX_TEXT = 6000
_E_LINE = re.compile(r"^E\s+([A-Za-z_][\w.]*)(?::\s?(.*))?$")


def _emit(event):
    if not _PATH:
        return
    try:
        with open(_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            fh.flush()
    except Exception:  # noqa: BLE001 - a reporting problem must never break the test run
        pass


def _type_from_text(text):
    """Last 'E   SomeError: message' line of a traceback (used for collection errors)."""
    found = (None, None)
    for line in text.splitlines():
        m = _E_LINE.match(line)
        if m and (m.group(1).endswith(("Error", "Exception", "Exit", "Interrupt")) or "." in m.group(1)):
            found = (m.group(1), m.group(2) or "")
    return found


def _crash_info(longrepr):
    info = {"text": None, "message": None, "path": None, "lineno": None, "frames": []}
    try:
        info["text"] = str(longrepr)[:_MAX_TEXT]
        crash = getattr(longrepr, "reprcrash", None)
        if crash is not None:
            info["message"] = (getattr(crash, "message", "") or "")[:1000]
            info["path"] = str(getattr(crash, "path", "") or "") or None
            info["lineno"] = getattr(crash, "lineno", None)
        tb = getattr(longrepr, "reprtraceback", None)
        for entry in (getattr(tb, "reprentries", None) or []):
            loc = getattr(entry, "reprfileloc", None)
            if loc is None:
                continue
            info["frames"].append(
                {
                    "path": str(loc.path),
                    "lineno": loc.lineno,
                    "lines": [str(x)[:300] for x in (getattr(entry, "lines", None) or [])][:12],
                }
            )
    except Exception:  # noqa: BLE001
        pass
    return info


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    try:
        report = outcome.get_result()
        if call.excinfo is not None:
            report.autopatch_exc = {"type": call.excinfo.typename, "message": str(call.excinfo.value)[:500]}
    except Exception:  # noqa: BLE001
        pass


def pytest_collection_finish(session):
    _emit({"e": "collected", "count": len(session.items)})


def pytest_runtest_logstart(nodeid, location):
    _emit({"e": "start", "nodeid": nodeid})


def pytest_runtest_logreport(report):
    try:
        event = {
            "e": "report", "nodeid": report.nodeid, "when": report.when, "outcome": report.outcome,
            "duration": round(float(getattr(report, "duration", 0.0)), 4),
            "wasxfail": getattr(report, "wasxfail", None),
        }
        if report.outcome == "skipped":
            lr = report.longrepr
            event["skip_reason"] = str(lr[2] if isinstance(lr, tuple) and len(lr) == 3 else lr)[:300]
        if report.outcome == "failed":
            event["crash"] = _crash_info(report.longrepr)
            event["exc"] = getattr(report, "autopatch_exc", None)
            event["captured"] = [[str(n), str(c)[:2000]] for n, c in (report.sections or [])][:4]
        _emit(event)
    except Exception:  # noqa: BLE001
        pass


def pytest_collectreport(report):
    try:
        if report.failed:
            crash = _crash_info(report.longrepr)
            etype, emsg = _type_from_text(crash.get("text") or "")
            _emit({"e": "collect_error", "nodeid": report.nodeid, "crash": crash, "exc": {"type": etype, "message": emsg}})
    except Exception:  # noqa: BLE001
        pass


def pytest_sessionfinish(session, exitstatus):
    _emit({"e": "finish", "exitstatus": int(exitstatus)})
