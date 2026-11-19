"""Request-level hardening for a tool meant to run on the person's own computer.

* Host check: only localhost names are served, on every request. Without it a web page on another site can use DNS
  rebinding to make the browser talk to this server under the attacker's hostname and read the answers.
  Localhost, 127.0.0.1, [::1] and *.localhost on any port are accepted, plus anything in ALLOWED_HOSTS.
* Security headers and a Content-Security-Policy. The policy is built from the templates: the only inline script is
  allowed by its SHA-256 hash, so no template had to change and no 'unsafe-inline' for scripts is needed.

This is defence in depth for local use. It is not authentication: anyone who can reach the port on 127.0.0.1 can use it.
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc\s*=)([^>]*)>(.*?)</script>", re.I | re.S)
_HOST_NAME = re.compile(r"^[a-z0-9.\-]+$")
LOCAL_NAMES = {"localhost", "127.0.0.1", "::1"}


def split_host(raw: str) -> str | None:
    """Host header -> lower-case host name without port, or None when malformed. IPv6 literals are returned without brackets."""
    raw = (raw or "").strip().lower()
    if not raw or len(raw) > 255:
        return None
    if raw.startswith("["):
        end = raw.find("]")
        if end == -1:
            return None
        host, rest = raw[1:end], raw[end + 1:]
        if rest and not re.fullmatch(r":\d{1,5}", rest):
            return None
        return host if re.fullmatch(r"[0-9a-f:.]+", host) else None
    host, _, port = raw.partition(":")
    if port and not re.fullmatch(r"\d{1,5}", port):
        return None
    host = host.rstrip(".")
    return host if _HOST_NAME.match(host) else None


def host_allowed(raw: str, extra=()) -> bool:
    host = split_host(raw)
    if host is None:
        return False
    if host in LOCAL_NAMES or host.endswith(".localhost"):
        return True
    return host in {e.lower() for e in extra}


def inline_script_hashes(template_dir: Path) -> list[str]:
    """CSP source tokens ('sha256-...') for every inline <script> in the templates. Templates must not template these blocks."""
    out: list[str] = []
    for path in sorted(Path(template_dir).rglob("*.html")):
        for attrs, body in _INLINE_SCRIPT.findall(path.read_text(encoding="utf-8")):
            if "{{" in body or "{%" in body:
                raise RuntimeError(f"{path.name}: an inline <script> contains template syntax, so it cannot be hashed for the CSP")
            if "type" in attrs.lower() and "json" in attrs.lower():
                continue  # data block, never executed
            token = "'sha256-" + base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode() + "'"
            if token not in out:
                out.append(token)
    return out


def build_csp(template_dir: Path) -> str:
    scripts = " ".join(["'self'"] + inline_script_hashes(template_dir))
    return "; ".join([
        "default-src 'self'",
        f"script-src {scripts}",
        "style-src 'self' 'unsafe-inline'",  # the pages set inline style attributes; scripts stay locked down
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


def security_headers(csp: str) -> dict:
    return {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }
