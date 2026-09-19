# Task 4 Report: Корпус позиций полного покрытия (генератор + фикстура + контрактный тест)

Date: 2026-09-11 · Branch: `feature/order-client-transport` · Base: `a4d7adb`

## What was implemented

Three new files, exactly as specified in `.superpowers/sdd/task-4-brief.md`:

1. **`tools/build_order_positions_fixture.py`** — one-off generator. Runs the real PDF pipeline (`bitrix_bot.pipeline.process_pdf_to_positions` + `json_positions.build_positions`) over all 10 PDFs under `examples/`, then adds a synthetic position (per `synthetic_position` rules: round D0=200/shina=65, rect A0=400×B0=200/shina=95, L0=1000) for every catalog article from `reference/1c_products_all.json` not covered by the corpus, excluding garbage `----` articles, groups, and `BLOCKED_1C_ARTICLES` (20-2).
2. **`tests/fixtures/order_positions_full.json`** — generated corpus, committed (1 066 944 bytes): `real_count=2768`, `synthetic_count=168`, `total=2936` positions, `blocked=["20-2"]`, sorted by article.
3. **`tests/test_order_positions_fixture.py`** — contract test (verbatim from brief): fixture exists + coverage (`>= 198` unique articles, no `20-2`/`----`, real and synthetic counts > 0), and per-position contract (exactly 9 keys, non-empty `params` dict, utf-8 round-trip serialization).

## TDD Evidence

**RED** — `.venv/bin/python -m pytest tests/test_order_positions_fixture.py -v` (before fixture existed):

```
FAILED tests/test_order_positions_fixture.py::test_fixture_exists_and_covers_catalog
FAILED tests/test_order_positions_fixture.py::test_fixture_positions_match_contract
E  FileNotFoundError: [Errno 2] No such file or directory:
   '/home/dimon64515/projects/xml-to-1c/tests/fixtures/order_positions_full.json'
```

**Generator run** — `.venv/bin/python tools/build_order_positions_fixture.py` (exit 0, no traceback, ~4 min). Progress lines for all 10 PDFs printed; final stdout line:

```
real=2768 synth=168 total=2936 unique_articles=198 blocked=['20-2']
```

`unique_articles=198 >= MIN_ARTICLES=198` ✓, `blocked=['20-2']` ✓.

**GREEN** — `.venv/bin/python -m pytest tests/test_order_positions_fixture.py -v`:

```
tests/test_order_positions_fixture.py::test_fixture_exists_and_covers_catalog PASSED [ 50%]
tests/test_order_positions_fixture.py::test_fixture_positions_match_contract PASSED [100%]
2 passed in 0.11s
```

**Full suite** — `.venv/bin/python -m pytest tests/ -x -q`:

```
231 passed, 1 warning in 42.63s
```

(1 pre-existing StarletteDeprecationWarning from fastapi TestClient; unrelated.)

## Files changed & commit

Commit `036ca8801a439fc2a0ccf2b3bf39324b98390290` (branch `feature/order-client-transport`):

```
test(order_client): full article coverage fixture (corpus + synthetic)
 tests/fixtures/order_positions_full.json | 43670 ++...
 tests/test_order_positions_fixture.py    |    46 +
 tools/build_order_positions_fixture.py   |   111 +
 3 files changed, 43827 insertions(+)
```

Only these three files staged (explicit `git add`, no `-A`, no amend). Pre-existing unrelated dirty files (`requirements.txt`, `tools/as_order_loader/README.md`, `tools/collect_multi_project_sizes.py`, `docs/*.md`, prior task reports) left untouched.

## Self-review findings

- Test and generator code are byte-for-byte the brief's verbatim code.
- Fixture sanity-checked programmatically: all 2936 positions have exactly the key set `{article, qty, thickness, material, params, comment, shina, conn0, conn1}`; corpus top-level keys are exactly `real_count, synthetic_count, blocked, positions`.
- `blocked` in fixture is `["20-2"]`, matching `set(BLOCKED_1C_ARTICLES)` at HEAD.
- Coverage is exactly at the boundary (`unique_articles == 198 == MIN_ARTICLES`): 199 valid catalog articles − blocked 20-2. This is correct per plan, but brittle by design — any catalog snapshot change requires re-running the generator.

## Concerns

- **Boundary brittleness**: coverage equals the test minimum exactly. If `reference/1c_products_all.json` is refreshed with new articles and the fixture is not regenerated, the test fails (loudly, as intended).
- The test file's module docstring reproduces an unclosed parenthesis from the brief verbatim (`...(фикстура для приёмки HTTP-сервиса 1С.`); harmless in a docstring, kept because the brief mandates exact code.
- Fixture (1 MB) makes the repo heavier; accepted per plan ("фикстура коммитится").
