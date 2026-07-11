# Task 5 Report: AsyncPriceEngine

## Scope
Implemented only Task 5 of the price-search implementation plan. Tasks 1-4 and 6-7 were left untouched.

## Files Created

1. `price_search/engine.py`
   - Implements `AsyncPriceEngine`.
   - Uses `PriceStorage`, `SourceRegistry`, and an optional fallback source.
   - Builds queries with decreasing precision (`name size`, `name brand size`, `name`).
   - Runs registered sources in parallel per query.
   - Filters offers for relevance against the current query keywords.
   - Falls back to the fallback source when fewer than `min_offers` relevant offers are found.
   - Sorts results by price, keeps the top-3, caches them via `PriceStorage`, and returns a `SearchResult`.

2. `tests/test_price_engine.py`
   - Adds `test_engine_returns_min_price` verifying that the engine returns the cheapest relevant offer across two registered sources.
   - Note: the placeholder titles from the plan (`"Cheap"`, `"Expensive"`) were filtered out as irrelevant by the engine, so the test fixture titles were adjusted to include the query keywords (`"Cheap Клапан RVN-560"`, `"Expensive Клапан RVN-560"`) while preserving the original test intent.

## Files Modified

- `tests/test_price_engine.py` (new file).
- `price_search/engine.py` (new file).

## Verification

Command run:

```bash
source /home/dimon64515/projects/xml-to-1c/.venv/bin/activate
cd /home/dimon64515/projects/xml-to-1c
pytest tests/test_price_engine.py -v
```

Result: 1 passed.

```
tests/test_price_engine.py::test_engine_returns_min_price PASSED
```

## Commit

```bash
git add price_search/engine.py tests/test_price_engine.py .superpowers/sdd/task-5-report.md
git commit -m "feat(price_search): add AsyncPriceEngine with caching and fallback"
```

No push performed.
