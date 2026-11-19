"""Check the Gemini connection from this computer with ONE tiny request (no project code is sent).

    python scripts\\gemini_rest_check.py

Uses the built-in HTTPS client (no google-genai, no Pydantic Core) and the settings in your .env.
Prints OK or the exact error kind. The API key is never printed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopatch.core.gemini_client import AIError  # noqa: E402
from autopatch.core.gemini_rest import RestGeminiClient  # noqa: E402
from autopatch.core.keycheck import check_key  # noqa: E402
from config import Config  # noqa: E402


def main() -> int:
    cfg = Config()
    verdict = check_key(cfg.GEMINI_API_KEY)  # shape check first: no request is made for a blank or placeholder key
    if not verdict["ok"]:
        print("FAIL key: " + verdict["message"])
        return 1
    if verdict["note"]:
        print("NOTE " + verdict["note"])
    print(f"Model: {cfg.GEMINI_MODEL}   key configured: {'yes' if cfg.GEMINI_API_KEY else 'NO'}   timeout: {cfg.GEMINI_TIMEOUT_SECONDS}s")
    if not cfg.GEMINI_API_KEY:
        print("FAIL not_configured: add GEMINI_API_KEY to your .env file.")
        return 1
    try:
        client = RestGeminiClient(cfg)
        text = client.generate("You are a connectivity check. Reply only with JSON.", 'Reply with exactly this JSON object: {"ok": true}')
    except AIError as exc:
        print(f"FAIL {exc.kind}: {exc}")
        return 1
    try:
        ok = json.loads(text).get("ok") is True
    except (ValueError, AttributeError):
        ok = False
    print("OK: Gemini answered over HTTPS." if ok else "OK (connected), but the answer was not the expected JSON: " + text[:120].replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
