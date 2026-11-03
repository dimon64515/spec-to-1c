"""Раннер обработки заявки: PDF → позиции пайплайна + дилерские цены trading-строк.

Часть 1 (Task 3): run_processing — пайплайн, статусы, price_search.
Часть 2 (Task 4): load_to_1c, apply_analog.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from bitrix_bot import pipeline
from bitrix_bot.config import load_bot_config
from json_positions import build_positions
from map_customer_equipment import load_mapping
from report_xlsx import is_trading_skip
from web_ui import analogs
from web_ui.store import Store


async def run_processing(
    store: Store,
    request_id: int,
    file_name: str,
    pdf_bytes: bytes,
    price_engine: Any = None,
) -> None:
    """Прогнать PDF через пайплайн и сохранить результат в store.

    Статусы: processing → done (позиции, meta, дилерские цены trading-строк)
    или error (текст исключения в requests.error). Пустой success — не ошибка:
    заявка уходит в done с meta.escalation пайплайна.

    Args:
        store: хранилище заявок.
        request_id: id заявки в статусе processing.
        file_name: имя файла (для логов будущих; пайплайну не нужно).
        pdf_bytes: сырые байты PDF.
        price_engine: AsyncPriceEngine или None — цены ищутся только для
            trading-строк; сбой price_search не блокирует обработку.
    """
    try:
        success, skipped, meta = pipeline.process_pdf_to_positions(pdf_bytes)
    except Exception as exc:  # пайплайн падает по-разному — эскалация в error
        store.set_status(request_id, "error", str(exc))
        return

    trading = [s for s in skipped if is_trading_skip(s)]
    if price_engine is not None and trading:
        items = [
            {"name": s.get("name") or s.get("raw_name") or "", "size": s.get("size") or ""}
            for s in trading
        ]
        try:
            results = await price_engine.search(items)
        except Exception:
            # недоступность источников цен не должна ронять заявку:
            # строки получат price_info=None («цена не найдена»)
            results = []
        _attach_dealer_prices(trading, results)

    store.save_positions(request_id, success, skipped)
    store.save_meta(request_id, meta or {})
    store.set_status(request_id, "done")


def _attach_dealer_prices(trading: List[dict], results: List[Any]) -> None:
    """Записать в каждую trading-строку price_info из результатов price_search.

    Соответствие по позиции (zip): results[i] → trading[i]. Пустые офферы или
    отсутствие результата → {"price": None, "url": None, "supplier": None}.
    """
    for idx, row in enumerate(trading):
        result = results[idx] if idx < len(results) else None
        best = result.best_offer if result is not None else None
        row["price_info"] = {
            "price": str(result.min_price) if result is not None and result.min_price is not None else None,
            "url": best.url if best is not None else None,
            "supplier": best.supplier if best is not None else None,
        }


def load_to_1c(store: Store, request_id: int, header: dict, base_comment: str = "Web-заявка") -> dict:
    """Загрузить loaded-позиции заявки в 1С и смержить построчные цены обратно.

    Порядок: loaded из store → build_positions → pipeline.load_order_to_1c
    (конфигурация из load_bot_config; order_service только при непустом
    order_service_url) → pipeline._merge_prices в строки store →
    set_order_number → статус loaded_1c. Загрузка разрешена только из статуса
    done — повторный вызов после успеха отклоняется ValueError. Бизнес-ошибка 1С
    (пустой order_number или непустые errors) исключением не бросается: статус
    остаётся done, повтор возможен, текст пишется в meta.load_error.

    При исключении 1С статус остаётся done (повтор возможен), текст ошибки
    пишется в meta.load_error заявки.

    Args:
        store: хранилище заявок.
        request_id: id заявки в статусе done.
        header: реквизиты заказа (в загрузке не участвуют — см. Task 5).
        base_comment: базовый комментарий заказа 1С.

    Returns:
        {"order_number": str|None, "errors": list, "warnings": list}.
    """
    req = store.get_request(request_id)
    if req is None:
        raise ValueError(f"нет заявки {request_id}")
    if req["status"] != "done":
        raise ValueError(
            f"загрузка в 1С разрешена только из статуса done, текущий: {req['status']}"
        )

    loaded = store.get_positions(request_id, "loaded")
    for row in loaded:
        # стабы/принятые аналоги без полного набора ключей — дефолты
        # совпадают с run_pipeline
        row.setdefault("params", {})
        row.setdefault("thickness", 0.8)
        row.setdefault("comment", "")

    cfg = load_bot_config()
    order_service = None
    if cfg.order_service_url:
        order_service = {
            "url": cfg.order_service_url,
            "key": cfg.order_service_key,
            "user": cfg.order_service_user,
            "password": cfg.order_service_password,
        }

    try:
        out = pipeline.load_order_to_1c(
            build_positions(loaded),
            cfg.execute_code_url,
            base_comment,
            timeout=cfg.request_timeout,
            order_service=order_service,
            request_id=str(request_id),
        )
    except Exception as exc:
        meta = dict(req["meta"] or {})
        meta["load_error"] = str(exc)
        store.save_meta(request_id, meta)
        return {"order_number": None, "errors": [str(exc)], "warnings": []}

    if isinstance(out, tuple):
        # обратная совместимость со стабами (order_number, errors, warnings)
        order_number, errors_1c, warnings_1c = out
        prices = []
    else:
        order_number = out["order_number"]
        errors_1c = list(out["errors"])
        warnings_1c = list(out["warnings"])
        prices = out.get("prices") or []

    if not order_number or errors_1c:
        # бизнес-ошибка 1С исключением не бросается: заказ не создан — статус
        # loaded_1c не ставим, повторная загрузка остаётся возможной
        meta = dict(req["meta"] or {})
        meta["load_error"] = "; ".join(str(e) for e in errors_1c) or (
            "1С не вернул номер заказа"
        )
        store.save_meta(request_id, meta)
        return {"order_number": None, "errors": errors_1c, "warnings": warnings_1c}

    pipeline._merge_prices(loaded, prices)
    for i, row in enumerate(loaded):
        store.update_position(request_id, "loaded", i, row)
    store.set_order_number(request_id, order_number)
    store.set_status(request_id, "loaded_1c")
    return {"order_number": order_number, "errors": errors_1c, "warnings": warnings_1c}


def apply_analog(store: Store, request_id: int, index: int) -> dict:
    """Принять предложенный аналог для skipped-строки с порядковым номером index.

    Строка конвертируется analogs.accept_analog и перемещается из skipped
    в loaded (store.move_position уплотняет индексы skipped). При AnalogError
    (нет категории, размер не распознан) строка остаётся skipped, в неё
    пишется "analog_error" — UI показывает причину без падения.

    Returns:
        Новая loaded-позиция или исходная skipped-строка с analog_error.
    """
    skipped = store.get_positions(request_id, "skipped")
    if index < 0 or index >= len(skipped):
        raise ValueError(f"нет skipped-позиции с индексом {index}")
    row = skipped[index]
    mapping = load_mapping()
    suggestion = analogs.suggest_analog(row, mapping)
    try:
        if suggestion is None:
            raise analogs.AnalogError("категория не определена")
        position = analogs.accept_analog(row, suggestion, mapping)
    except analogs.AnalogError as exc:
        row = dict(row)
        row["analog_error"] = str(exc)
        store.update_position(request_id, "skipped", index, row)
        return row
    return store.move_position(request_id, "skipped", index, "loaded", data=position)
