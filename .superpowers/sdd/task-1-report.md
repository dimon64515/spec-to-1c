# Task 1 Report: Модели данных и SQLite хранилище

## Status

DONE

## Files Created

- `price_search/__init__.py` — exports `PriceOffer` and `SearchResult`
- `price_search/models.py` — Pydantic models `PriceOffer` and `SearchResult`
- `price_search/storage.py` — `PriceStorage` class with SQLite persistence
- `tests/test_price_models.py` — model unit tests
- `tests/test_price_storage.py` — storage unit tests

## Files Modified

- `requirements.txt` — added `aiohttp>=3.9.0`, `beautifulsoup4>=4.12.0`, `lxml>=5.0.0`, `pydantic>=2.0.0`

## Test Command and Result

```bash
source .venv/bin/activate
pytest tests/test_price_models.py tests/test_price_storage.py -v
```

Result:

```
4 passed in 0.13s
```

All tests pass without warnings.

## Concerns

- None. Minor improvement: added explicit SQLite datetime adapter/converter to avoid Python 3.12 deprecation warnings; this does not affect the public interface.
