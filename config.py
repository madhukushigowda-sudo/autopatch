"""Central configuration.

Every value can be overridden with an environment variable (or a .env file).
Secrets (MONGODB_URI, later GEMINI_API_KEY) are only ever read from the
environment and are never written to code, logs or API responses.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

try:  # python-dotenv is optional at runtime; .env is a convenience on Windows.
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # pragma: no cover
    pass


def _num(name: str, default, cast=int):
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return cast(raw)
    except ValueError:
        raise RuntimeError(f"Environment variable {name} must be a number, got {raw!r}") from None


class Config:
    """Plain config object; tests pass overrides as keyword arguments."""

    def __init__(self, **overrides):
        env = os.environ
        self.SECRET_KEY = env.get("SECRET_KEY", "dev-only-change-me")
        self.BASE_DIR = BASE_DIR
        self.DATA_DIR = Path(env.get("DATA_DIR", BASE_DIR / "data")).resolve()

        # Storage: "auto" = try MongoDB, fall back to local JSON; "mongo"; "local".
        self.STORAGE_BACKEND = env.get("STORAGE_BACKEND", "auto").lower()
        self.MONGODB_URI = env.get("MONGODB_URI", "")
        self.MONGODB_DB = env.get("MONGODB_DB", "autopatch")
        self.MONGODB_TIMEOUT_MS = _num("MONGODB_TIMEOUT_MS", 3000)

        # Upload limits (all enforced server-side).
        self.MAX_UPLOAD_MB = _num("MAX_UPLOAD_MB", 10)  # whole HTTP request
        self.MAX_UNCOMPRESSED_MB = _num("MAX_UNCOMPRESSED_MB", 50)  # all extracted files
        self.MAX_FILES = _num("MAX_FILES", 500)  # files kept after filtering
        self.MAX_ENTRIES = _num("MAX_ENTRIES", 5000)  # raw ZIP entries inspected
        self.MAX_FILE_MB = _num("MAX_FILE_MB", 2, float)  # single file

        # Analysis (Stage 2). Executing project code is only allowed from this machine (see api/analysis.py).
        self.ALLOW_EXECUTION = env.get("ALLOW_EXECUTION", "true").lower() not in ("0", "false", "no", "off")
        self.PYTEST_TIMEOUT_SECONDS = _num("PYTEST_TIMEOUT_SECONDS", 60, float)
        self.ENTRYPOINT_TIMEOUT_SECONDS = _num("ENTRYPOINT_TIMEOUT_SECONDS", 10, float)
        self.TOOL_TIMEOUT_SECONDS = _num("TOOL_TIMEOUT_SECONDS", 60, float)
        self.MAX_OUTPUT_KB = _num("MAX_OUTPUT_KB", 200)  # captured per stream, per process
        self.MAX_ENTRYPOINTS = _num("MAX_ENTRYPOINTS", 3)
        self.MAX_FINDINGS = _num("MAX_FINDINGS", 500)

        # AI patch generation (Stage 3). The key is read from the environment/.env only and is never exposed.
        self.GEMINI_API_KEY = env.get("GEMINI_API_KEY", "").strip()
        # auto = use the google-genai SDK if it can be imported, otherwise the built-in HTTPS client; sdk | rest force one.
        # Extra Host header values to accept besides localhost / 127.0.0.1 / [::1] (comma separated, e.g. "myhost.local,10.0.0.5").
        self.ALLOWED_HOSTS = [h.strip().lower() for h in env.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
        self.GEMINI_TRANSPORT = env.get("GEMINI_TRANSPORT", "auto").strip().lower() or "auto"
        self.GEMINI_REST_BASE_URL = env.get("GEMINI_REST_BASE_URL", "").strip()  # tests only; empty = Google's endpoint
        self.GEMINI_MODEL = env.get("GEMINI_MODEL", "gemini-flash-latest").strip() or "gemini-flash-latest"
        self.GEMINI_TIMEOUT_SECONDS = _num("GEMINI_TIMEOUT_SECONDS", 180, float)
        # Gemini 3 models "think" before answering, which can exceed the deadline. "low" keeps patch requests fast.
        # Set GEMINI_THINKING_LEVEL to minimal/low/medium/high to override, or "off" to send nothing.
        # Left empty, AutoPatch uses "low" for gemini-3* models and sends nothing for other models.
        self.GEMINI_THINKING_LEVEL = env.get("GEMINI_THINKING_LEVEL", "").strip().lower()
        self.GEMINI_RETRIES = _num("GEMINI_RETRIES", 2)  # extra attempts after an HTTP 503 (overloaded) only
        self.GEMINI_TEMPERATURE = _num("GEMINI_TEMPERATURE", 0.2, float)
        self.GEMINI_MAX_OUTPUT_TOKENS = _num("GEMINI_MAX_OUTPUT_TOKENS", 8192)
        self.PATCH_MAX_FILES = _num("PATCH_MAX_FILES", 3)
        self.PATCH_MAX_CHANGED_LINES = _num("PATCH_MAX_CHANGED_LINES", 200)
        self.PATCH_MAX_DIFF_KB = _num("PATCH_MAX_DIFF_KB", 64)
        self.PATCH_MAX_CONTEXT_KB = _num("PATCH_MAX_CONTEXT_KB", 60)

        for key, value in overrides.items():
            setattr(self, key, value)

    # Flask reads upper-case attributes via app.config.from_object(cfg).
    @property
    def MAX_CONTENT_LENGTH(self) -> int:
        return int(self.MAX_UPLOAD_MB * 1024 * 1024)
