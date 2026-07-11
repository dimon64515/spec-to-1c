# Task 4 Report: HVAC-источники и fallback на поисковики

## Scope
Implemented only Task 4 of the price-search implementation plan. Tasks 1-3 and 5-7 were left untouched.

## Files Created

1. `price_search/sources/hvac/__init__.py`
   - Exports `GenericHvacSource`.

2. `price_search/sources/hvac/generic.py`
   - Implements `GenericHvacSource(BasePriceSource)`.
   - Supports configurable CSS selectors for item, title, price, and supplier.
   - Normalizes prices by extracting digits.
   - Uses `aiohttp` for async HTTP and `BeautifulSoup`/`lxml` for HTML parsing.

3. `price_search/fallback/__init__.py`
   - Empty package marker.

4. `price_search/fallback/search_engines.py`
   - Implements `SearchEngineFallback`.
   - Supports Yandex (`.serp-item`) and Google (`div.g`) result parsing.
   - Uses `Decimal("0")` for offers without a reliable price, per the plan.

## Files Modified

- `tests/test_price_sources.py`
  - Added imports for `GenericHvacSource` and `SearchEngineFallback`.
  - Added `test_generic_hvac_parse` covering configurable selector parsing.
  - Added `test_search_engine_fallback_parse` covering Yandex SERP parsing.

## Verification

Command run:

```bash
source /home/dimon64515/projects/xml-to-1c/.venv/bin/activate
cd /home/dimon64515/projects/xml-to-1c
pytest tests/test_price_sources.py -v
```

Result: all 6 tests passed.

```
tests/test_price_sources.py::test_source_registry PASSED
tests/test_price_sources.py::test_pulscen_parse_html PASSED
tests/test_price_sources.py::test_tiu_parse_html PASSED
tests/test_price_sources.py::test_blizko_parse_html PASSED
tests/test_price_sources.py::test_generic_hvac_parse PASSED
tests/test_price_sources.py::test_search_engine_fallback_parse PASSED
```

## Commit

```bash
git add price_search/sources/hvac/ price_search/fallback/ tests/test_price_sources.py .superpowers/sdd/task-4-report.md
git commit -m "feat(price_search): add generic HVAC source and search engine fallback"
```

No push performed.
