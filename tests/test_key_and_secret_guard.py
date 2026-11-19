"""Gemini key checks and the pre-transmission secret guard (Backend V2, Group A).

Secrets used here are fabricated test strings. The tests assert that no secret value ever appears in a response,
an error message, a status payload or a log record."""
import json
import logging

import pytest

from autopatch import create_app
from autopatch.core.guarded_llm import GuardedLLM, SecretsDetected, scan_outgoing
from autopatch.core.keycheck import check_key
from autopatch.core.secret_scan import scan_text
from tests import projects as P
from tests.test_gemini_rest import GOOD_DIFF, KEY, Fake, envelope, proposal  # noqa: F401

FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
FAKE_GOOGLE = "AIza" + "SyD1234567890abcdefghijklmnopqrstuv"
FAKE_GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


# ------------------------------------------------------------------------------------------- key checks
@pytest.mark.parametrize("value,problem", [
    ("", "empty"), ("   ", "empty"), (None, "empty"),
    ("your-key-here", "placeholder"), ("YOUR_API_KEY", "placeholder"), ("<paste key>", "whitespace"), ("changeme", "placeholder"),
    ("xxxxxxxxxxxxxxxxxxxx", "placeholder"), ("replace_me_with_key", "placeholder"),
    ('"AIza' + "a" * 35 + '"', "quoted"), ("AIza abc def", "whitespace"), ("AIzaKéy" + "a" * 30, "non_ascii"),
])
def test_unusable_keys_are_rejected_with_a_specific_reason(value, problem):
    r = check_key(value)
    assert r["ok"] is False and r["problem"] == problem and r["message"]
    assert (value or "").strip() not in r["message"] or not (value or "").strip()  # the message never echoes the key


def test_well_formed_key_passes_and_odd_formats_only_get_a_note():
    assert check_key("AIza" + "A" * 35) == {"ok": True, "problem": None, "message": None, "note": None}
    r = check_key("some-other-provider-key-0123456789")
    assert r["ok"] is True and r["note"]


def test_placeholder_words_do_not_reject_a_well_formed_google_key():
    # 'here'/'your' can occur by chance inside a real key; the exact Google shape wins
    assert check_key("AIza" + "yourhere" + "A" * 27)["ok"] is True


def _app(tmp_path, key, **over):
    return create_app({"DATA_DIR": tmp_path / "d", "STORAGE_BACKEND": "local", "GEMINI_API_KEY": key, "GEMINI_TRANSPORT": "rest", **over})


def test_status_reports_the_key_problem_without_the_key(tmp_path):
    d = _app(tmp_path, "your-key-here").test_client().get("/api/ai/status").get_json()
    assert d["configured"] is False and d["key_problem"] == "placeholder" and "placeholder" in d["key_message"]
    assert "your-key-here" not in json.dumps(d)
    ok = _app(tmp_path / "x", KEY).test_client().get("/api/ai/status").get_json()
    assert ok["configured"] is True and ok["key_problem"] is None


def test_a_bad_key_makes_no_request_and_returns_the_specific_message(tmp_path):
    fake = Fake()
    fake.replies = [(200, envelope(proposal()))]
    try:
        app = _app(tmp_path, "your-key-here", GEMINI_REST_BASE_URL=fake.url, GEMINI_RETRIES=0)
        c, f = _setup(app)
        r = c.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
        assert r.status_code == 503 and "placeholder" in r.get_json()["error"]
        assert fake.requests == []
    finally:
        fake.close()


# ------------------------------------------------------------------------------------------- scanner
@pytest.mark.parametrize("text,kind", [
    (f'aws = "{FAKE_AWS}"', "AWS access key id"), (f"key = {FAKE_GOOGLE}", "Google API key"), (f"t = '{FAKE_GH}'", "GitHub token"),
    ("-----BEGIN RSA PRIVATE " + "KEY-----", "Private key block"), ('DB = "postgres://admin:s3cretPass@db.internal/app"', "Database URL with credentials"),
    ('password = "Zx9!qL4#mW7vB2"', "Hard-coded credential"),
])
def test_scanner_detects_common_secrets_without_returning_values(text, kind):
    hits = scan_text(text, "a.py")
    assert [h.kind for h in hits] == [kind]
    assert not hasattr(hits[0], "preview") and text.split('"')[-2:-1] not in ([str(hits[0])],)
    assert "AKIA" not in repr(hits) and "s3cret" not in repr(hits) and "Zx9" not in repr(hits)


def test_scanner_ignores_placeholders_and_normal_code():
    clean = 'password = "your-password-here"\ntoken = os.environ["TOKEN"]\nsecret = "changeme"\napi_key = None\nx = "hello world, nothing here"\n'
    assert scan_text(clean) == []


def test_allow_marker_is_per_line_only():
    line = f'k = "{FAKE_AWS}"'
    assert scan_text(line + "  # autopatch: allow-secret") == []
    assert len(scan_text(line + "\n" + line + "  # autopatch: allow-secret")) == 1


# ------------------------------------------------------------------------------------------- guard
class Recorder:
    transport = "rest"

    def __init__(self):
        self.calls = []

    def generate(self, system, prompt):
        self.calls.append((system, prompt))
        return "ok"


class Cfg:
    GEMINI_KEY_MESSAGE = None


def test_guard_refuses_before_the_inner_client_is_called_and_never_echoes_the_secret(caplog):
    inner = Recorder()
    g = GuardedLLM(inner, Cfg())
    prompt = f"## Editable files\n### FILE: app/settings.py (excerpt)\n```python\nAWS = '{FAKE_AWS}'\n```\n"
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(SecretsDetected) as ei:
            g.generate("system", prompt)
    assert inner.calls == []
    assert ei.value.status == 422 and ei.value.kind == "secrets_detected"
    assert "AWS access key id" in str(ei.value) and "app/settings.py" in str(ei.value)
    assert FAKE_AWS not in str(ei.value) and FAKE_AWS not in caplog.text and FAKE_AWS[:8] not in str(ei.value)


def test_guard_scans_the_system_text_too():
    with pytest.raises(SecretsDetected):
        GuardedLLM(Recorder(), Cfg()).generate(f"token {FAKE_GH}", "clean")


def test_guard_passes_clean_prompts_through():
    inner = Recorder()
    assert GuardedLLM(inner, Cfg()).generate("s", "def add(a, b):\n    return a - b\n") == "ok" and len(inner.calls) == 1


def test_guard_blocks_the_gemini_key_itself():
    with pytest.raises(SecretsDetected):
        GuardedLLM(Recorder(), Cfg()).generate("s", f"debug: {FAKE_GOOGLE}")


def test_scan_outgoing_returns_kinds_and_sections_only():
    hits = scan_outgoing("s", f"### FILE: a.py (x)\nk='{FAKE_AWS}'\n")
    assert hits == [("AWS access key id", "a.py")]


# ------------------------------------------------------------------------------------------- end to end (REST and SDK modes)
def _setup(app, files=None):
    from autopatch.core import analysis as analysis_svc
    from tests.helpers import upload_files
    c, ext = app.test_client(), app.extensions["autopatch"]
    pid = upload_files(c, files or P.FAILING)
    run = analysis_svc.start_analysis(ext["store"], ext["cfg"], pid, {"run_entrypoints": False}, background=False)
    f = next(x for x in ext["store"].find("findings", {"run_id": run["_id"]}) if x["category"] == "test_failure")
    return c, f


def _with_secret(marker=""):
    files = dict(P.FAILING)
    name = next(k for k in files if k.endswith("util.py"))
    files[name] = f'AWS_KEY = "{FAKE_AWS}"{marker}\n' + files[name]
    return files


def test_end_to_end_secret_in_project_is_never_transmitted_rest(tmp_path, caplog):
    fake = Fake()
    fake.replies = [(200, envelope(proposal()))]
    try:
        app = _app(tmp_path, KEY, GEMINI_REST_BASE_URL=fake.url, GEMINI_RETRIES=0)
        c, f = _setup(app, _with_secret())
        with caplog.at_level(logging.DEBUG):
            r = c.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
        body = r.get_data(as_text=True)
        assert r.status_code == 422 and "secret" in body.lower() and FAKE_AWS not in body and FAKE_AWS not in caplog.text
        assert fake.requests == []  # nothing left the machine
        hist = c.get(f"/api/findings/{f['_id']}/patches").get_data(as_text=True)
        assert FAKE_AWS not in hist
    finally:
        fake.close()


def test_end_to_end_allow_marker_lets_the_request_through(tmp_path):
    fake = Fake()
    fake.replies = [(200, envelope(proposal()))]
    try:
        app = _app(tmp_path, KEY, GEMINI_REST_BASE_URL=fake.url, GEMINI_RETRIES=0)
        c, f = _setup(app, _with_secret("  # autopatch: allow-secret"))
        r = c.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
        assert r.status_code == 201 and len(fake.requests) == 1
    finally:
        fake.close()


def test_end_to_end_sdk_mode_is_guarded_too(tmp_path, monkeypatch):
    """With the SDK transport the guard wraps GeminiClient; the SDK is never reached when secrets are present."""
    from autopatch.core import gemini_client
    reached = []
    monkeypatch.setattr(gemini_client.GeminiClient, "generate", lambda self, s, p: reached.append(1) or "")
    app = create_app({"DATA_DIR": tmp_path / "d", "STORAGE_BACKEND": "local", "GEMINI_API_KEY": KEY, "GEMINI_TRANSPORT": "sdk"})
    c, f = _setup(app, _with_secret())
    r = c.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
    assert r.status_code == 422 and reached == [] and FAKE_AWS not in r.get_data(as_text=True)


# ------------------------------------------------------------------------------------------- Google status text (REST)
def test_rest_errors_include_google_status_text_but_not_the_key(tmp_path):
    fake = Fake()
    body = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": f"API key not valid {KEY}",
                      "details": [{"@type": "x", "reason": "API_KEY_INVALID"}]}}
    fake.replies = [(400, body)]
    try:
        app = _app(tmp_path, KEY, GEMINI_REST_BASE_URL=fake.url, GEMINI_RETRIES=0)
        c, f = _setup(app)
        r = c.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
        text = r.get_data(as_text=True)
        assert r.status_code == 502 and "API_KEY_INVALID" in text and "INVALID_ARGUMENT" in text and KEY not in text
    finally:
        fake.close()
