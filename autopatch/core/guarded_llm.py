"""Wraps the active Gemini client (SDK or HTTPS) with two checks that run before anything leaves the machine:

1. The API key must be usable (see keycheck): a blank/placeholder key gets a specific message, no request is made.
2. The whole outgoing text (system + prompt) is scanned for secrets. If any are found the request is refused.
   The error names the kinds and counts and the section of the prompt, never the secret itself.

The protected patch code asks ``ext["llm"]`` for its client, so wrapping it here needs no change to protected files.
"""
from __future__ import annotations

import logging
import re
from collections import Counter

from .gemini_client import AIError
from .secret_scan import scan_text

log = logging.getLogger(__name__)
_SECTION = re.compile(r"^###? (?:FILE: )?(.{1,200}?)(?: \(.*)?$")


class SecretsDetected(AIError):
    """Raised before transmission when the outgoing text contains something that looks like a credential."""

    def __init__(self, summary: str, kinds: dict):
        super().__init__("api", summary)
        self.kind = "secrets_detected"
        self.status = 422
        self.kinds = dict(kinds)


def scan_outgoing(system: str, prompt: str) -> list[tuple[str, str]]:
    """[(kind, section)] for each secret-like line. No values are returned."""
    hits: list[tuple[str, str]] = []
    for label, text in (("the instructions", system or ""), ("the request", prompt or "")):
        section = label
        # scan per line so each hit can be tied to the nearest preceding "### FILE: path" heading
        for line in text.splitlines():
            m = _SECTION.match(line)
            if m:
                section = m.group(1).strip()[:120]
                continue
            for f in scan_text(line):
                hits.append((f.kind, section))
    # multi-line constructs (private key blocks) are single-line headers, so per-line scanning covers them
    return hits


def describe(hits: list[tuple[str, str]]) -> str:
    kinds = Counter(k for k, _ in hits)
    places = list(dict.fromkeys(s for _, s in hits))[:5]
    what = ", ".join(f"{n} x {k}" for k, n in kinds.most_common(5))
    return (f"Not sent to Gemini: the code to be repaired appears to contain a secret ({what}) in: {', '.join(places)}. "
            "Move the secret into an environment variable or remove it, then try again. If it is a harmless test value, add "
            "'# autopatch: allow-secret' to that line.")


class GuardedLLM:
    def __init__(self, inner, cfg):
        self._inner, self._cfg = inner, cfg
        self.transport = getattr(inner, "transport", "sdk")

    def generate(self, system: str, prompt: str) -> str:
        message = getattr(self._cfg, "GEMINI_KEY_MESSAGE", None)
        if message:
            raise AIError("not_configured", message)
        hits = scan_outgoing(system, prompt)
        if hits:
            log.warning("Refused to send to Gemini: %d secret-like value(s) found (%s)",
                        len(hits), ", ".join(sorted({k for k, _ in hits})))  # kinds only
            raise SecretsDetected(describe(hits), Counter(k for k, _ in hits))
        return self._inner.generate(system, prompt)
