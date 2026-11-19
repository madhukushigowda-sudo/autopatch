# Backend V2, Group A: changelog

Scope was limited to six approved items. No frontend file, protected Gemini/patch-engine file, database schema, multi-language wiring or container code was changed (see `PROTECTED-FILES-REPORT.md`).

## 1. Gemini key validation
* New `autopatch/core/keycheck.py`. A key that is empty, a placeholder (`your-key-here`, `changeme`, `<paste key>`...), wrapped in quotes, containing whitespace, or containing non-ASCII characters is rejected **before any request**, with a specific message. A key in the exact Google shape (`AIza` + 35 characters) is always accepted. An unusual format only produces a note, because Google may change formats.
* `/api/ai/status` now also returns `key_problem`, `key_message`, `key_note` (never the key). `configured` is `false` for a rejected key.
* REST errors now include Google's own status words (for example `INVALID_ARGUMENT API_KEY_INVALID`). Only upper-case status/reason tokens are passed on, so no free text and no key can leak.
* `.env.example` no longer ships the placeholder key value; the setting is empty. `scripts/gemini_rest_check.py` runs the key check first.

## 2. Detection precision
See `DETECTION-REPORT.md` (before/after, method, trade-offs). Summary: recall 17/21 to 21/21; Medium-and-up precision 0.82 to 1.00 on the labelled corpus; nothing suppressed. New rules AP004, AP005, Ruff B012/B014; graded AP001; `# autopatch: ignore`; lower confidence in test files.

## 3. Host validation and security headers
* New `autopatch/core/security.py`. Every request (pages, API, static files) is refused with HTTP 403 unless its Host is `localhost`, `127.0.0.1`, `[::1]` or `*.localhost` (any port), or listed in the new `ALLOWED_HOSTS` setting. This closes the DNS-rebinding route to the local API.
* Headers on every response: Content-Security-Policy, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, COOP, CORP, Permissions-Policy.
* CSP: `default-src 'self'`; scripts only from `'self'` plus the SHA-256 of the single inline script in `landing.html` (computed at start-up, so no template changed and there is no `unsafe-inline` for scripts); `style-src 'self' 'unsafe-inline'` (pages use inline style attributes); `img-src 'self' data:`; `connect-src 'self'`; `object-src 'none'`; `frame-ancestors 'none'`; `base-uri`/`form-action` `'self'`.
* This is defence in depth for a local tool. It is **not authentication**.

## 4. Secret guard before transmission
* New `GuardedLLM` wraps the active client (SDK or HTTPS) and is registered as `ext["llm"]`. Before every request it applies the key check, then scans the system text and the full prompt for secrets (cloud/API keys, tokens, private-key blocks, database URLs with passwords, hard-coded credentials).
* On a hit **nothing is sent**. The error (HTTP 422) names the kinds, counts and the file section, never a value or a prefix; logs carry kinds only. A harmless test value can be allowed per line with `# autopatch: allow-secret`. There is no global switch.
* Limits: the scanner is pattern- and entropy-based. It will miss secrets in unusual formats and may flag harmless strings. It only protects what goes to Gemini from this app.

## 5. Windows diagnostics
* New `scripts/windows_check.py` (+ `autopatch/core/diagnostics.py`). Imports each component in its own child interpreter, so a blocked DLL cannot crash the check. Reports OK / BLOCKED / MISSING per component, the blocked file path when Windows names one, plus data-folder write access, IPv4/IPv6 listening, `.env` presence and key shape. `--network` adds one tiny Gemini request.
* `sandbox.py` now says "Windows blocked it (WinError 4551...)" when a tool cannot start for that reason (the message never contains a path or argument).
* It never attempts to get around a block: no policy change, no unblocking, no copying of DLLs, no elevation (a test asserts the module contains none of those).

## 6. Tests
Earlier suite: 342 tests. This phase adds corpus/precision (10), key and secret guard (33), Host/headers/CSP (54), Windows diagnostics (17), dashboard independent recomputation (1) and frontend byte-identity (1). One existing assertion was updated: the SDK transport now also registers the guard (`test_gemini_rest.py`). See `TEST-RESULTS-backend-v2.md`.

## Behaviour changes to be aware of
* A project whose code contains a detected secret can no longer be sent to Gemini until the secret is removed or the line is marked `# autopatch: allow-secret`.
* Requests addressed to any other name (for example a LAN IP) get HTTP 403 unless added to `ALLOWED_HOSTS`.
* Narrow `except X: pass` findings now rank as Low.

## Not done / not claimed
* Not production-ready. Not a hardened sandbox. Container isolation, schema changes, multi-language analysis: out of scope for this phase.
* Windows itself was not available here; run `scripts\windows_check.py` and the steps in `WINDOWS-TESTING.md` on the target PC.
* No real Google request could be made from the build environment (no network). The HTTPS client is tested against a local stand-in server.
* IPv6 `[::1]` was verified by unit tests only; the build sandbox has no IPv6, so no browser run on `[::1]`.
* Bandit was not installed in the build environment.
