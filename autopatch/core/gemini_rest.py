"""Gemini over plain HTTPS (Python standard library only).

Why this exists: Google's ``google-genai`` SDK needs Pydantic Core, a compiled library that some Windows
Application Control policies block. This client sends the same request straight to Google's official Gemini
REST API (``models/<model>:generateContent``) and needs no third-party package at all.

It is a drop-in for :class:`gemini_client.GeminiClient`: same ``generate(system, prompt) -> str`` method, same
:class:`AIError` kinds, same settings (model, timeout, retries, temperature, token limit, thinking level).
It only fetches text. Everything the model returns is still validated by the unchanged patch engine.

* The API key travels in the ``x-goog-api-key`` header, never in the URL, and is scrubbed from every error.
* HTTPS is required (certificates verified with the operating system's trust store). Redirects are not followed.
* Nothing is logged except error kind and elapsed time.
"""
from __future__ import annotations

import json
import re
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from .gemini_client import AIError, _clock, _is_gateway_timeout, classify, redact, thinking_level

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class _HttpError(Exception):
    """An HTTP error response from the API, shaped so ``classify`` can read ``.code``."""

    def __init__(self, code: int, message: str, reason: str = ""):
        super().__init__(message)
        self.code = code
        self.reason = reason


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):  # a redirect would forward the API key header elsewhere
        return None


def _base_url(cfg) -> str:
    base = (getattr(cfg, "GEMINI_REST_BASE_URL", "") or DEFAULT_BASE_URL).rstrip("/")
    parts = urllib.parse.urlsplit(base)
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS):
        raise ValueError("GEMINI_REST_BASE_URL must use https (plain http is allowed only for localhost).")
    return base


def _google_reason(body: bytes) -> str:
    """Google's error status and reason codes (for example 'INVALID_ARGUMENT API_KEY_INVALID'). Upper-case words only,
    so free text, and therefore anything resembling a key, cannot pass through."""
    try:
        err = json.loads(body.decode("utf-8", "replace")).get("error") or {}
        words = [str(err.get("status", ""))] + [str(d.get("reason", "")) for d in (err.get("details") or []) if isinstance(d, dict)]
    except (ValueError, AttributeError):
        return ""
    return " ".join(w for w in words if re.fullmatch(r"[A-Z][A-Z0-9_]{2,60}", w))[:120]


def _error_message(body: bytes) -> str:
    try:
        err = json.loads(body.decode("utf-8", "replace")).get("error") or {}
        return " ".join(str(err.get(k, "")) for k in ("status", "message")).strip()
    except (ValueError, AttributeError):
        return ""


def extract_text(data: dict) -> str:
    """Concatenate the answer parts, skipping the model's internal thought parts."""
    out = []
    for cand in (data.get("candidates") or [])[:1]:
        for part in ((cand.get("content") or {}).get("parts") or []):
            if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought"):
                out.append(part["text"])
    return "".join(out)


class RestGeminiClient:
    """Callable LLM: ``generate(system, prompt) -> str``."""

    transport = "rest"

    def __init__(self, cfg):
        self.cfg = cfg
        self.base = _base_url(cfg)
        self._opener = urllib.request.build_opener(_NoRedirect)  # honours the system proxy settings

    # -- request ------------------------------------------------------------------------------------------
    def _url(self) -> str:
        model = urllib.parse.quote(self.cfg.GEMINI_MODEL.strip().removeprefix("models/"), safe="-._~")
        return f"{self.base}/models/{model}:generateContent"

    def _body(self, system: str, prompt: str, level: str | None) -> bytes:
        gen = {"temperature": self.cfg.GEMINI_TEMPERATURE, "maxOutputTokens": int(self.cfg.GEMINI_MAX_OUTPUT_TOKENS), "responseMimeType": "application/json"}
        if level:
            gen["thinkingConfig"] = {"thinkingLevel": level}
        return json.dumps({"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                           "generationConfig": gen}).encode("utf-8")

    def _post(self, body: bytes) -> dict:
        req = urllib.request.Request(self._url(), data=body, method="POST", headers={
            "Content-Type": "application/json", "x-goog-api-key": self.cfg.GEMINI_API_KEY, "Accept": "application/json", "User-Agent": "AutoPatch-rest/1"})
        try:
            with self._opener.open(req, timeout=float(self.cfg.GEMINI_TIMEOUT_SECONDS)) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:  # also covers un-followed redirects (3xx)
            body = exc.read(65536)
            raise _HttpError(exc.code, _error_message(body), _google_reason(body)) from None
        except urllib.error.URLError as exc:
            reason = exc.reason if isinstance(exc.reason, Exception) else exc
            raise reason from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AIError("api", "The Gemini response was unexpectedly large and was discarded.")
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise AIError("api", "The Gemini API returned something that is not JSON.") from None
        if not isinstance(data, dict):
            raise AIError("api", "The Gemini API returned an unexpected response.")
        return data

    # -- public -------------------------------------------------------------------------------------------
    def generate(self, system: str, prompt: str) -> str:
        key = self.cfg.GEMINI_API_KEY
        if not key:
            raise AIError("not_configured")
        level = thinking_level(self.cfg)
        attempts = 1 + max(0, int(self.cfg.GEMINI_RETRIES))
        fast_504_retried = thinking_dropped = False
        text = None
        attempt = 0
        while attempt < attempts:
            attempt += 1
            started = _clock()
            try:
                data = self._post(self._body(system, prompt, level))
                text = extract_text(data)
                log.info("Gemini (REST) call finished in %.1f s (attempt %d/%d)", _clock() - started, attempt, attempts)
                break
            except AIError:
                raise
            except Exception as exc:
                err = classify(exc, key)
                elapsed = _clock() - started
                log.warning("Gemini (REST) call failed: %s after %.1f s (attempt %d/%d)", err.kind, elapsed, attempt, attempts)
                # An older/newer model may not accept the thinking setting: retry once without it (not counted as an attempt).
                if level and not thinking_dropped and getattr(exc, "code", None) == 400 and "thinking" in redact(exc, key).lower():
                    thinking_dropped, level = True, None
                    attempt -= 1
                    continue
                if attempt < attempts:
                    if err.kind == "unavailable":
                        time.sleep(2 * attempt)
                        continue
                    if not fast_504_retried and _is_gateway_timeout(exc) and elapsed < float(self.cfg.GEMINI_TIMEOUT_SECONDS) / 3:
                        fast_504_retried = True
                        continue
                reason = getattr(exc, "reason", "")
                if reason and err.kind in ("invalid_key", "api", "model", "quota"):
                    err = AIError(err.kind, f"{err} (Google reported: {reason})")
                raise err from None
        if not text or not text.strip():
            raise AIError("blocked")
        return text


def probe_sdk() -> tuple[bool, str]:
    """Really import the SDK (``find_spec`` cannot see a package that is installed but blocked)."""
    try:
        import google.genai  # noqa: F401
        return True, ""
    except Exception as exc:  # ImportError, or OSError from a blocked DLL
        return False, type(exc).__name__


def select_llm(cfg):
    """Pick the transport. Returns a REST client, or ``None`` to keep the SDK client. Never raises for a missing SDK."""
    choice = (getattr(cfg, "GEMINI_TRANSPORT", "auto") or "auto").strip().lower()
    if choice not in ("auto", "sdk", "rest"):
        log.warning("Unknown GEMINI_TRANSPORT %r; using auto", choice)
        choice = "auto"
    if choice == "sdk":
        return None
    if choice == "auto":
        ok, why = probe_sdk()
        if ok:
            return None
        log.warning("Gemini SDK cannot be used (%s); using the built-in HTTPS client instead", why)
    return RestGeminiClient(cfg)
