# Gemini over HTTPS (no SDK, no Pydantic Core)

Problem: on Windows with Application Control, `import google.genai` fails ("DLL load failed while importing _pydantic_core"), so Generate patch returned HTTP 503 "SDK not installed".

## Added
- `autopatch/core/gemini_rest.py`: `RestGeminiClient` (stdlib `urllib`), `select_llm(cfg)`, `probe_sdk()`. Same `generate(system, prompt) -> str` and `AIError` kinds as the SDK client; reuses its `classify`, retry rules and thinking-level logic.
- `GEMINI_TRANSPORT` (`auto` | `rest` | `sdk`) and `GEMINI_REST_BASE_URL` (tests only) settings; documented in `.env.example` and the README.
- `create_app` registers the REST client as `ext["llm"]` (the hook the patch API already honours) when the SDK cannot be imported or `rest` is forced.
- An `after_request` hook on `/api/ai/status` only: adds `transport` and, when REST is active, reports `sdk_installed: true` (the unchanged UI reads it as "an AI client is available").
- `scripts/gemini_rest_check.py`: one tiny live request to confirm your key and network.
- `tests/test_gemini_rest.py` (38 tests): request shape, key only in a header, thinking-level rules, response parsing, every error mapping, retries, timeouts, no redirects, key never in errors or logs, transport selection, status hook, and the full propose / accept / verify workflow plus validation rejections through the real patch API.

## Changed
- `requirements.txt`: `google-genai` is now optional (commented out). `scripts/check.py` clears the two new env names. `static/js/studio.js` notes the transport.

## Not changed
- Protected files (`api/patches.py`, `core/gemini_client.py`, `patch_diff.py`, `patch_prompt.py`, `patches.py`, `static/js/patches.js`) are byte-identical; `tests/test_protected_files.py` enforces it. Validation, consent, review, accept/reject, verification and rollback are untouched. No schema change. No fake responses in the product.

## Limits
- The real Google endpoint could not be reached from the build sandbox (no network). The client is tested against a local server that speaks the same JSON; run `scripts\gemini_rest_check.py` on your machine to confirm the live call.
- `GEMINI_TIMEOUT_SECONDS` applies per network operation (as with the SDK's timeout), not as a hard total deadline.
- Application Control may also block other compiled tools (for example `ruff.exe`, `bandit`); that is a separate policy question.
