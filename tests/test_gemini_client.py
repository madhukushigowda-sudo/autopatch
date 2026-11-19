"""Gemini wrapper: configuration errors and failure mapping. No network, no real SDK, no paid calls."""
import sys
import types

import pytest

from autopatch.core.gemini_client import AIError, GeminiClient, ai_status, classify, redact
from config import Config

KEY = "AIza-super-secret-key-123"


def cfg(**kw):
    # GEMINI_THINKING_LEVEL is pinned to "" so a developer's .env cannot change what these tests see.
    return Config(**{"GEMINI_API_KEY": KEY, "GEMINI_THINKING_LEVEL": "", **kw})


def install_fake_sdk(monkeypatch, behaviour):
    """Install a fake `google.genai` whose generate_content runs `behaviour(model, contents, config)`."""
    calls = []

    class Models:
        def generate_content(self, model, contents, config=None):
            calls.append({"model": model, "contents": contents, "config": config})
            return behaviour(model, contents, config)

    class Client:
        def __init__(self, api_key=None, http_options=None):
            self.api_key, self.http_options, self.models = api_key, http_options, Models()

    ns = lambda **kw: types.SimpleNamespace(**kw)  # noqa: E731
    genai = types.ModuleType("google.genai")
    genai.Client = Client
    t = types.ModuleType("google.genai.types")
    t.HttpOptions = lambda timeout=None: ns(timeout=timeout)
    t.GenerateContentConfig = lambda **kw: ns(**kw)
    t.ThinkingConfig = lambda **kw: ns(**kw)
    genai.types = t
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", t)
    return calls


class Boom(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


def test_status_never_contains_the_key():
    st = ai_status(cfg())
    assert st["configured"] is True and KEY not in str(st)
    assert ai_status(Config(GEMINI_API_KEY=""))["configured"] is False


def test_missing_key_is_a_clear_error_and_no_sdk_call(monkeypatch):
    calls = install_fake_sdk(monkeypatch, lambda *a: None)
    with pytest.raises(AIError) as e:
        GeminiClient(Config(GEMINI_API_KEY="")).generate("s", "p")
    assert e.value.kind == "not_configured" and e.value.status == 503 and calls == []


def test_sdk_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "google", None)  # makes `from google import genai` raise ImportError
    with pytest.raises(AIError) as e:
        GeminiClient(cfg()).generate("s", "p")
    assert e.value.kind == "sdk_missing"


def test_successful_call_uses_configured_model_and_json_mode(monkeypatch):
    calls = install_fake_sdk(monkeypatch, lambda *a: types.SimpleNamespace(text='{"ok": true}'))
    out = GeminiClient(cfg(GEMINI_MODEL="my-model", GEMINI_TIMEOUT_SECONDS=12)).generate("system text", "prompt text")
    assert out == '{"ok": true}'
    c = calls[0]
    assert c["model"] == "my-model" and c["contents"] == "prompt text"
    assert c["config"].response_mime_type == "application/json" and c["config"].system_instruction == "system text"


@pytest.mark.parametrize("exc,kind,status", [
    (Boom("API key not valid. Please pass a valid API key.", 400), "invalid_key", 502),
    (Boom("forbidden", 403), "invalid_key", 502),
    (Boom("RESOURCE_EXHAUSTED quota", 429), "quota", 429),
    (Boom("model not found", 404), "model", 502),
    (Boom("boom", 500), "api", 502),
    (TimeoutError("timed out"), "timeout", 504),
    (ConnectionError("no route"), "network", 502),
    (RuntimeError("weird"), "api", 502),
])
def test_failures_are_mapped_and_never_leak_the_key(monkeypatch, exc, kind, status):
    def behaviour(*a):
        raise type(exc)(f"{exc} {KEY}", *( [exc.code] if hasattr(exc, "code") else [] ))
    install_fake_sdk(monkeypatch, behaviour)
    with pytest.raises(AIError) as e:
        GeminiClient(cfg()).generate("s", "p")
    assert (e.value.kind, e.value.status) == (kind, status)
    assert KEY not in str(e.value)


@pytest.mark.parametrize("text", [None, "", "   "])
def test_empty_or_blocked_response(monkeypatch, text):
    install_fake_sdk(monkeypatch, lambda *a: types.SimpleNamespace(text=text))
    with pytest.raises(AIError) as e:
        GeminiClient(cfg()).generate("s", "p")
    assert e.value.kind == "blocked"


def test_redact_and_classify_helpers():
    assert KEY not in redact(f"bad key {KEY}", KEY)
    assert classify(Boom("x", 429), KEY).kind == "quota"


def test_gemini3_models_default_to_low_thinking_and_other_models_send_nothing(monkeypatch):
    calls = install_fake_sdk(monkeypatch, lambda *a: types.SimpleNamespace(text="{}"))
    GeminiClient(cfg(GEMINI_MODEL="gemini-3.6-flash")).generate("s", "p")
    assert calls[-1]["config"].thinking_config.thinking_level == "low"
    GeminiClient(cfg(GEMINI_MODEL="gemini-2.5-flash")).generate("s", "p")
    assert not hasattr(calls[-1]["config"], "thinking_config")
    GeminiClient(cfg(GEMINI_MODEL="gemini-3.6-flash", GEMINI_THINKING_LEVEL="high")).generate("s", "p")
    assert calls[-1]["config"].thinking_config.thinking_level == "high"
    GeminiClient(cfg(GEMINI_MODEL="gemini-3.6-flash", GEMINI_THINKING_LEVEL="off")).generate("s", "p")
    assert not hasattr(calls[-1]["config"], "thinking_config")
    assert ai_status(cfg(GEMINI_MODEL="gemini-3.6-flash"))["thinking_level"] == "low"


def test_explicit_thinking_level_is_passed_even_for_a_non_gemini3_model(monkeypatch):
    calls = install_fake_sdk(monkeypatch, lambda *a: types.SimpleNamespace(text="{}"))
    GeminiClient(cfg(GEMINI_MODEL="gemini-2.5-flash", GEMINI_THINKING_LEVEL="low")).generate("s", "p")
    assert calls[-1]["config"].thinking_config.thinking_level == "low"


def test_timeout_default_is_long_enough_for_thinking_models():
    assert Config().GEMINI_TIMEOUT_SECONDS >= 180


def fake_clock(monkeypatch):
    """Deterministic elapsed time: the fake SDK calls clock.tick(seconds) to 'spend' time inside a request."""
    clock = types.SimpleNamespace(now=0.0)
    clock.tick = lambda s: setattr(clock, "now", clock.now + s)
    monkeypatch.setattr("autopatch.core.gemini_client._clock", lambda: clock.now)
    return clock


def test_fast_504_is_retried_once_then_succeeds(monkeypatch):
    clock = fake_clock(monkeypatch)
    state = {"n": 0}

    def behaviour(*a):
        state["n"] += 1
        clock.tick(2)
        if state["n"] == 1:
            raise Boom("gateway", 504)
        return types.SimpleNamespace(text='{"ok": 1}')
    calls = install_fake_sdk(monkeypatch, behaviour)
    assert GeminiClient(cfg(GEMINI_TIMEOUT_SECONDS=180)).generate("s", "p") == '{"ok": 1}' and len(calls) == 2


def test_fast_504_is_retried_at_most_once(monkeypatch):
    clock = fake_clock(monkeypatch)
    calls = install_fake_sdk(monkeypatch, lambda *a: clock.tick(1) or (_ for _ in ()).throw(Boom("gateway", 504)))
    with pytest.raises(AIError) as e:
        GeminiClient(cfg(GEMINI_TIMEOUT_SECONDS=180, GEMINI_RETRIES=2)).generate("s", "p")
    assert e.value.kind == "timeout" and e.value.status == 504 and len(calls) == 2


def test_slow_504_is_not_retried(monkeypatch):
    clock = fake_clock(monkeypatch)
    calls = install_fake_sdk(monkeypatch, lambda *a: clock.tick(60) or (_ for _ in ()).throw(Boom("deadline", 504)))   # exactly 1/3
    with pytest.raises(AIError) as e:
        GeminiClient(cfg(GEMINI_TIMEOUT_SECONDS=180)).generate("s", "p")
    assert e.value.kind == "timeout" and len(calls) == 1
    calls = install_fake_sdk(monkeypatch, lambda *a: clock.tick(179) or (_ for _ in ()).throw(Boom("deadline", 504)))
    with pytest.raises(AIError):
        GeminiClient(cfg(GEMINI_TIMEOUT_SECONDS=180)).generate("s", "p")
    assert len(calls) == 1


def test_client_side_timeouts_are_never_retried_even_when_fast(monkeypatch):
    clock = fake_clock(monkeypatch)
    for exc in (TimeoutError("timed out"), Boom("read timed out"), Boom("deadline", 408)):
        calls = install_fake_sdk(monkeypatch, lambda *a, exc=exc: clock.tick(1) or (_ for _ in ()).throw(exc))
        with pytest.raises(AIError) as e:
            GeminiClient(cfg(GEMINI_TIMEOUT_SECONDS=180)).generate("s", "p")
        assert e.value.kind == "timeout" and len(calls) == 1


def test_504_retry_respects_the_attempt_limit_and_503_behaviour_is_unchanged(monkeypatch):
    monkeypatch.setattr("autopatch.core.gemini_client.time.sleep", lambda s: None)
    clock = fake_clock(monkeypatch)
    calls = install_fake_sdk(monkeypatch, lambda *a: clock.tick(1) or (_ for _ in ()).throw(Boom("gateway", 504)))
    with pytest.raises(AIError):
        GeminiClient(cfg(GEMINI_RETRIES=0)).generate("s", "p")                    # retries disabled -> no 504 retry
    assert len(calls) == 1
    seq = iter([Boom("gateway", 504), Boom("overloaded", 503), Boom("overloaded", 503), None])

    def behaviour(*a):
        clock.tick(1)
        nxt = next(seq)
        if nxt is None:
            return types.SimpleNamespace(text='{"ok": 1}')
        raise nxt
    calls = install_fake_sdk(monkeypatch, behaviour)
    assert GeminiClient(cfg(GEMINI_RETRIES=3)).generate("s", "p") == '{"ok": 1}' and len(calls) == 4


def test_attempt_logs_show_elapsed_time_but_no_key_or_prompt(monkeypatch, caplog):
    import logging
    clock = fake_clock(monkeypatch)
    calls = install_fake_sdk(monkeypatch, lambda *a: clock.tick(3.5) or (_ for _ in ()).throw(Boom(f"bad {KEY}", 504)))
    with caplog.at_level(logging.INFO, logger="autopatch.core.gemini_client"), pytest.raises(AIError):
        GeminiClient(cfg(GEMINI_TIMEOUT_SECONDS=180, GEMINI_RETRIES=1)).generate("system text", "SECRET-PROMPT-BODY")
    text = caplog.text
    assert "timeout after 3.5 s (attempt 1/2)" in text and "timeout after 3.5 s (attempt 2/2)" in text and len(calls) == 2
    assert KEY not in text and "SECRET-PROMPT-BODY" not in text and "system text" not in text


def test_503_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr("autopatch.core.gemini_client.time.sleep", lambda s: None)
    state = {"n": 0}

    def behaviour(*a):
        state["n"] += 1
        if state["n"] < 3:
            raise Boom("overloaded", 503)
        return types.SimpleNamespace(text='{"ok": 1}')
    calls = install_fake_sdk(monkeypatch, behaviour)
    assert GeminiClient(cfg(GEMINI_RETRIES=2)).generate("s", "p") == '{"ok": 1}' and len(calls) == 3


def test_503_gives_up_after_the_configured_retries_and_timeouts_are_not_retried(monkeypatch):
    monkeypatch.setattr("autopatch.core.gemini_client.time.sleep", lambda s: None)
    calls = install_fake_sdk(monkeypatch, lambda *a: (_ for _ in ()).throw(Boom("overloaded", 503)))
    with pytest.raises(AIError) as e:
        GeminiClient(cfg(GEMINI_RETRIES=1)).generate("s", "p")
    assert e.value.kind == "unavailable" and e.value.status == 503 and len(calls) == 2
    calls.clear()
    clock = fake_clock(monkeypatch)
    install_fake_sdk(monkeypatch, lambda *a: clock.tick(180) or (_ for _ in ()).throw(Boom("deadline", 504)))   # slow 504
    with pytest.raises(AIError) as e2:
        GeminiClient(cfg()).generate("s", "p")
    assert e2.value.kind == "timeout" and "GEMINI_THINKING_LEVEL" in str(e2.value)
