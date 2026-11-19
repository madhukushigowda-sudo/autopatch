"""Thin wrapper around Google's official Gen AI SDK (`google-genai`).

* The SDK is imported lazily, so AutoPatch (and ordinary scanning) works without it and without a key.
* The API key is read from config (environment / .env) only. It is never logged and is scrubbed from every
  error message before it can reach an API response.
* Every failure becomes an :class:`AIError` with a ``kind`` the API maps to a clear HTTP status.
* Nothing returned by the model is trusted here: this module only fetches text.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

# kind -> (HTTP status for the API, user-facing hint)
ERROR_KINDS = {
    "not_configured": (503, "Add GEMINI_API_KEY to your .env file and restart AutoPatch."),
    "sdk_missing": (503, "The Gemini SDK is not installed. Run: pip install google-genai"),
    "invalid_key": (502, "Gemini rejected the API key. Check GEMINI_API_KEY in your .env file."),
    "quota": (429, "Gemini quota or rate limit reached. Wait a moment and try again, or check your plan."),
    "network": (502, "Could not reach the Gemini API. Check your internet connection."),
    "unavailable": (503, "Gemini is temporarily overloaded (HTTP 503). Try again in a moment."),
    "timeout": (504, "The Gemini request timed out. Try again."),
    "model": (502, "Gemini does not recognise the configured model. Check GEMINI_MODEL."),
    "blocked": (502, "Gemini returned no usable answer (empty or blocked response)."),
    "api": (502, "The Gemini API returned an error."),
}


class AIError(Exception):
    def __init__(self, kind: str, message: str | None = None):
        self.kind = kind if kind in ERROR_KINDS else "api"
        self.status, hint = ERROR_KINDS[self.kind]
        super().__init__(message or hint)


def sdk_installed() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("google.genai") is not None
    except (ImportError, ValueError):
        return False


def thinking_level(cfg) -> str | None:
    """Explicit setting wins ("off" disables); otherwise "low" for Gemini 3 models, nothing for others."""
    level = (cfg.GEMINI_THINKING_LEVEL or "").lower()
    if level in ("off", "none", "default"):
        return None
    if level:
        return level
    return "low" if cfg.GEMINI_MODEL.lower().startswith("gemini-3") else None


def ai_status(cfg) -> dict:
    """What the UI may know: never the key itself."""
    return {"configured": bool(cfg.GEMINI_API_KEY), "model": cfg.GEMINI_MODEL, "sdk_installed": sdk_installed(),
            "timeout_seconds": cfg.GEMINI_TIMEOUT_SECONDS, "thinking_level": thinking_level(cfg)}


def _clock() -> float:
    return time.monotonic()


def _is_gateway_timeout(exc: Exception) -> bool:
    """True only for an HTTP 504 *response*. A client-side timeout carries no status code and is never retried."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return isinstance(code, int) and code == 504


def redact(text: str, secret: str) -> str:
    text = str(text)
    if secret and len(secret) >= 4:
        text = text.replace(secret, "[redacted]")
    return text[:300]


def classify(exc: Exception, key: str) -> AIError:
    """Map an SDK / network exception to an AIError without leaking the key."""
    name = type(exc).__name__.lower()
    msg = redact(exc, key)
    low = msg.lower()
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int):
        if code in (401, 403) or "api key" in low and ("invalid" in low or "not valid" in low):
            return AIError("invalid_key")
        if code == 400 and "api key" in low:
            return AIError("invalid_key")
        if code == 429 or "quota" in low or "resource_exhausted" in low:
            return AIError("quota")
        if code == 404:
            return AIError("model")
        if code in (408, 504):
            return AIError("timeout", "Gemini did not finish within the time limit (the model may be thinking for a long time). "
                           "Try again, raise GEMINI_TIMEOUT_SECONDS, or set GEMINI_THINKING_LEVEL=low in .env.")
        if code == 503:
            return AIError("unavailable")
        return AIError("api", f"The Gemini API returned an error (HTTP {code}).")
    if "timeout" in name or "timed out" in low or isinstance(exc, TimeoutError):
        return AIError("timeout")
    if isinstance(exc, (ConnectionError, OSError)) or any(t in name for t in ("connect", "network", "transport", "dns")):
        return AIError("network")
    return AIError("api", "The Gemini request failed.")


class GeminiClient:
    """Callable LLM: ``generate(system, prompt) -> str``. Tests substitute a fake with the same method."""

    def __init__(self, cfg):
        self.cfg = cfg

    def generate(self, system: str, prompt: str) -> str:
        key = self.cfg.GEMINI_API_KEY
        if not key:
            raise AIError("not_configured")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise AIError("sdk_missing") from exc
        level = thinking_level(self.cfg)
        attempts = 1 + max(0, int(self.cfg.GEMINI_RETRIES))
        text = None
        fast_504_retried = False
        for attempt in range(1, attempts + 1):
            started = _clock()
            try:
                client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=int(self.cfg.GEMINI_TIMEOUT_SECONDS * 1000)))
                options = dict(system_instruction=system, temperature=self.cfg.GEMINI_TEMPERATURE,
                               max_output_tokens=self.cfg.GEMINI_MAX_OUTPUT_TOKENS, response_mime_type="application/json")
                if level:
                    options["thinking_config"] = types.ThinkingConfig(thinking_level=level)
                response = client.models.generate_content(model=self.cfg.GEMINI_MODEL, contents=prompt,
                                                          config=types.GenerateContentConfig(**options))
                text = getattr(response, "text", None)
                log.info("Gemini call finished in %.1f s (attempt %d/%d)", _clock() - started, attempt, attempts)
                break
            except AIError:
                raise
            except Exception as exc:  # SDK/network errors vary by version; classify, never leak
                err = classify(exc, key)
                elapsed = _clock() - started
                # kind and elapsed time only: no message, no prompt, no key
                log.warning("Gemini call failed: %s after %.1f s (attempt %d/%d)", err.kind, elapsed, attempt, attempts)
                if attempt < attempts:
                    if err.kind == "unavailable":  # transient overload: short back-off, then retry
                        time.sleep(2 * attempt)
                        continue
                    # A 504 that came back quickly (< 1/3 of the timeout) is a gateway hiccup: retry once. A slow 504 means the
                    # deadline expired, and a client-side timeout has no status code; neither is retried.
                    if (not fast_504_retried and _is_gateway_timeout(exc)
                            and elapsed < float(self.cfg.GEMINI_TIMEOUT_SECONDS) / 3):
                        fast_504_retried = True
                        continue
                raise err from None
        if not text or not str(text).strip():
            raise AIError("blocked")
        return str(text)
