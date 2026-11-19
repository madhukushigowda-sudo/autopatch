# Frontend build changelog

Built on the verified Stage 0 baseline. The Gemini and patch engine files (`autopatch/api/patches.py`, `autopatch/core/gemini_client.py`,
`patch_diff.py`, `patch_prompt.py`, `patches.py`, `static/js/patches.js`) are byte-identical to the baseline; `tests/test_protected_files.py` enforces this.
No database schema, collection, owner field or migration was added. No new Python or JavaScript dependency.

## Added
- Read-only insight layer: `autopatch/core/insights.py`, `autopatch/api/insights.py` (dashboard, cross-repository issues, history, verification, reports, Markdown export).
- Design system `static/css/ap.css` (dark glass, navy / blue / cyan / violet), shared `static/js/shell.js` (canvas network background, syntax highlighter, count-up, toasts).
- Pages: Dashboard, Repositories (+ detail with file browser), Scan Center, Bug Intelligence (+ detail), AI Repair Studio (+ detail), Verification Center, Reports; redesigned landing page.
- Tests: `tests/test_insights.py`, `tests/test_pages.py` (routes, redirects, Patchy artwork hash, no HTML-string insertion, no external resources), `tests/test_protected_files.py`.

## Changed
- `templates/base.html`, `workspace.html`, `landing.html`, `finding.html`; `static/css/landing.css`; `common.js` (dark-theme severity colours only); `pages.py` routes; `api/projects.py` version string; `create_app` registers the insights blueprint.

## Removed
- `overview`, `analyze`, `project` templates and scripts and `app.css` (replaced). The three "coming later" sidebar placeholders are gone.

## Behaviour notes
- Health score formula is shown in the UI and the report; it is a heuristic. Before any scan it shows "No data yet".
- "Bugs fixed (verified)" counts applied patches whose verification verdict is `target_resolved`; other applied patches are shown as "not verified".
- Patch accept / reject / roll back still use the browser's native confirm and prompt dialogs (they live in the protected `patches.js`).

## Known limitations
- GitHub import is deferred. Non-Python languages remain unsupported in this build.
- Test execution is restricted local execution, not a hardened sandbox.
- Real Gemini output was not exercised here (mocked); the browser test used a fake client.
- Scan Center shows the backend's real stages, not per-file names.
- Tested in headless Chrome on Linux; not tested on Windows or in other browsers.
