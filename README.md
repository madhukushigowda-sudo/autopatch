# AutoPatch

Autonomous software bug detection and repair for Python projects (INNOVEDGE Hackathon 2026, Problem Statement 02).

**Promise:** AutoPatch does not just suggest a fix. It reproduces the bug, tests candidate fixes, rejects fixes that
cause regressions, and lets the developer approve the change. Passing tests do not prove complete correctness, so
patches will be labelled *"Verified against the available test suite"* with actual test counts.

## Status (Stage 3 of 7 plus the frontend build)

| Stage | Scope | State |
|---|---|---|
| 1 | Flask scaffold, config, storage (MongoDB + local fallback), safe ZIP / .py upload, AST project index | **Implemented and tested** |
| 2 | Bug detection and execution analysis: pytest baseline, Ruff, Bandit, entry points, tracebacks, findings, dashboard | **Implemented and tested** (Bandit run itself not verified here, see Limitations) |
| 3 | Gemini patch generation, diff review, accept/reject, working-copy apply, verification, history, rollback | **Implemented and tested with a mocked Gemini** (real Gemini call not verified here) |
| 4 | Verification gate, regression testing, rejection reasons | Not started |
| 5 | Approve / reject, exact rollback, history, patched ZIP | Not started |
| 6 | UI polish, gradebook demo project with a regression trap | Not started |
| 7 | Benchmark (5-10 cases, measured results) | Not started |

## Gemini transport (works when the SDK is blocked)

AutoPatch can reach Gemini two ways, chosen by `GEMINI_TRANSPORT` in `.env`:

| Value | Behaviour |
|---|---|
| `auto` (default) | Uses the `google-genai` SDK if it really imports; otherwise uses the built-in HTTPS client |
| `rest` | Always uses the built-in HTTPS client (Python standard library only: no `google-genai`, no Pydantic Core) |
| `sdk` | Always uses the SDK |

If Windows Application Control blocks the SDK's compiled Pydantic Core file, set `GEMINI_TRANSPORT=rest` (or leave `auto`). The built-in client calls Google's
official REST endpoint (`models/<model>:generateContent`), sends the key in a header, verifies HTTPS certificates, does not follow redirects, and uses the same
model, timeout, retry, temperature, token and thinking settings. Everything it returns goes through the same validation, review, accept/reject, verification and
rollback as before. Test it from your machine (it sends one tiny request, no project code): `python scripts\gemini_rest_check.py`.

## Interface (frontend build)

The interface is a dark glass UI built with plain HTML, CSS and JavaScript (no framework, no build step, no CDN; it works offline).
Every page reads real data from the backend; nothing on the dashboard is estimated or hard-coded.

| Page | Address | What it does |
|---|---|---|
| Landing | `/` | Product introduction, animated Patchy hero, live numbers from this installation |
| Dashboard | `/app` | Repositories, files scanned, bugs detected, bugs fixed (verified vs not verified), health score with visible formula, recent activity |
| Repositories | `/app/repositories`, `/app/repositories/<id>` | ZIP / .py upload with drag and drop, repository list, file browser with syntax highlighting, scan history, repairs, dependencies |
| Scan Center | `/app/scan` | Start a scan; each real pipeline stage is shown live (polling); results, tool status, integrity check |
| Bug Intelligence | `/app/issues`, `/app/findings/<id>` | All bugs across repositories with filters; evidence, root cause, AI explanation |
| AI Repair Studio | `/app/studio`, `/app/studio/<id>` | Generate a repair, compare original vs proposed side by side, approve or reject (the Stage 3 patch workflow, unchanged) |
| Verification Center | `/app/verification` | Repair verification results (before/after test counts), scan and test history |
| Reports | `/app/reports` | Health summary, detection and repair statistics, Markdown export (`/api/reports/<id>?format=md`), print |

New read-only endpoints (no database changes): `GET /api/dashboard`, `/api/issues`, `/api/history`, `/api/verification`, `/api/reports/<project_id>`.
Old addresses (`/analyze`, `/app/analyze`, `/app/projects/<id>`) redirect to the new pages. Importing from GitHub is not available yet.


### Optional reviewer instruction (Generate patch)

The finding page has an optional **Reviewer instruction** box (max 500 characters) next to the consent checkbox, for example
"Raise ValueError('Cannot divide by zero') instead of returning None."

- It is sent to Gemini only when you press Generate patch, as a clearly labelled section of the prompt, together with the code you already consented to send. Do not paste secrets.
- It is a preference, not a permission: the system prompt says it cannot override the rules, and the server-side validation is unchanged. Patches that edit test files, create/delete/rename files, touch files that were not offered, or fail to apply are still rejected.
- The server cleans the text (control, zero-width and bidirectional characters removed, line endings normalised, triple backticks neutralised). Text over 500 characters after cleaning is rejected with HTTP 400, never truncated.
- The cleaned text is stored on the patch record (`instruction`, `instruction_chars`) and shown in the patch detail and history. Patches created before this feature simply show no instruction.
- API: `POST /api/findings/<id>/patches` accepts `{"confirm_send": true, "instruction": "..."}`; `instruction` is optional.
- Gemini may still ignore the instruction; the diff review remains the safeguard.

## How Stage 2 works

1. **Upload** (Stage 1): the ZIP/.py files are validated, stored as an untouched original and indexed with `ast`.
2. **Start an analysis** from the project page. The request is accepted only from the same machine (loopback address,
   `localhost` host, no cross-site `Origin`, JSON body) and only if `ALLOW_EXECUTION` is not `false`. One analysis runs at a time,
   in a background thread.
3. The original is **copied to a temporary working folder** (`data/runs/<run>/work`). All execution happens there, in
   child processes, never in the Flask process. A SHA-256 digest of the original is taken before and after to
   prove it was not changed; the folder is deleted at the end.
4. Stages: parse structure, run `pytest` (with a small in-process report plugin that records every test and detects a hung test),
   run Ruff and Bandit, run AST pattern checks (swallowed exceptions, division by a literal zero, unreachable code), run up to
   three detected entry points once (no arguments, no stdin), then build findings.
5. Child processes get a **minimal environment** (secret-looking variables and `GEMINI_API_KEY` / `MONGODB_URI` are never passed),
   closed stdin, a timeout, and an output cap. On timeout the whole process tree is killed.
6. Every **finding** has an ID, category, file, line, severity (Critical/High/Medium/Low), confidence (High/Medium/Low),
   description, root cause (a hypothesis), triggering condition, evidence, and detection method. It is labelled either
   **Runtime-confirmed** (observed while code ran: test failure, traceback, entry-point crash, timeout, collection error) or
   **Static, not confirmed** (Ruff, Bandit, AST patterns, syntax errors). Static findings are never labelled runtime.
   Severity, confidence and the runtime/static label are kept separate; confidence is a heuristic, not a calibrated probability.
7. The dashboard (`/app`) shows only numbers read from stored results. Open a project to run an analysis, watch progress,
   filter findings by severity / category / runtime-vs-static, and open a finding for its traceback and code evidence.

Pages: `/` landing, `/app` overview, `/app/analyze` upload and projects, `/app/projects/<id>` analysis, `/app/findings/<id>` investigation.

## How Stage 3 works (AI patches)

1. Add your key to `.env` (`GEMINI_API_KEY=...`) and restart. Ordinary scanning never calls Gemini.
2. Open a finding, tick the consent box (it states what is sent: the finding, the relevant source files, related test code and a project outline), press **Generate patch**.
3. The response is treated as untrusted. It must be valid JSON; the diff is parsed and checked. Rejected: malformed diffs, new/deleted/renamed files, paths outside the project, test files, non-Python files, files the model was not given, oversized patches, context that does not match the real code, and results that are not valid Python. Rejected proposals are kept in the history as "Rejected by validation". No AI-generated command is ever run.
4. You review the diff (unified or original-vs-proposed), explanation, side effects, suggested tests, risk and confidence. Risk and confidence are heuristics, not calibrated probabilities.
5. **Accept** applies it to a separate working copy (`data/projects/<id>/working`), never to your original upload. Before applying, AutoPatch runs the project's tests (baseline) and takes a snapshot of the touched files; after applying it runs the tests again and compares per-test results. A regression is reverted automatically and reported. **Reject** changes nothing.
6. Results use wording like "Verified against the available test suite: 2 passed, 0 failed, 0 skipped (baseline: 1 passed, 1 failed, 0 skipped)". If no test exercises the finding, the result says the fix is insufficiently verified. Passing tests never mean "correct".
7. **Rollback** restores the snapshot byte for byte and checks the working copy's digest against the pre-patch digest. Patches roll back newest first, and rollback refuses to overwrite files edited after the patch.
8. History (proposal, explanation, diff, decision, timestamps, test results, changed files, patch hash, rollback info) is stored for every candidate, including rejected and rolled-back ones, and shown on the finding page and the project's "Patch history" tab.

New endpoints: `GET /api/ai/status`, `POST|GET /api/findings/<id>/patches`, `GET /api/projects/<id>/patches`, `GET /api/patches/<id>`, `POST /api/patches/<id>/accept|reject|rollback`. State-changing endpoints accept only same-machine JSON requests; generation also needs `confirm_send: true`.

## Setup on Windows (PowerShell)

```powershell
cd autopatch
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked:  Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt
copy .env.example .env                 # then put your GEMINI_API_KEY in .env (needed only for Generate patch)
python app.py                          # http://127.0.0.1:5000
pytest                                 # run all Stage 1 + Stage 2 tests
```

Requires Python 3.10 or newer. The server listens on `127.0.0.1` only.

### MongoDB Atlas (optional)

Put the connection string in `.env` as `MONGODB_URI=...` (never in code or chat). With `STORAGE_BACKEND=auto`
AutoPatch tries Atlas and silently falls back to local JSON files under `data/db/` if it cannot connect; the top-right
chip always shows which backend is active and why. Atlas needs your IP allowed under *Network Access*.
The Mongo code path could not be exercised against a real cluster during development (see Limitations).

## Safety rules implemented so far

* Uploaded originals are copied into `data/projects/<id>/original/` and never modified by AutoPatch. Analysis works on a copy.
* ZIPs are rejected for: path traversal, absolute paths, drive letters, backslash tricks, symlinks, encrypted entries,
  duplicate or case-colliding names, Windows-reserved names and illegal characters, too many entries/files,
  oversized files and oversized expanded totals. Binary files are dropped (and listed as skipped).
* Uploading never executes anything. Execution happens only when you press "Run analysis", only from this computer,
  only in child processes on a temporary copy, with timeouts. No endpoint accepts code to run directly.
* Secrets come from environment variables only; error messages never echo connection strings; child processes do not receive them.
* The UI inserts all uploaded names/content with `textContent`, never `innerHTML`. The UI uses no CDN (works offline).

## Limitations (honest list)

* Python projects only.
* **Restricted local prototype, not a hardened security sandbox.** Project code runs as your user and can read files,
  use the network, and write outside the temporary folder. A test that modifies the original upload is *detected*
  (the run is flagged) but not prevented. Only analyze code you are willing to run on your machine. Do not expose this server publicly.
* Bandit was not installed in the development environment, so the real Bandit run was not exercised; its JSON parsing and wiring were tested
  with sample output and a stand-in executable. If Bandit is missing, the run reports it as "unavailable".
* Process-tree kill on Windows uses `taskkill /T /F` and was not tested (development was on Linux). Timeout handling was tested on Linux.
* Root causes are heuristics. "Code under test" for a failing assertion is a guess from the failing statement.
* Flaky tests are not detected (a test is run once). Entry points are run once with no arguments and no input.
* Projects needing packages not installed here fail to import; this is reported as an environment finding, not a project bug.
* The local store is for a single process. `MongoStore` was not tested against a live Atlas cluster.
* The listed Bootstrap/highlight.js front-end stack was replaced by plain CSS/JS so the UI works offline and could be verified by screenshots.
* **Stage 3 limits:** the real Gemini SDK call could not be run in the development environment (no network, SDK not installed), so it was tested only against a mocked SDK and a mocked client. Check the first real call yourself; the default model `gemini-flash-latest` can be changed with `GEMINI_MODEL`. Generation is a single synchronous request (default timeout 180 s; Gemini 3 models default to thinking level `low` here, see `.env.example`; HTTP 503 is retried twice). Patches may only edit existing, non-test `.py` files that were sent to the model, up to 3 files / 200 changed lines. Verification uses the project's own tests in a throw-away copy (restricted local run, not a sandbox). Entry-point and static findings usually have no covering test, so they end as "insufficiently verified". One patch operation runs at a time; there is no patched-ZIP download or per-file approval yet.
* Several engine modules (sandbox, Ruff/Bandit wrappers, pytest runner, patterns, findings and runtime-findings) were already present in the
  project folder when Stage 2 started; they were reviewed, tested and adopted.


## Backend V2 (Group A) additions
Gemini key validation, detection-precision changes, Host validation with security headers and CSP, a secret guard before anything is sent to Gemini, and Windows diagnostics (`python scripts\windows_check.py`). Read `docs/CHANGELOG-backend-v2.md`, `docs/DETECTION-REPORT.md`, `docs/PROTECTED-FILES-REPORT.md` and `docs/WINDOWS-TESTING.md`. This is a local development tool: not production-ready, not a hardened sandbox, and the Host/CSP checks are not authentication.
