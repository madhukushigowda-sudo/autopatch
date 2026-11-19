# Test results, Backend V2 Group A

All results below come from commands actually run on the final tree in the Linux build environment (Python 3.13, Ruff 0.16.9; Bandit not installed; no network; no IPv6).

## `python scripts/check.py` (clean environment, temporary data folder)
```
[PASS] no .env in this folder
[PASS] files match CHECKSUMS.txt: 166 files
[PASS] secret and leak scan: 165 text files
[PASS] ruff (E,F,W,B): no findings; known E702 in patch_diff.py exempted
[PASS] pytest (clean environment, temporary data): 458 tests: 457 passed, 0 failed, 1 skipped
      skipped: tests.test_static_tools::test_bandit_runs_for_real   (Bandit not installed here)
[PASS] baseline tests still present and passing: 267 baseline tests
[PASS] no data folder created in project
RESULT: PASS
```
Previous build (gemini2): 342 tests. Added here: 116 (corpus/precision 10, key and secret guard 33, Host/headers/CSP 54, Windows diagnostics 17, dashboard recomputation 1, frontend byte-identity 1). One existing assertion changed: with the SDK transport the app now registers the secret guard (`tests/test_gemini_rest.py`). No other existing test was edited.
The first full run of this phase failed 4 checks (two baseline tests expected HTTP 403 for a foreign Host and I had returned 400; Ruff on new files; two literal examples tripped the leak scanner). They were fixed (the Host rejection is now 403) and the run above is the re-run.

## Real-browser run (Playwright + Chrome, enforcing CSP, app with the guard in place and a stand-in LLM)
| Address | Checks | CSP violations | Foreign Host header |
|---|---|---|---|
| `http://127.0.0.1:5057` | 43 / 43 | 0 | 403 |
| `http://localhost:5057` | 43 / 43 | 0 | 403 |
| `http://[::1]:5057` | **not run** (no IPv6 in the build sandbox; unit-tested only) | n/a | n/a |

The only logged "error" is Chrome's `ERR_ABORTED` when the Markdown report download starts (expected). A negative test confirmed the CSP is enforced: an injected inline script did not run and an external image was blocked.

## Detection precision
See `DETECTION-REPORT.md` and `docs/evidence/*.json`: recall 17/21 to 21/21; Medium-and-up precision 0.82 to 1.00; 0 findings removed.

## Protected files and frontend
`python scripts/verify_protected.py --against ../gemini2/autopatch-dev ../frontend1/autopatch-dev ../backend1/autopatch-dev`: all 6 protected files byte-identical to the baseline and all three builds. `tests/test_frontend_unchanged.py`: all 30 template/static files identical to Frontend V1. Details in `PROTECTED-FILES-REPORT.md`.

## Not tested
Windows (real Application Control, `taskkill` cleanup), real Google API calls, Bandit, IPv6 in a browser, container isolation. Not production-ready.
