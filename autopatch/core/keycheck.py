"""Check the Gemini API key's shape before it is ever used. Pure string checks: nothing is sent anywhere and the key is never returned.

A key that is blank, a placeholder copied from .env.example, wrapped in quotes or containing spaces can never work, so
the person gets a specific instruction instead of Google's generic "key not valid" after a network round trip.
A format that merely looks unusual is reported as a note only, because Google may change key formats.
"""
from __future__ import annotations

import re

_GOOGLE_KEY = re.compile(r"^AIza[0-9A-Za-z_\-]{35}$")
_PLACEHOLDER_WORDS = ("your", "here", "xxx", "changeme", "change-me", "change_me", "example", "placeholder", "paste", "insert",
                      "replace", "todo", "api-key", "api_key", "apikey", "<", ">", "{", "}")

MESSAGES = {
    "empty": "GEMINI_API_KEY is empty. Create a key in Google AI Studio, put it in your .env file and restart AutoPatch.",
    "placeholder": ("GEMINI_API_KEY is still the placeholder text from .env.example. Replace it with a real key from "
                    "Google AI Studio and restart AutoPatch."),
    "quoted": "GEMINI_API_KEY is wrapped in quotation marks. Remove the quotes in your .env file so the line is KEY=value.",
    "whitespace": "GEMINI_API_KEY contains a space or line break. Paste the key on one line with nothing around it.",
    "non_ascii": "GEMINI_API_KEY contains characters that are not allowed in an API key (for example a smart quote or an accented letter). Copy the key again.",
}
NOTES = {
    "unusual_format": "The key does not look like a typical Google AI Studio key (they start with 'AIza' and are 39 characters). "
                      "AutoPatch will still try it.",
}


def check_key(value) -> dict:
    """Return {"ok": bool, "problem": code|None, "message": str|None, "note": str|None}. Never includes the key."""
    key = value if isinstance(value, str) else ""
    stripped = key.strip()
    if not stripped:
        return _bad("empty")
    if _GOOGLE_KEY.match(stripped):
        return {"ok": True, "problem": None, "message": None, "note": None}
    if stripped[0] in "\"'`" or stripped[-1] in "\"'`":
        return _bad("quoted")
    if re.search(r"\s", stripped):
        return _bad("whitespace")
    if not stripped.isascii():
        return _bad("non_ascii")
    low = stripped.lower()
    if any(w in low for w in _PLACEHOLDER_WORDS):
        return _bad("placeholder")
    return {"ok": True, "problem": None, "message": None, "note": NOTES["unusual_format"] if not stripped.startswith("AIza") else None}


def _bad(code: str) -> dict:
    return {"ok": False, "problem": code, "message": MESSAGES[code], "note": None}


def apply_to_config(cfg) -> dict:
    """Record the verdict on cfg. A key that can never work is blanked so no request is attempted with it."""
    result = check_key(getattr(cfg, "GEMINI_API_KEY", ""))
    cfg.GEMINI_KEY_PROBLEM = result["problem"]
    cfg.GEMINI_KEY_MESSAGE = result["message"]
    cfg.GEMINI_KEY_NOTE = result["note"]
    if not result["ok"]:
        cfg.GEMINI_API_KEY = ""
    return result
