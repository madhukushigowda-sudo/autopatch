"""The built-in HTTPS Gemini client (no SDK, no Pydantic Core).

A tiny local HTTP server plays Google's endpoint so the real request/response code runs; no network, no paid calls,
and nothing here changes what the patch engine validates."""
import json
import logging
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from autopatch import create_app
from autopatch.core import gemini_rest
from autopatch.core.gemini_client import AIError
from autopatch.core.gemini_rest import RestGeminiClient, extract_text, select_llm
from tests import projects as P

KEY = "AIza" + "TestKeyThatMustNeverLeak0123456789"
GOOD_DIFF = "--- a/util.py\n+++ b/util.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
TEST_EDIT = "--- a/test_util.py\n+++ b/test_util.py\n@@ -1,4 +1,4 @@\n from util import add\n \n def test_add():\n-    assert add(1, 2) == 3\n+    assert True\n"


def envelope(text, **extra):
    return {"candidates": [{"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP"}], **extra}


def proposal(diff=GOOD_DIFF):
    return json.dumps({"explanation": "Addition was wrong.", "why_it_fixes": "Uses +.", "affected_files": [], "diff": diff,
                       "side_effects": ["None expected"], "suggested_tests": ["assert add(2, 3) == 5"], "risk": "low", "confidence": "medium"})


class Fake:
    """Scripted Google endpoint. replies: list of (status, body|callable|bytes); the last one repeats."""

    def __init__(self):
        self.replies, self.requests, self.delay = [], [], 0.0
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": json.loads(body or b"{}")})
                if outer.delay:
                    time.sleep(outer.delay)
                status, payload = outer.replies.pop(0) if len(outer.replies) > 1 else outer.replies[0]
                if status in (301, 302):
                    self.send_response(status)
                    self.send_header("Location", "http://127.0.0.1:9/elsewhere")
                    self.end_headers()
                    return
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1beta"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def fake():
    f = Fake()
    f.replies = [(200, envelope("{}"))]
    yield f
    f.close()


class Cfg:
    def __init__(self, base, **over):
        self.GEMINI_API_KEY, self.GEMINI_MODEL, self.GEMINI_TIMEOUT_SECONDS, self.GEMINI_RETRIES = KEY, "gemini-flash-latest", 5.0, 2
        self.GEMINI_TEMPERATURE, self.GEMINI_MAX_OUTPUT_TOKENS, self.GEMINI_THINKING_LEVEL, self.GEMINI_REST_BASE_URL = 0.2, 8192, "", base
        self.GEMINI_TRANSPORT = "rest"
        self.__dict__.update(over)


@pytest.fixture()
def client(fake, monkeypatch):
    monkeypatch.setattr(gemini_rest, "time", types.SimpleNamespace(sleep=lambda *_: None))  # no real back-off (module-local; the fake server still really sleeps)
    return lambda **over: RestGeminiClient(Cfg(fake.url, **over))


# ---------------------------------------------------------------- request shape
def test_request_goes_to_the_official_path_with_the_key_only_in_a_header(fake, client):
    fake.replies = [(200, envelope('{"ok": true}'))]
    assert client().generate("SYSTEM", "PROMPT") == '{"ok": true}'
    r = fake.requests[0]
    assert r["path"] == "/v1beta/models/gemini-flash-latest:generateContent"
    assert r["headers"]["x-goog-api-key"] == KEY and KEY not in r["path"] and "key=" not in r["path"]
    b = r["body"]
    assert b["systemInstruction"]["parts"][0]["text"] == "SYSTEM" and b["contents"] == [{"role": "user", "parts": [{"text": "PROMPT"}]}]
    assert b["generationConfig"] == {"temperature": 0.2, "maxOutputTokens": 8192, "responseMimeType": "application/json"}
    assert KEY not in json.dumps(b)


def test_thinking_level_follows_the_existing_rules(fake, client):
    fake.replies = [(200, envelope("x"))]
    client(GEMINI_MODEL="gemini-3-flash-preview").generate("s", "p")
    assert fake.requests[-1]["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    client(GEMINI_MODEL="gemini-3-flash-preview", GEMINI_THINKING_LEVEL="off").generate("s", "p")
    assert "thinkingConfig" not in fake.requests[-1]["body"]["generationConfig"]
    client(GEMINI_THINKING_LEVEL="medium").generate("s", "p")
    assert fake.requests[-1]["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "medium"}


def test_model_name_is_url_safe(fake, client):
    fake.replies = [(200, envelope("x"))]
    client(GEMINI_MODEL="models/gemini-2.5-pro").generate("s", "p")
    assert fake.requests[-1]["path"] == "/v1beta/models/gemini-2.5-pro:generateContent"
    client(GEMINI_MODEL="a/../b?x=1").generate("s", "p")
    assert "?" not in fake.requests[-1]["path"].split(":generateContent")[0] and "/../" not in fake.requests[-1]["path"]


def test_thinking_rejected_by_the_model_is_retried_once_without_it(fake, client):
    fake.replies = [(400, {"error": {"status": "INVALID_ARGUMENT", "message": "Thinking level is not supported for this model"}}), (200, envelope("ok"))]
    assert client(GEMINI_THINKING_LEVEL="low").generate("s", "p") == "ok"
    assert "thinkingConfig" in fake.requests[0]["body"]["generationConfig"] and "thinkingConfig" not in fake.requests[1]["body"]["generationConfig"]


# ---------------------------------------------------------------- responses
def test_text_parts_are_joined_and_thought_parts_skipped():
    data = {"candidates": [{"content": {"parts": [{"text": "hidden", "thought": True}, {"text": '{"a":'}, {"text": " 1}"}]}}]}
    assert extract_text(data) == '{"a": 1}'
    assert extract_text({}) == "" and extract_text({"candidates": []}) == "" and extract_text({"candidates": [{"finishReason": "SAFETY"}]}) == ""


@pytest.mark.parametrize("payload", [{}, {"candidates": []}, {"promptFeedback": {"blockReason": "SAFETY"}}, envelope("   ")])
def test_empty_or_blocked_answers_are_reported_as_blocked(fake, client, payload):
    fake.replies = [(200, payload)]
    with pytest.raises(AIError) as e:
        client().generate("s", "p")
    assert e.value.kind == "blocked" and e.value.status == 502


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]", b""])
def test_non_json_replies_become_api_errors(fake, client, raw):
    fake.replies = [(200, raw)]
    with pytest.raises(AIError) as e:
        client().generate("s", "p")
    assert e.value.kind == "api"


# ---------------------------------------------------------------- errors
@pytest.mark.parametrize("status,body,kind,http", [
    (400, {"error": {"status": "INVALID_ARGUMENT", "message": "API key not valid. Please pass a valid API key."}}, "invalid_key", 502),
    (403, {"error": {"status": "PERMISSION_DENIED", "message": "forbidden"}}, "invalid_key", 502),
    (401, {"error": {"message": "unauthenticated"}}, "invalid_key", 502),
    (429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}}, "quota", 429),
    (404, {"error": {"status": "NOT_FOUND", "message": "models/x is not found"}}, "model", 502),
    (500, {"error": {"message": "internal"}}, "api", 502),
    (400, {"error": {"message": "bad request"}}, "api", 502),
    (503, {"error": {"status": "UNAVAILABLE", "message": "overloaded"}}, "unavailable", 503),
])
def test_http_errors_map_to_the_same_kinds_as_the_sdk_client(fake, client, status, body, kind, http):
    fake.replies = [(status, body)]
    with pytest.raises(AIError) as e:
        client(GEMINI_RETRIES=0).generate("s", "p")
    assert e.value.kind == kind and e.value.status == http


def test_503_is_retried_then_succeeds_and_gives_up_after_the_limit(fake, client):
    fake.replies = [(503, {"error": {"message": "overloaded"}}), (503, {"error": {"message": "overloaded"}}), (200, envelope("fine"))]
    assert client(GEMINI_RETRIES=2).generate("s", "p") == "fine" and len(fake.requests) == 3
    fake.requests.clear()
    fake.replies = [(503, {"error": {"message": "overloaded"}})]
    with pytest.raises(AIError) as e:
        client(GEMINI_RETRIES=2).generate("s", "p")
    assert e.value.kind == "unavailable" and len(fake.requests) == 3


def test_fast_504_is_retried_once_only(fake, client):
    fake.replies = [(504, {"error": {"message": "gateway"}}), (200, envelope("fine"))]
    assert client(GEMINI_RETRIES=2).generate("s", "p") == "fine" and len(fake.requests) == 2
    fake.requests.clear()
    fake.replies = [(504, {"error": {"message": "gateway"}})]
    with pytest.raises(AIError) as e:
        client(GEMINI_RETRIES=5).generate("s", "p")
    assert e.value.kind == "timeout" and len(fake.requests) == 2


def test_client_side_timeout_is_not_retried(fake, client):
    fake.delay = 0.6
    fake.replies = [(200, envelope("late"))]
    with pytest.raises(AIError) as e:
        client(GEMINI_TIMEOUT_SECONDS=0.2, GEMINI_RETRIES=3).generate("s", "p")
    assert e.value.kind == "timeout" and len(fake.requests) == 1


def test_unreachable_server_is_a_network_error():
    with pytest.raises(AIError) as e:
        RestGeminiClient(Cfg("http://127.0.0.1:9/v1beta", GEMINI_TIMEOUT_SECONDS=2)).generate("s", "p")
    assert e.value.kind == "network" and e.value.status == 502


def test_missing_key_is_not_configured_and_sends_nothing(fake, client):
    with pytest.raises(AIError) as e:
        client(GEMINI_API_KEY="").generate("s", "p")
    assert e.value.kind == "not_configured" and fake.requests == []


def test_redirects_are_not_followed_so_the_key_cannot_be_forwarded(fake, client):
    fake.replies = [(302, b"")]
    with pytest.raises(AIError) as e:
        client(GEMINI_RETRIES=0).generate("s", "p")
    assert e.value.kind == "api" and len(fake.requests) == 1


def test_the_key_never_reaches_errors_or_logs(fake, client, caplog):
    caplog.set_level(logging.DEBUG)
    fake.replies = [(400, {"error": {"status": "INVALID_ARGUMENT", "message": f"API key not valid: {KEY}"}}), (500, {"error": {"message": f"boom {KEY}"}})]
    for _ in range(2):
        with pytest.raises(AIError) as e:
            client(GEMINI_RETRIES=0).generate("s", "p")
        assert KEY not in str(e.value) and KEY not in repr(e.value)
    assert KEY not in caplog.text


def test_remote_plain_http_and_bad_base_urls_are_refused():
    for base in ("http://example.com/v1beta", "ftp://x/y", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            RestGeminiClient(Cfg(base))
    assert RestGeminiClient(Cfg("")).base == gemini_rest.DEFAULT_BASE_URL and gemini_rest.DEFAULT_BASE_URL.startswith("https://")


# ---------------------------------------------------------------- transport selection
def test_select_llm_rest_and_sdk_are_forced():
    assert isinstance(select_llm(Cfg("", GEMINI_TRANSPORT="rest")), RestGeminiClient)
    assert select_llm(Cfg("", GEMINI_TRANSPORT="sdk")) is None


def test_auto_falls_back_to_rest_when_the_sdk_import_is_blocked(monkeypatch):
    monkeypatch.setitem(sys.modules, "google.genai", None)  # makes "import google.genai" raise ImportError
    assert isinstance(select_llm(Cfg("", GEMINI_TRANSPORT="auto")), RestGeminiClient)

    class Blocked:  # what Windows Application Control does: the import fails with an OSError-style DLL error
        def __getattr__(self, name):
            raise OSError("DLL load failed")
    monkeypatch.setattr(gemini_rest, "probe_sdk", lambda: (False, "ImportError"))
    assert isinstance(select_llm(Cfg("", GEMINI_TRANSPORT="auto")), RestGeminiClient)


def test_auto_keeps_the_sdk_when_it_imports(monkeypatch):
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.genai", types.ModuleType("google.genai"))
    assert select_llm(Cfg("", GEMINI_TRANSPORT="auto")) is None


def test_unknown_transport_value_means_auto(monkeypatch):
    monkeypatch.setattr(gemini_rest, "probe_sdk", lambda: (False, "ImportError"))
    assert isinstance(select_llm(Cfg("", GEMINI_TRANSPORT="banana")), RestGeminiClient)


# ---------------------------------------------------------------- through the real app and the unchanged patch engine
@pytest.fixture()
def rest_app(tmp_path, fake):
    app = create_app({"DATA_DIR": tmp_path / "d", "STORAGE_BACKEND": "local", "GEMINI_API_KEY": KEY, "GEMINI_TRANSPORT": "rest", "GEMINI_REST_BASE_URL": fake.url, "GEMINI_RETRIES": 0})
    return app


def test_status_endpoint_reports_rest_transport_without_the_key(rest_app):
    d = rest_app.test_client().get("/api/ai/status").get_json()
    assert d["transport"] == "rest" and d["configured"] is True and d["sdk_installed"] is True and KEY not in json.dumps(d)


def test_status_endpoint_is_untouched_for_the_sdk_transport(tmp_path):
    app = create_app({"DATA_DIR": tmp_path / "d", "STORAGE_BACKEND": "local", "GEMINI_API_KEY": KEY, "GEMINI_TRANSPORT": "sdk"})
    d = app.test_client().get("/api/ai/status").get_json()
    from autopatch.core.gemini_client import GeminiClient
    from autopatch.core.guarded_llm import GuardedLLM
    llm = app.extensions["autopatch"]["llm"]  # Backend V2: always the secret guard, here around the SDK client
    assert d["transport"] == "sdk" and isinstance(llm, GuardedLLM) and isinstance(llm._inner, GeminiClient) and KEY not in json.dumps(d)


def _setup(rest_app):
    from autopatch.core import analysis as analysis_svc
    from tests.helpers import upload_files
    c, ext = rest_app.test_client(), rest_app.extensions["autopatch"]
    pid = upload_files(c, P.FAILING)
    run = analysis_svc.start_analysis(ext["store"], ext["cfg"], pid, {"run_entrypoints": False}, background=False)
    f = next(x for x in ext["store"].find("findings", {"run_id": run["_id"]}) if x["category"] == "test_failure")
    return c, f


def test_full_workflow_over_https_proposal_accept_and_verification(rest_app, fake):
    fake.replies = [(200, envelope(proposal()))]
    c, f = _setup(rest_app)
    r = c.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
    assert r.status_code == 201 and r.get_json()["status"] == "proposed"
    sent = fake.requests[0]["body"]
    assert "test_add" in sent["contents"][0]["parts"][0]["text"] and fake.requests[0]["headers"]["x-goog-api-key"] == KEY
    pid_ = r.get_json()["_id"]
    acc = c.post(f"/api/patches/{pid_}/accept", json={})
    assert acc.status_code == 200 and acc.get_json()["status"] == "applied" and acc.get_json()["verification"]["verdict"] == "target_resolved"


def test_validation_still_rejects_bad_proposals_that_arrive_over_https(rest_app, fake):
    c, f = _setup(rest_app)
    fake.replies = [(200, envelope(proposal(TEST_EDIT)))]
    r = c.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
    assert r.status_code == 422 and "rejected by validation" in r.get_json()["error"]
    fake.replies = [(200, envelope("this is not json at all"))]
    assert c.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True}).status_code == 422
    hist = c.get(f"/api/findings/{f['_id']}/patches").get_json()["patches"]
    assert [h["status"] for h in hist] == ["invalid", "invalid"]


def test_gemini_errors_surface_with_the_same_http_status_through_the_api(rest_app, fake):
    c, f = _setup(rest_app)
    for status, body, want in [(429, {"error": {"message": "quota"}}, 429), (403, {"error": {"message": "no"}}, 502), (503, {"error": {"message": "busy"}}, 503)]:
        fake.replies = [(status, body)]
        r = c.post(f"/api/findings/{f['_id']}/patches", json={"confirm_send": True})
        assert r.status_code == want and KEY not in r.get_data(as_text=True)


def test_consent_is_still_required_before_anything_is_sent(rest_app, fake):
    c, f = _setup(rest_app)
    assert c.post(f"/api/findings/{f['_id']}/patches", json={}).status_code == 400 and fake.requests == []
