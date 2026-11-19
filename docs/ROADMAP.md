# Roadmap (approved)

0 Safe baseline. 1 Reproduction verification and re-scan. 2 Repair loop (up to three candidates, human approval mandatory).
3 Regression demo and measured benchmark. 4 App shell and design system, Dashboard, Code Scan, History, landing page
redesign (robot mascot and hero/scroll animations preserved). 5 AI Analysis, Test Center, Autofix Studio.
6 Authentication. 7 Export and suggested regression test. 8 Security hardening and isolation.
9 Data reliability. 10 Test hardening, Windows CI, packaging, documentation, release candidate.

Rules for every stage: approval before starting and before moving on; full test suite plus new tests; no schema change,
users collection or owner field without separate approval; the Gemini patch workflow is not changed unless proposed,
tested and approved separately; the original project, .env, keys, data, snapshots and backups are never touched.
