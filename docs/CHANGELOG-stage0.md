# Stage 0 changelog

Base: verified baseline (69 files). No application code, Gemini code, templates, static files, config or schema changed.

## ADDED (16 files)
- `scripts/check.py` - the one check command (checksums, leak scan, Ruff, full tests in a clean environment, baseline-id guard).
- `tests/test_compat.py` - read-only compatibility tests on synthetic fixtures (7 tests).
- `tests/fixtures/compat/current/{projects,runs,findings,patches}.json` and `tests/fixtures/compat/legacy/{...}.json` - 8 synthetic fixture files.
- `tests/baseline_ids.txt` - the 267 baseline test ids and required outcomes.
- `docs/BASELINE.md`, `docs/ROADMAP.md`, `docs/CHANGELOG-stage0.md`.
- `INSTALL.txt`, `CHECKSUMS.txt`.

## MODIFIED (0 files)
None. `tests/conftest.py` is unchanged (fixture Option B).

## REMOVED (0 files)
None. No file is replaced; nothing in the baseline is overwritten.

## Fix 1 (after the first real Windows run)

The first Windows run passed 272 tests (0 failed, 2 skipped) but the baseline-id guard reported 9 long parametrized
ids as "missing": pytest spells some of them differently on Windows. The guard now groups tests by function and checks
that no function disappears, loses parametrized cases, fails, or is skipped without permission.
MODIFIED: `scripts/check.py`, `tests/test_compat.py` (8 added tests), `tests/baseline_ids.txt` (header comment only),
`docs/CHANGELOG-stage0.md`, `CHECKSUMS.txt`. No application code changed.
