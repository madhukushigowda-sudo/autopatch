# Detection precision: before and after (Backend V2, Group A)

Nothing was suppressed to improve a number. Every rule that fired before still fires; the changes either lower the **severity** of a finding that is usually deliberate, add rules that find real bugs the old detector missed, or let the author mark a line.

## Method

`python scripts/measure_detection.py [--verbose|--json]` runs the real analysis pipeline (static stage) on a labelled corpus in `tests/fixtures/detection_corpus/`:

* `good/` 14 files of idiomatic code with no known bug (EAFP, `StopIteration`, optional imports, sentinels, guarded division, default-bound closures, context managers...). Any finding here is a false positive.
* `buggy/` 20 files with 21 planted defects, each on a line tagged `# <<BUG>>`. A finding on a tagged line is a true positive; any other finding is counted as a false positive.

Raw results: `docs/evidence/detection-before.json`, `docs/evidence/detection-after.json`.

## Results

| Metric | Before | After |
|---|---|---|
| Planted bugs found (recall) | 17 / 21 = 0.81 | **21 / 21 = 1.00** |
| Precision, all findings | 18 / 23 = 0.78 | 22 / 27 = 0.81 |
| Precision, Medium severity or higher | 14 / 17 = 0.82 | **18 / 18 = 1.00** |
| False positives on `good/` | 3 (all AP001, Medium) | 3 (all AP001, **Low severity, Low confidence**) |

Missed before: `return` inside `finally`, `x = items.sort()`, and two discarded `str.strip()/lower()` results.

## What changed

1. **AP001 "exception silently swallowed" is graded.** A *broad* handler (`except:`, `except Exception`, `except BaseException`) stays Medium. A *narrow* handler (`except KeyError: pass`, `except StopIteration: pass`) is now Low severity / Low confidence with wording that says it is often intentional. It is still reported.
2. **New rule AP004**: result of a `str` method such as `.strip()`/`.lower()` discarded.
3. **New rule AP005**: `x = items.sort()` / `return items.sort()` (the value is `None`).
4. **Ruff B012** (`return` in `finally`) and **B014** (redundant exception types) added to the selected rules, with explanations in the rule knowledge base.
5. **`# autopatch: ignore`** (or `# autopatch: ignore[AP004,E711]`) on a line hides findings for that line, for AutoPatch's own rules and Ruff's. It is per line, and a marker naming another code does not hide this one.
6. **Test files**: static findings in test files get one step lower confidence.

## Trade-offs, stated plainly

* The remaining 3 "false positives" on `good/` are the narrow-`pass` handlers. We kept them (Low) rather than hide them, because `except ValueError: pass` is sometimes a real bug. A reviewer will see them in the list; they no longer count as Medium and no longer pull the health score by 3 each (they now cost 1).
* **The corpus is small and I wrote it after having seen the AP001 noise.** The "after" precision numbers are therefore tuned to a corpus the changes were designed against and will not generalise as cleanly. The thresholds in `tests/test_detection_corpus.py` stop regressions on this corpus; they do not prove accuracy elsewhere.
* The two stray "false positives" inside `buggy/` (`F841` on a variable assigned after use, `RET503` on a function whose last statement is the planted bug) are real observations by Ruff but not the planted defect, so the strict labelling counts them as false positives.
* A narrow handler that really hides a bug is now less prominent (Low instead of Medium). That is the cost of lowering noise.

## Real code check (standard library, 19 files, no labels)

| | Before | After |
|---|---|---|
| Findings | 28 | 28 (none removed) |
| Medium or higher | 26 | 4 |
| Low | 2 | 24 |

22 of the 28 are narrow `except ...: pass` handlers from CPython's own modules. These were not hand-labelled, so this is not a precision figure; it only shows the noise moved out of the Medium+ list. Of the four that remain at Medium or higher: three are `B006` mutable-default reports in `argparse`, `copy` and `difflib`, and one is an `F821` on `BaseExceptionGroup` in `contextlib`. The `F821` is a known false positive (that builtin exists from Python 3.11 and Ruff is not told the project's Python version); it predates Backend V2 and was not changed.

## Not covered

Bandit was not installed in the test environment, so security-rule precision was not measured. Only Python is analysed.
