# Stage 0 baseline

Source: `autopatch-stage3-instruction-final-v2.zip` (SHA-256 `c8c2ca990d628c359a70bb39b4f7d0e610fb6e0c6e6f75fa14794380e2212ede`).
It was verified byte-identical (69 of 69 files) to the working Windows project, with the reviewer-instruction
feature and the Gemini 503/504 retry improvements present.

## Test baseline (untouched copy, clean environment, temporary data folder)

267 tests: 266 passed, 1 skipped, 0 failed (Python 3.13, Linux, Bandit not installed).
On Windows with Bandit installed the expected result is 265 passed, 2 skipped, 0 failed: two POSIX-only tests in
`tests/test_sandbox.py` are skipped and the Bandit test runs. `tests/baseline_ids.txt` lists all 267 test ids.

## Stored data (read-only description)

The local store keeps one JSON file per collection in `<DATA_DIR>/db/`: `projects`, `runs`, `findings`, `patches`.
Project files live in `<DATA_DIR>/projects/<id>/` (`original`, `working`, `snapshots/<patch_id>`).
Stage 0 changes none of this. Synthetic copies of these collections are in `tests/fixtures/compat/`.

## Known notes (not changed in stage 0)

- Three Ruff E702 notes in `autopatch/core/patch_diff.py` are pre-existing; `scripts/check.py` exempts them.
- `tests/test_store.py` tries a local connection to closed port 127.0.0.1:1 when `pymongo` is installed. It is harmless.
- Running tests with a developer `.env` or environment variables set can change results (this caused the
  `GEMINI_THINKING_LEVEL=low` failure on Windows). `scripts/check.py` removes the config variables for its run.
  Running plain `pytest` is not isolated.

## Workflow

Work only in stage folders (`stage0`, `stage1`, ...). Each stage ZIP is a complete snapshot, extracted into a new folder.
Your real project is changed only by a separate, explicitly approved promotion step.
