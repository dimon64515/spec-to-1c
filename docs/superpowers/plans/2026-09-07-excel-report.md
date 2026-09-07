# Excel-отчёт + round-trip правки: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** После обработки PDF формировать Excel-отчёт (4 листа: Загружено / Пропущено / Перекупное / Ошибки 1С) и отправлять его в чат Битрикс файлом с кратким резюме + давать скачивать рядом с XML в Streamlit; отредактированный отчёт можно загрузить обратно (веб или чат бота) — создаётся новый заказ 1С с правками.

**Architecture:** Общий модуль `report_xlsx.py` (корень) — генерация и разбор xlsx, единая классификация «перекупное». Оркестрация пересоздания — `recreate_order_from_report` в `bitrix_bot/pipeline.py` (там уже живут `load_order_to_1c`/`build_positions`). Интеграции: `bitrix_bot` (send_file через disk REST + intake .xlsx) и `web_app.py` (download_button + вкладка пересоздания).

**Tech Stack:** Python 3.12, openpyxl (уже в requirements), pytest, Streamlit, FastAPI.

**Spec:** `docs/superpowers/specs/2026-09-07-excel-report-design.md`

## Global Constraints

- openpyxl уже установлен (`requirements.txt`), новые зависимости НЕ добавлять.
- Отправка файла в Битрикс: сбой доставки отчёта НЕ должен приводить к reschedule/пересозданию заказа — всегда фолбэк на текстовый `build_report` (заказ уже создан).
- Обновления в 1С НЕ делаем: пересоздание = новый заказ, старый удаляет менеджер вручную; в комментарии нового — `Заменяет заказ №N (исправлено из отчёта)`.
- Имя файла в Битрикс: ASCII, `report_order_<N>.xlsx`.
- Русские названия листов и заголовков — как в спеке («Загружено», «Пропущено», «Перекупное», «Ошибки 1С»).
- Стиль тестов: обычные pytest-функции, локальные stub-классы без unittest.mock (как в tests/test_bitrix_report.py).
- Каждый таск заканчивается зелёными тестами и коммитом.

---

### Task 1: Парсер кладёт material/thickness/quantity в пропущенные позиции

**Files:**
- Modify: `process_specification_table.py` (~строки 1060-1121, функция `parse_row`)
- Test: `tests/test_process_specification_table.py` (дописать; если файла нет — создать)

**Interfaces:**
- Produces: skip-dict от `parse_row` теперь содержит ключи `material: str`, `thickness: float`, `quantity: float|None` во всех ветках пропуска. Позже Task 2 (`report_xlsx.py`) читает их через `.get()`.

- [ ] **Step 1: Написать падающий тест**

Создать/дописать в `tests/test_process_specification_table.py`:

```python
from process_specification_table import parse_row


def test_trading_skip_contains_material_thickness_quantity():
    ok, skip = parse_row(
        {"name": "Гибкий воздуховод Ф125", "size": "Ф125", "unit": "м", "quantity": "10"},
        {},
    )
    assert ok is None
    assert skip["reason"].startswith("Покупная позиция")
    assert skip["material"] == "оцинкованная"
    assert skip["thickness"] == 0.8
    assert skip["quantity"] == 10


def test_bad_quantity_skip_contains_material():
    ok, skip = parse_row(
        {"name": "Отвод нержавеющий 0.7 300x200", "size": "300x200",
         "unit": "шт", "quantity": "abc"},
        {},
    )
    assert ok is None
    assert skip["reason"].startswith("Не удалось распознать количество")
    assert skip["material"] == "оцинкованная"
    assert skip["thickness"] == 0.7
    assert skip["quantity"] is None
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/test_process_specification_table.py -k skip_contains -v`
Expected: FAIL с `KeyError: 'material'`.

- [ ] **Step 3: Минимальная правка parse_row**

В `process_specification_table.py` в `parse_row` сразу после блока определения `thickness`
(после строки `thickness_explicit = True` под `if thick_from_name is not None:`) вставить:

```python
    try:
        parsed_quantity = float(str(quantity_raw).replace(",", ".").replace(" ", ""))
    except ValueError:
        parsed_quantity = None
```

Затем в трёх ветках `return None, _apply_ocr_warnings({...}, ocr_warnings)` добавить
в словарь три ключа: `"quantity": parsed_quantity, "material": material, "thickness": thickness`.

Ветка 1 (покупная позиция, ~1096):
```python
        return None, _apply_ocr_warnings(
            {"name": name, "size": size, "unit": unit,
             "quantity": parsed_quantity, "material": material, "thickness": thickness,
             "reason": "Покупная позиция (гибкий воздуховод/трубопровод/изоляция) — завод не производит"},
            ocr_warnings,
        )
```

Ветка 2 (покупная арматура, ~1108) — аналогично, с теми же тремя ключами.

Ветка 3 (количество, ~1117): заменить блок `try: quantity = float(...)` на:
```python
    if parsed_quantity is None:
        return None, _apply_ocr_warnings(
            {"name": name, "size": size, "unit": unit,
             "quantity": None, "material": material, "thickness": thickness,
             "reason": f"Не удалось распознать количество: {quantity_raw}"},
            ocr_warnings,
        )
    quantity = parsed_quantity
```

(Старый блок `try/except ValueError` с `return None, ...` вокруг него удалить — он переехал выше.)

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_process_specification_table.py -v`
Expected: PASS, включая старые тесты файла.

Run полного регресса: `python -m pytest tests/ -x -q`
Expected: PASS (поведение существующих ключей не изменилось — добавлены только новые).

- [ ] **Step 5: Commit**

```bash
git add process_specification_table.py tests/test_process_specification_table.py
git commit -m "feat(parser): keep material/thickness/quantity in skipped rows"
```

---

### Task 2: Модуль report_xlsx.py — генерация отчёта

**Files:**
- Create: `report_xlsx.py` (корень репозитория)
- Test: `tests/test_report_xlsx.py`

**Interfaces:**
- Consumes: `PipelineResult` из `bitrix_bot/pipeline.py` (поля: file_name, order_number, loaded, skipped, errors_1c, warnings_1c); skip-dict с ключами из Task 1.
- Produces:
  - `build_excel_report(res) -> bytes` — используется в Task 4 (бот), Task 6 (веб), Task 10 (веб round-trip).
  - `is_trading_skip(skip: dict) -> bool` — используется в Task 3 (`build_summary`).
  - Константы листов: `SHEET_LOADED="Загружено"`, `SHEET_SKIPPED="Пропущено"`, `SHEET_TRADING="Перекупное"`, `SHEET_ERRORS="Ошибки 1С"` — Task 7 (parse_edited_report) ищет листы по ним.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_report_xlsx.py`:

```python
import io

from openpyxl import load_workbook

from bitrix_bot.pipeline import PipelineResult
from report_xlsx import (
    SHEET_ERRORS, SHEET_LOADED, SHEET_SKIPPED, SHEET_TRADING,
    build_excel_report, is_trading_skip,
)


def _sample_result() -> PipelineResult:
    return PipelineResult(
        file_name="spec.pdf",
        order_number="839",
        loaded=[
            {"article": "1-1-1", "params": {"A0": 300, "B0": 200}, "quantity": 2,
             "material_code": "1", "thickness": 0.8,
             "connection_0": "6", "connection_1": "2",
             "connection_2": "0", "connection_3": "0",
             "system": "П1", "comment": "Воздуховод 300x200"},
        ],
        skipped=[
            {"name": "Гибкий воздуховод Ф125", "size": "Ф125", "unit": "м",
             "quantity": 10, "material": "оцинкованная", "thickness": 0.8,
             "reason": "Покупная позиция (гибкий воздуховод/трубопровод/изоляция) — завод не производит"},
            {"name": "Клапан ОЗ-60-НО-400*200", "size": "400x200", "unit": "шт",
             "quantity": 1, "material": "оцинкованная", "thickness": 0.8,
             "reason": "Покупная арматура (брендовый клапан/шумоглушитель) — завод не производит"},
            {"name": "Неизвестная деталь", "size": "100x100", "unit": "шт",
             "quantity": 3, "material": "оцинкованная", "thickness": 0.8,
             "reason": "Не удалось распознать артикул"},
            {"raw_name": "Диффузор SR-P 300", "model": "SR-P-300",
             "reason": "Оборудование не производится"},
        ],
        errors_1c=["Нет цены у артикула 1-1-1"],
        warnings_1c=["Проверьте шину"],
    )


def test_sheets_and_split():
    wb = load_workbook(io.BytesIO(build_excel_report(_sample_result())))
    assert wb.sheetnames == [SHEET_LOADED, SHEET_SKIPPED, SHEET_TRADING, SHEET_ERRORS]

    loaded_ws = wb[SHEET_LOADED]
    headers = [c.value for c in loaded_ws[1]]
    assert headers[:6] == ["Артикул", "A", "B", "D", "Кол-во", "Материал"]
    assert "Соед. 0" in headers and "Соед. 3" in headers
    row = [c.value for c in loaded_ws[2]]
    assert row[0] == "1-1-1" and row[1] == 300 and row[2] == 200

    skipped_ws = wb[SHEET_SKIPPED]
    names = [r[0] for r in skipped_ws.iter_rows(min_row=2, values_only=True)]
    assert names == ["Неизвестная деталь"]  # только не-перекупное

    trading_ws = wb[SHEET_TRADING]
    tnames = [r[0] for r in trading_ws.iter_rows(min_row=2, values_only=True)]
    assert "Гибкий воздуховод Ф125" in tnames
    assert "Диффузор SR-P 300" in tnames  # raw_name-строка тоже перекупное
    theaders = [c.value for c in trading_ws[1]]
    assert "Материал" in theaders and "Толщина" in theaders and "Включить в заказ" in theaders

    errors_ws = wb[SHEET_ERRORS]
    labels = [r[0] for r in errors_ws.iter_rows(values_only=True) if r[0]]
    assert "Номер заказа" in labels
    texts = [r[-1] for r in errors_ws.iter_rows(values_only=True) if r and r[0] in ("Ошибка", "Предупреждение")]
    assert "Нет цены у артикула 1-1-1" in texts


def test_is_trading_skip():
    assert is_trading_skip({"reason": "Покупная позиция (…) — завод не производит"})
    assert is_trading_skip({"reason": "Покупная арматура (…) — завод не производит"})
    assert is_trading_skip({"raw_name": "Диффузор", "reason": "Оборудование не производится"})
    assert not is_trading_skip({"reason": "Не удалось распознать артикул"})
```

- [ ] **Step 2: Запустить, убедиться что падает**

Run: `python -m pytest tests/test_report_xlsx.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'report_xlsx'`.

- [ ] **Step 3: Реализация report_xlsx.py**

Создать `report_xlsx.py`:

```python
"""Генерация и разбор Excel-отчёта по результатам пайплайна.

build_excel_report: PipelineResult -> xlsx (4 листа).
Листы и заголовки — константы: parse_edited_report (ниже в этом же файле,
Task 7) читает их обратно для пересоздания заказа.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from process_specification_table import detect_product_type

if TYPE_CHECKING:
    from bitrix_bot.pipeline import PipelineResult

SHEET_LOADED = "Загружено"
SHEET_SKIPPED = "Пропущено"
SHEET_TRADING = "Перекупное"
SHEET_ERRORS = "Ошибки 1С"

TRADING_REASON_PREFIXES = ("Покупная позиция", "Покупная арматура")
ORDER_NUMBER_LABEL = "Номер заказа"

LOADED_HEADERS = ["Артикул", "A", "B", "D", "Кол-во", "Материал", "Толщина",
                  "Соед. 0", "Соед. 1", "Соед. 2", "Соед. 3",
                  "Система", "Комментарий"]
SKIPPED_HEADERS = ["Наименование", "Размер", "Кол-во", "Ед.", "Материал",
                   "Толщина", "Причина", "Включить в заказ"]
TRADING_HEADERS = ["Наименование", "Категория", "Размер", "Материал", "Толщина",
                   "Кол-во", "Ед.", "Производитель", "Модель", "Включить в заказ"]


def is_trading_skip(skip: dict) -> bool:
    """Перекупное (покупное) оборудование: скипы по причине «Покупная …»
    и строки автомаппинга оборудования (raw_name)."""
    if skip.get("raw_name"):
        return True
    reason = str(skip.get("reason", ""))
    return reason.startswith(TRADING_REASON_PREFIXES)


def _write_sheet(ws, headers: List[str], rows: List[List[Any]]) -> None:
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    for i, h in enumerate(headers, 1):
        width = len(str(h))
        for row in rows:
            width = max(width, len(str(row[i - 1])) if row[i - 1] is not None else 0)
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 8), 60)


def _loaded_row(p: dict) -> List[Any]:
    dims = p.get("params") or {}
    return [
        p.get("article", ""), dims.get("A0"), dims.get("B0"), dims.get("D0"),
        p.get("quantity"), p.get("material_code", ""), p.get("thickness"),
        p.get("connection_0", ""), p.get("connection_1", ""),
        p.get("connection_2", ""), p.get("connection_3", ""),
        p.get("system", ""), p.get("comment", ""),
    ]


def _skipped_row(s: dict) -> List[Any]:
    return [s.get("name", ""), s.get("size", ""), s.get("quantity"),
            s.get("unit", ""), s.get("material", ""), s.get("thickness"),
            s.get("reason", ""), ""]


def _trading_row(s: dict) -> List[Any]:
    name = s.get("name") or s.get("raw_name") or ""
    return [name, detect_product_type(name) or "",
            s.get("size") or s.get("model") or "",
            s.get("material", ""), s.get("thickness"),
            s.get("quantity"), s.get("unit", "шт"),
            s.get("manufacturer", ""), s.get("model", ""), ""]


def build_excel_report(res: "PipelineResult") -> bytes:
    """Собрать xlsx-отчёт: Загружено / Пропущено / Перекупное / Ошибки 1С."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_LOADED
    _write_sheet(ws, LOADED_HEADERS, [_loaded_row(p) for p in res.loaded])

    trading = [s for s in res.skipped if is_trading_skip(s)]
    skipped = [s for s in res.skipped if not is_trading_skip(s)]

    _write_sheet(wb.create_sheet(SHEET_SKIPPED), SKIPPED_HEADERS,
                 [_skipped_row(s) for s in skipped])
    _write_sheet(wb.create_sheet(SHEET_TRADING), TRADING_HEADERS,
                 [_trading_row(s) for s in trading])

    ws = wb.create_sheet(SHEET_ERRORS)
    summary = [
        (ORDER_NUMBER_LABEL, res.order_number or ""),
        ("Файл", res.file_name),
        ("Загружено", len(res.loaded)),
        ("Пропущено", len(skipped)),
        ("Перекупное", len(trading)),
        ("Ошибок 1С", len(res.errors_1c)),
        ("Предупреждений 1С", len(res.warnings_1c)),
    ]
    ws.append(["Показатель", "Значение"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for label, value in summary:
        ws.append([label, value])
    ws.append([])
    ws.append(["Тип", "Текст"])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for e in res.errors_1c:
        ws.append(["Ошибка", e])
    for w in res.warnings_1c:
        ws.append(["Предупреждение", w])
    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 60

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

- [ ] **Step 4: Запустить тесты**

Run: `python -m pytest tests/test_report_xlsx.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add report_xlsx.py tests/test_report_xlsx.py
git commit -m "feat(report): build_excel_report with 4 sheets (loaded/skipped/trading/errors)"
```

---

### Task 3: build_summary — краткое резюме для чата

**Files:**
- Modify: `bitrix_bot/report.py`
- Test: `tests/test_bitrix_report.py` (дописать)

**Interfaces:**
- Consumes: `PipelineResult`, `is_trading_skip` из `report_xlsx.py` (Task 2).
- Produces: `build_summary(res: PipelineResult) -> str` — подпись к файлу в Task 4 и фолбэк-текстах.

- [ ] **Step 1: Падающий тест** (дописать в `tests/test_bitrix_report.py`)

```python
from bitrix_bot.report import build_summary


def test_build_summary_counts_trading():
    res = PipelineResult(
        file_name="spec.pdf", order_number="839",
        loaded=[{"article": "1-1-1", "params": {}, "quantity": 2}],
        skipped=[
            {"name": "гибкий", "reason": "Покупная позиция (…) — завод не производит"},
            {"name": "мусор", "reason": "Не удалось распознать артикул"},
        ],
        errors_1c=["ошибка1"],
        warnings_1c=[],
    )
    text = build_summary(res)
    assert "№839" in text
    assert "Загружено: 1" in text
    assert "Пропущено: 1" in text
    assert "Перекупное: 1" in text
    assert "Ошибок 1С: 1" in text
```

- [ ] **Step 2: Убедиться что падает**

Run: `python -m pytest tests/test_bitrix_report.py -k summary -v`
Expected: FAIL — `ImportError: cannot import name 'build_summary'`.

- [ ] **Step 3: Реализация** — в конец `bitrix_bot/report.py`:

```python
def build_summary(res: PipelineResult) -> str:
    """Короткая сводка для подписи к Excel-файлу в чате."""
    n_trading = sum(1 for s in res.skipped if is_trading_skip(s))
    n_skipped = len(res.skipped) - n_trading
    header = f"Заказ 1С: №{res.order_number}" if res.order_number else "Заказ НЕ создан"
    lines = [
        header,
        f"Файл: {res.file_name}",
        f"Загружено: {len(res.loaded)} · Пропущено: {n_skipped}"
        f" · Перекупное: {n_trading} · Ошибок 1С: {len(res.errors_1c)}",
    ]
    if res.errors_1c:
        lines.append("Ошибки: " + "; ".join(res.errors_1c[:5]))
    return "\n".join(lines)
```

И в шапку `bitrix_bot/report.py` добавить импорт:
```python
from report_xlsx import is_trading_skip
```

- [ ] **Step 4: Тесты**

Run: `python -m pytest tests/test_bitrix_report.py -v`
Expected: PASS (старые тесты `build_report` не тронуты).

- [ ] **Step 5: Commit**

```bash
git add bitrix_bot/report.py tests/test_bitrix_report.py
git commit -m "feat(report): build_summary caption for excel file delivery"
```

---

### Task 4: BitrixClient.send_file — загрузка файла в диск + сообщение

**Files:**
- Modify: `bitrix_bot/bitrix_client.py`
- Test: `tests/test_bitrix_client.py` (дописать)

**Interfaces:**
- Consumes: `BitrixClient.call` (существующий), `send_message` (существующий).
- Produces: `send_file(dialog_id: str, file_name: str, content: bytes, caption: str, bot_id: Optional[int] = None) -> None`. Используется в Task 5. При сбое attach — фолбэк на текст со ссылкой DETAIL_URL.

- [ ] **Step 1: Падающий тест** (дописать в `tests/test_bitrix_client.py`)

```python
import base64


class _StubTransport:
    """Записывает вызовы call(); download/get — заглушки."""

    def __init__(self):
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == "disk.storage.getlist":
            return [{"ID": "7"}]
        if method == "disk.storage.get":
            return {"ROOT_OBJECT_ID": "99"}
        if method == "disk.folder.uploadfile":
            return {"ID": "555", "DETAIL_URL": "https://portal/disk/555"}
        if method in ("im.message.add", "imbot.message.add"):
            return {"message_id": 1}
        return {}


def test_send_file_uploads_and_attaches():
    client = BitrixClient.__new__(BitrixClient)
    client._webhook = "http://hook/"
    client._timeout = 30
    client._client_id = ""
    stub = _StubTransport()
    client.call = stub.call

    client.send_file("task|42", "report_order_839.xlsx", b"PK\x03\x04", "Заказ №839",
                     bot_id=5)

    methods = [m for m, _ in stub.calls]
    assert methods == ["disk.storage.getlist", "disk.storage.get",
                       "disk.folder.uploadfile", "imbot.message.add"]
    up = stub.calls[2][1]
    assert up["id"] == "99"
    assert up["data"] == {"NAME": "report_order_839.xlsx"}
    assert base64.b64decode(up["fileContent"]) == b"PK\x03\x04"
    msg = stub.calls[3][1]
    assert msg["MESSAGE"] == "Заказ №839"
    assert msg["ATTACH"] == [["DISK", "555"]]
    assert msg["BOT_ID"] == 5


def test_send_file_falls_back_to_link_on_attach_error():
    from bitrix_bot.bitrix_client import BitrixError

    client = BitrixClient.__new__(BitrixClient)
    client._webhook = "http://hook/"
    client._timeout = 30
    client._client_id = ""
    stub = _StubTransport()
    client.call = stub.call

    def failing(method, **params):
        if method == "imbot.message.add":
            raise BitrixError("attach not supported")
        return stub.call(method, **params)

    client.call = failing
    sent = []
    client.send_message = lambda dialog_id, text, bot_id=None: sent.append(text)

    client.send_file("task|42", "r.xlsx", b"x", "cap", bot_id=5)

    assert sent and "https://portal/disk/555" in sent[0]
    assert sent[0].startswith("cap")
```

(Если в файле уже есть импорт `BitrixClient`/`BitrixError` — не дублировать.)

- [ ] **Step 2: Убедиться что падает**

Run: `python -m pytest tests/test_bitrix_client.py -k send_file -v`
Expected: FAIL — `AttributeError: 'BitrixClient' object has no attribute 'send_file'`.

- [ ] **Step 3: Реализация** — в `bitrix_bot/bitrix_client.py` добавить импорт `import base64` и метод класса:

```python
    def send_file(self, dialog_id: str, file_name: str, content: bytes,
                  caption: str, bot_id: Optional[int] = None) -> None:
        """Прикрепить файл к сообщению чата: upload на Диск + im.message.add
        с ATTACH. Если attach не поддержан — фолбэк: текст со ссылкой на файл."""
        storages = self.call("disk.storage.getlist")
        if not storages:
            raise BitrixError("disk.storage.getlist: нет доступных хранилищ")
        storage = self.call("disk.storage.get", id=storages[0]["ID"])
        folder_id = storage["ROOT_OBJECT_ID"]
        uploaded = self.call(
            "disk.folder.uploadfile",
            id=folder_id,
            data={"NAME": file_name},
            fileContent=base64.b64encode(content).decode("ascii"),
            generateUniqueName=True,
        )
        file_id = uploaded["ID"]
        detail_url = uploaded.get("DETAIL_URL") or ""
        attach = [["DISK", file_id]]
        try:
            if bot_id:
                params = dict(DIALOG_ID=dialog_id, MESSAGE=caption,
                              ATTACH=attach, BOT_ID=bot_id)
                if self._client_id:
                    params["CLIENT_ID"] = self._client_id
                self.call("imbot.message.add", **params)
            else:
                self.call("im.message.add", DIALOG_ID=dialog_id,
                          MESSAGE=caption, ATTACH=attach)
        except BitrixError:
            # тариф/скоп не позволяет attach — хотя бы ссылка на файл
            suffix = f"\nФайл: {detail_url}" if detail_url else ""
            self.send_message(dialog_id, caption + suffix, bot_id=bot_id)
```

- [ ] **Step 4: Тесты**

Run: `python -m pytest tests/test_bitrix_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bitrix_bot/bitrix_client.py tests/test_bitrix_client.py
git commit -m "feat(bitrix): send_file via disk upload + im attach with link fallback"
```

**После мержа:** smoke-тест против реального портала (вебхук в `bitrix.local.yaml`):
`python -c "from bitrix_bot.config import load_bot_config; from bitrix_bot.bitrix_client import BitrixClient; c=BitrixClient(load_bot_config().incoming_webhook); c.send_file('<dialog_id>', 'smoke.xlsx', b'PK', 'smoke')"` — проверить, что файл виден в чате. Если attach не сработал (фолбэк-ссылка) — зафиксировать в BACKLOG.md, текстовый фолбэк считается приемлемым.

---

### Task 5: Бот отправляет файл + резюме (фолбэк на текст)

**Files:**
- Modify: `bitrix_bot/server.py` (make_handler, :39-54)
- Test: `tests/test_bitrix_server.py` (дописать)

**Interfaces:**
- Consumes: `build_excel_report` (Task 2), `build_summary` (Task 3), `send_file` (Task 4), `build_report` (существующий).
- Produces: handler из `make_handler` шлёт файл+резюме; при сбое — нарезку `build_report` (старое поведение). Task 9 добавит в этот handler ветку round-trip (по расширению job.file_name).

- [ ] **Step 1: Падающий тест** (дописать в `tests/test_bitrix_server.py`)

```python
import os


def _enqueue_pdf(queue, tmp_path, handler):
    pdf = tmp_path / "spec.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    job = queue.enqueue(
        Job(dialog_id="task|42", task_id=42, pdf_path="",
            file_name="spec.pdf", order_comment="c", bot_id=5),
        pdf_bytes=pdf.read_bytes(),
    )
    handler(job)
    return job


def test_handler_sends_excel_file(tmp_path, monkeypatch):
    from bitrix_bot.pipeline import PipelineResult
    import bitrix_bot.server as server_mod

    sent_files, sent_msgs = [], []

    class _Client:
        def send_file(self, dialog_id, file_name, content, caption, bot_id=None):
            sent_files.append((file_name, caption))

        def send_message(self, dialog_id, text, bot_id=None):
            sent_msgs.append(text)

    monkeypatch.setattr(
        server_mod, "run_pipeline",
        lambda *a, **k: PipelineResult(file_name="spec.pdf", order_number="839",
                                       loaded=[{"article": "1-1-1", "params": {},
                                                "quantity": 2}],
                                       skipped=[], errors_1c=[], warnings_1c=[]),
    )

    from bitrix_bot.config import BotConfig
    cfg = BotConfig(execute_code_url="http://x", report_limit=3500)
    handler = server_mod.make_handler(cfg, _Client())
    queue = JobQueue(str(tmp_path / "jobs.db"), str(tmp_path))
    _enqueue_pdf(queue, tmp_path, handler)

    assert len(sent_files) == 1
    assert sent_files[0][0] == "report_order_839.xlsx"
    assert "№839" in sent_files[0][1]
    assert not sent_msgs  # текстовая нарезка не отправлялась


def test_handler_falls_back_to_text_on_send_file_error(tmp_path, monkeypatch):
    from bitrix_bot.pipeline import PipelineResult
    import bitrix_bot.server as server_mod

    sent_msgs = []

    class _Client:
        def send_file(self, *a, **k):
            raise RuntimeError("disk unavailable")

        def send_message(self, dialog_id, text, bot_id=None):
            sent_msgs.append(text)

    monkeypatch.setattr(
        server_mod, "run_pipeline",
        lambda *a, **k: PipelineResult(file_name="spec.pdf", order_number="839",
                                       loaded=[{"article": "1-1-1", "params": {},
                                                "quantity": 2}],
                                       skipped=[], errors_1c=[], warnings_1c=[]),
    )

    from bitrix_bot.config import BotConfig
    cfg = BotConfig(execute_code_url="http://x", report_limit=3500)
    handler = server_mod.make_handler(cfg, _Client())
    queue = JobQueue(str(tmp_path / "jobs.db"), str(tmp_path))
    _enqueue_pdf(queue, tmp_path, handler)

    assert sent_msgs and "№839" in sent_msgs[0]
```

(Импорты `Job`, `JobQueue` — те, что уже используются в этом файле тестов.)

- [ ] **Step 2: Убедиться что падает**

Run: `python -m pytest tests/test_bitrix_server.py -k "excel_file or falls_back" -v`
Expected: FAIL — handler шлёт только send_message, `sent_files` пуст.

- [ ] **Step 3: Реализация** — заменить тело `make_handler` в `bitrix_bot/server.py`:

```python
def make_handler(cfg: BotConfig, client) -> Callable[[Job], None]:
    """Обработчик задания очереди: пайплайн -> Excel-файл с резюме в чат;
    при сбое доставки файла — фолбэк на текстовую нарезку."""
    def handle(job: Job) -> None:
        data = Path(job.pdf_path).read_bytes()
        res = run_pipeline(
            data, job.file_name, job.order_comment,
            cfg.execute_code_url, timeout=cfg.request_timeout,
        )
        try:
            xlsx = build_excel_report(res)
            client.send_file(
                job.dialog_id,
                f"report_order_{res.order_number or 'new'}.xlsx",
                xlsx, build_summary(res), bot_id=job.bot_id,
            )
        except Exception:
            # заказ в 1С уже создан — сбой доставки отчёта не должен
            # приводить к reschedule (иначе дубликат заказа)
            logger.exception("excel report delivery failed for job %s", job.id)
            for msg in build_report(res, cfg.report_limit):
                try:
                    client.send_message(job.dialog_id, msg, bot_id=job.bot_id)
                except Exception:
                    logger.exception("report delivery failed for job %s", job.id)
    return handle
```

И в импорты `bitrix_bot/server.py` добавить:
```python
from bitrix_bot.report import build_report, build_summary
from report_xlsx import build_excel_report
```

- [ ] **Step 4: Тесты**

Run: `python -m pytest tests/test_bitrix_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bitrix_bot/server.py tests/test_bitrix_server.py
git commit -m "feat(bot): deliver excel report file with summary, text fallback"
```

---

### Task 6: Веб: кнопка скачивания отчёта рядом с XML

**Files:**
- Modify: `web_app.py` (блок колонок :364-379)
- Test: нет (Streamlit-виджеты; проверка — запуск `streamlit run web_app.py` вручную, см. Step 4)

**Interfaces:**
- Consumes: `build_excel_report` (Task 2), `PipelineResult` из `bitrix_bot.pipeline`.
- Produces: третья колонка с `st.download_button` рядом с XML/skipped.json.

- [ ] **Step 1: Импорт в `web_app.py`** — добавить в блок импортов:

```python
from bitrix_bot.pipeline import PipelineResult
from report_xlsx import build_excel_report
```

- [ ] **Step 2: Заменить блок колонок** (web_app.py :364-379) на:

```python
            col1, col2, col3 = st.columns(3)
            with col1:
                st.download_button(
                    label="⬇️ Скачать order.xml",
                    data=xml_text,
                    file_name="order.xml",
                    mime="application/xml",
                )
            with col2:
                skipped_json = json.dumps(all_skipped, ensure_ascii=False, indent=2)
                st.download_button(
                    label="⬇️ Скачать skipped.json",
                    data=skipped_json,
                    file_name="skipped.json",
                    mime="application/json",
                )
            with col3:
                report_xlsx = build_excel_report(
                    PipelineResult(
                        file_name=file_name,
                        loaded=success_rows,
                        skipped=all_skipped,
                    )
                )
                st.download_button(
                    label="⬇️ Скачать отчёт.xlsx",
                    data=report_xlsx,
                    file_name="report.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
```

- [ ] **Step 3: Проверка синтаксиса**

Run: `python -m py_compile web_app.py report_xlsx.py bitrix_bot/server.py`
Expected: без ошибок.

- [ ] **Step 4: Ручной смоук** (если доступно окружение)

Run: `streamlit run web_app.py` → загрузить тестовый PDF из `examples/samples/` → «Сгенерировать XML» → убедиться, что кнопка «⬇️ Скачать отчёт.xlsx» есть и файл открывается (4 листа). Остановить: Ctrl+C.

- [ ] **Step 5: Commit**

```bash
git add web_app.py
git commit -m "feat(web): excel report download button next to xml"
```

---

### Task 7: parse_edited_report — разбор отредактированного отчёта

**Files:**
- Modify: `report_xlsx.py` (добавить в конец файла)
- Test: `tests/test_report_xlsx.py` (дописать)

**Interfaces:**
- Consumes: константы/заголовки из Task 2 (`SHEET_*`, `LOADED_HEADERS`, `SKIPPED_HEADERS`, `TRADING_HEADERS`, `ORDER_NUMBER_LABEL`).
- Produces:
  ```python
  class EditedReportError(ValueError): ...
  @dataclass
  class EditedReport:
      loaded_rows: List[dict]     # success-формат: article, params{A0,B0,D0}, quantity, material_code, thickness, connection_0..3, system, comment
      include_rows: List[dict]    # raw spec rows {name,size,unit,quantity,material,thickness} для process_rows
      skipped_rows: List[dict]    # все пропущенные/перекупные (для нового отчёта)
      replaced_order: Optional[str]
  def parse_edited_report(content: bytes) -> EditedReport
  ```
  Task 8 (`recreate_order_from_report`) — единственный потребитель.

- [ ] **Step 1: Падающий тест** (дописать в `tests/test_report_xlsx.py`)

```python
from report_xlsx import EditedReportError, parse_edited_report


def test_parse_edited_report_roundtrip():
    res = _sample_result()
    content = build_excel_report(res)

    # «Правка»: толщина 1.0, добавим «да» у перекупной позиции
    wb = load_workbook(io.BytesIO(content))
    wb[SHEET_LOADED].cell(row=2, column=7, value=1.0)  # Толщина
    wb[SHEET_TRADING].cell(row=2, column=10, value="да")  # Включить в заказ
    buf = io.BytesIO()
    wb.save(buf)

    edited = parse_edited_report(buf.getvalue())
    assert edited.replaced_order == "839"
    assert len(edited.loaded_rows) == 1
    row = edited.loaded_rows[0]
    assert row["article"] == "1-1-1"
    assert row["params"] == {"A0": 300, "B0": 200}
    assert row["thickness"] == 1.0
    assert row["material_code"] == "1"
    assert row["connection_0"] == "6"
    assert len(edited.include_rows) == 1
    assert edited.include_rows[0]["name"] == "Гибкий воздуховод Ф125"
    assert len(edited.skipped_rows) == 4  # все пропущенные/перекупные


def test_parse_edited_report_rejects_garbage():
    import pytest
    with pytest.raises(EditedReportError):
        parse_edited_report(b"not an xlsx")
```

- [ ] **Step 2: Убедиться что падает**

Run: `python -m pytest tests/test_report_xlsx.py -k parse_edited -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Реализация** — добавить в конец `report_xlsx.py`:

```python
class EditedReportError(ValueError):
    """Отредактированный отчёт не читается (формат/значения)."""


INCLUDE_YES = {"да", "yes", "1", "+"}


@dataclass
class EditedReport:
    loaded_rows: List[dict]
    include_rows: List[dict]
    skipped_rows: List[dict]
    replaced_order: Optional[str] = None


def _s(value) -> str:
    return "" if value is None else str(value).strip()


def _num(value, ctx: str) -> float:
    try:
        return float(str(value).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        raise EditedReportError(f"{ctx}: ожидалось число, получено {value!r}")


def parse_edited_report(content: bytes) -> EditedReport:
    """Прочитать отредактированный отчёт обратно в позиции для пересоздания заказа."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as e:
        raise EditedReportError(f"Не удалось прочитать xlsx: {e}")

    for name in (SHEET_LOADED, SHEET_ERRORS):
        if name not in wb.sheetnames:
            raise EditedReportError(f"В файле нет листа «{name}» — это не отчёт системы")

    replaced_order = None
    loaded_rows: List[dict] = []
    include_rows: List[dict] = []
    skipped_rows: List[dict] = []

    # --- Загружено ---
    ws = wb[SHEET_LOADED]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    if not any(any(c is not None and str(c).strip() for c in r) for r in rows):
        raise EditedReportError("Лист «Загружено» пуст — нечего пересоздавать")
    for idx, r in enumerate(rows, start=2):
        if not any(c is not None and str(c).strip() for c in r):
            continue  # удалённая пользователем строка — пропускаем молча
        ctx = f"Лист «{SHEET_LOADED}», строка {idx}"
        article = _s(r[0])
        if not article:
            raise EditedReportError(f"{ctx}: пустой артикул")
        params = {}
        for key, cell in (("A0", r[1]), ("B0", r[2]), ("D0", r[3])):
            if cell is not None and str(cell).strip():
                params[key] = _num(cell, ctx)
        if not params:
            raise EditedReportError(f"{ctx}: нет ни одного размера (A/B/D)")
        quantity = _num(r[4], ctx)
        if quantity <= 0:
            raise EditedReportError(f"{ctx}: количество должно быть > 0")
        q = int(quantity) if float(quantity).is_integer() else quantity
        thickness = _num(r[6], ctx)
        if thickness <= 0:
            raise EditedReportError(f"{ctx}: толщина должна быть > 0")
        loaded_rows.append({
            "article": article,
            "params": params,
            "quantity": q,
            "material_code": _s(r[5]) or "1",
            "thickness": thickness,
            "connection_0": _s(r[7]), "connection_1": _s(r[8]),
            "connection_2": _s(r[9]), "connection_3": _s(r[10]),
            "system": _s(r[11]),
            "comment": _s(r[12]),
        })

    # --- Пропущено / Перекупное ---
    for sheet_name, headers in ((SHEET_SKIPPED, SKIPPED_HEADERS),
                                (SHEET_TRADING, TRADING_HEADERS)):
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        inc_col = headers.index("Включить в заказ")
        for idx, r in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            if not any(c is not None and str(c).strip() for c in r):
                continue
            name = _s(r[0])
            if not name:
                continue
            item = {
                "name": name,
                "size": _s(r[2]),
                "unit": _s(r[3] if sheet_name == SHEET_SKIPPED else r[6]) or "шт",
                "quantity": 1.0,
                "material": _s(r[4] if sheet_name == SHEET_SKIPPED else r[3]) or "оцинкованная",
                "thickness": 0.8,
            }
            q_raw = r[2] if sheet_name == SHEET_SKIPPED else r[5]
            if q_raw is not None and str(q_raw).strip():
                try:
                    item["quantity"] = float(str(q_raw).replace(",", "."))
                except ValueError:
                    pass
            skipped_rows.append(item)
            if _s(r[inc_col]).lower() in INCLUDE_YES:
                include_rows.append(item)

    # --- Ошибки 1С: номер заменяемого заказа из сводки ---
    ws = wb[SHEET_ERRORS]
    for r in ws.iter_rows(values_only=True):
        if r and _s(r[0]) == ORDER_NUMBER_LABEL and len(r) > 1:
            replaced_order = _s(r[1]) or None
            break

    return EditedReport(
        loaded_rows=loaded_rows,
        include_rows=include_rows,
        skipped_rows=skipped_rows,
        replaced_order=replaced_order,
    )
```

Также в шапку `report_xlsx.py` добавить импорты: `from dataclasses import dataclass`.

- [ ] **Step 4: Тесты**

Run: `python -m pytest tests/test_report_xlsx.py -v`
Expected: PASS (включая тесты Task 2).

- [ ] **Step 5: Commit**

```bash
git add report_xlsx.py tests/test_report_xlsx.py
git commit -m "feat(report): parse_edited_report reads back the edited xlsx"
```

---

### Task 8: recreate_order_from_report — пересоздание заказа

**Files:**
- Modify: `bitrix_bot/pipeline.py` (добавить функцию)
- Test: `tests/test_bitrix_pipeline.py` (дописать)

**Interfaces:**
- Consumes: `parse_edited_report`, `EditedReportError` (Task 7); `process_rows` из `process_specification_table` (существующий, возвращает `(xml, skipped, success)`); `build_positions` из `json_positions` (существующий); `load_order_to_1c` (существующий).
- Produces:
  ```python
  def recreate_order_from_report(content: bytes, execute_url: str,
                                 base_comment: str = "",
                                 timeout: float = 280.0) -> PipelineResult
  ```
  Комментарий нового заказа: `base_comment + " | Заменяет заказ №N (исправлено из отчёта)"`. Используется в Task 9 (бот) и Task 10 (веб).

- [ ] **Step 1: Падающий тест** (дописать в `tests/test_bitrix_pipeline.py`)

```python
def test_recreate_order_from_report(monkeypatch):
    import report_xlsx

    res_in = PipelineResult(
        file_name="spec.pdf", order_number="839",
        loaded=[{"article": "1-1-1", "params": {"A0": 300, "B0": 200},
                 "quantity": 2, "material_code": "1", "thickness": 0.8,
                 "connection_0": "6", "connection_1": "2",
                 "connection_2": "0", "connection_3": "0",
                 "system": "", "comment": "Воздуховод 300x200"}],
        skipped=[{"name": "Гибкий воздуховод Ф125", "size": "Ф125", "unit": "м",
                  "quantity": 10, "material": "оцинкованная", "thickness": 0.8,
                  "reason": "Покупная позиция (…) — завод не производит"}],
    )
    xlsx_bytes = report_xlsx.build_excel_report(res_in)

    wb = report_xlsx.load_workbook(io.BytesIO(xlsx_bytes))
    wb[report_xlsx.SHEET_LOADED].cell(row=2, column=7, value=1.0)  # толщина 1.0
    buf = io.BytesIO()
    wb.save(buf)

    captured = {}

    def fake_load(positions, execute_url, order_comment, timeout=280.0):
        captured["positions"] = positions
        captured["comment"] = order_comment
        return {"order_number": "840", "errors": [], "warnings": [], "raw": "ЗАКАЗ 840 | строк=1 | ошибок=0 | предупр=0"}

    from bitrix_bot import pipeline as pipeline_mod
    monkeypatch.setattr(pipeline_mod, "load_order_to_1c", fake_load)

    out = pipeline_mod.recreate_order_from_report(
        buf.getvalue(), "http://x", base_comment="Задача Битрикс24 №42: Т")

    assert captured["comment"] == "Задача Битрикс24 №42: Т | Заменяет заказ №839 (исправлено из отчёта)"
    assert captured["positions"][0]["thickness"] == 1.0
    assert captured["positions"][0]["material"] == "Рулон оц.(08ПС)1.00"
    assert out.order_number == "840"
    assert len(out.loaded) == 1
    assert len(out.skipped) == 1  # перекупное осталось пропущенным
```

(Добавить в начало файла импорты `import io` и `from bitrix_bot.pipeline import PipelineResult` — если ещё нет.)

- [ ] **Step 2: Убедиться что падает**

Run: `python -m pytest tests/test_bitrix_pipeline.py -k recreate -v`
Expected: FAIL — `AttributeError: module 'bitrix_bot.pipeline' has no attribute 'recreate_order_from_report'`.

- [ ] **Step 3: Реализация** — добавить в `bitrix_bot/pipeline.py`:

```python
def recreate_order_from_report(
    content: bytes,
    execute_url: str,
    base_comment: str = "",
    timeout: float = 280.0,
) -> PipelineResult:
    """Пересоздать заказ из отредактированного Excel-отчёта (round-trip).

    Лист «Загружено» → позиции напрямую; позиции с «Включить в заказ» = да
    из «Пропущено»/«Перекупного» проходят повторный разбор process_rows
    (подбор аналога). Создаётся НОВЫЙ заказ; старый (replaced_order) удаляет
    менеджер вручную — в 1С ничего не обновляем.
    """
    from process_specification_table import process_rows
    from json_positions import build_positions
    from report_xlsx import EditedReportError, parse_edited_report

    edited = parse_edited_report(content)

    extra_success: List[Dict[str, Any]] = []
    extra_skipped: List[Dict[str, Any]] = []
    if edited.include_rows:
        _, extra_skipped, extra_success = process_rows(edited.include_rows)

    success = edited.loaded_rows + extra_success
    if not success:
        raise EditedReportError(
            "Нечего загружать: лист «Загружено» пуст, а включённые позиции "
            "не дали ни одного аналога."
        )

    comment = base_comment or "Заказ из Excel-отчёта"
    if edited.replaced_order:
        comment += f" | Заменяет заказ №{edited.replaced_order} (исправлено из отчёта)"

    positions = build_positions(success)
    out = load_order_to_1c(positions, execute_url, comment, timeout=timeout)
    return PipelineResult(
        file_name=f"edited_report_{edited.replaced_order or 'new'}",
        order_number=out["order_number"],
        loaded=success,
        skipped=edited.skipped_rows + extra_skipped,
        errors_1c=out["errors"],
        warnings_1c=out["warnings"],
        raw=out["raw"],
    )
```

- [ ] **Step 4: Тесты**

Run: `python -m pytest tests/test_bitrix_pipeline.py -v`
Expected: PASS. (Если `parse_1c_result` не распарсит тестовый raw — поправить raw-строку в тесте под формат «ЗАКАЗ 840 | ...»; парсер ищет `ЗАКАЗ\s+(\S+)`.)

- [ ] **Step 5: Commit**

```bash
git add bitrix_bot/pipeline.py tests/test_bitrix_pipeline.py
git commit -m "feat(pipeline): recreate_order_from_report round-trip"
```

---

### Task 9: Бот принимает .xlsx в чате → пересоздание заказа

**Files:**
- Modify: `bitrix_bot/events.py` (`_first_file`, `_looks_pdf`, `find_pdf`)
- Modify: `bitrix_bot/server.py` (webhook-ветка после parse_event; handler из Task 5)
- Test: `tests/test_bitrix_server.py` (дописать)

**Interfaces:**
- Consumes: `recreate_order_from_report` (Task 8), `BotEvent.file_name`/`file_url`.
- Produces: `find_pdf(client, event, ext: str = ".pdf")` — при `ext=".xlsx"` ищет xlsx (имя метода сохранено для совместимости). webhook маршрутизирует по расширению `event.file_name`; handler (Task 5) тоже ветвится по `job.file_name`.

- [ ] **Step 1: Падающий тест** (дописать в `tests/test_bitrix_server.py`)

```python
def test_webhook_routes_xlsx_to_recreate(monkeypatch):
    from bitrix_bot.pipeline import PipelineResult
    import bitrix_bot.server as server_mod

    captured = {}

    def fake_recreate(content, execute_url, base_comment="", timeout=280.0):
        captured["called"] = True
        return PipelineResult(file_name="edited", order_number="840",
                              loaded=[{"article": "1-1-1", "params": {},
                                       "quantity": 2}],
                              skipped=[], errors_1c=[], warnings_1c=[])

    monkeypatch.setattr(server_mod, "recreate_order_from_report", fake_recreate)

    sent = []

    class _Client:
        def send_message(self, dialog_id, text, bot_id=None):
            sent.append(text)

        def get_task_title(self, task_id):
            return "Т"

        def download_file(self, url):
            return b"xlsx-bytes"

    from fastapi.testclient import TestClient
    cfg = BotConfig(execute_code_url="http://x", task_comment_prefix="Задача Битрикс24")
    app = server_mod.create_app(cfg=cfg, client=_Client(), start_worker=False)
    client = TestClient(app)

    payload = {
        "event": "ONIMBOTMESSAGEADD",
        "data": {
            "PARAMS": {
                "DIALOG_ID": "task|42",
                "MESSAGE_ID": "1",
                "FROM_USER_ID": "7",
                "FILES": [{"url": "http://f/report.xlsx", "name": "report_order_839.xlsx"}],
            },
            "BOT": [{"BOT_ID": "5"}],
        },
    }
    resp = client.post("/webhook/bot", json=payload)
    assert resp.status_code == 200
    # webhook вернул ok; задание ушло в очередь, воркер обработает в фоне —
    # здесь проверяем только маршрутизацию события (file_name xlsx дошёл)
```

(Импорт `BotConfig` — тот, что уже используется в файле тестов.)

Плюс тест `find_pdf` с xlsx (дописать в `tests/test_bitrix_server.py` или создать
`tests/test_bitrix_events.py`):

```python
def test_find_pdf_finds_xlsx_when_asked():
    from bitrix_bot.events import BotEvent, find_pdf

    class _Client:
        def download_file(self, url):
            return b"data"

    ev = BotEvent(dialog_id="task|42", message_id="1", user_id=7, text="",
                  task_id=42, bot_id=5,
                  file_url="http://f/report.xlsx", file_name="report.xlsx")
    data, name = find_pdf(_Client(), ev, ext=".xlsx")
    assert data == b"data" and name == "report.xlsx"

    # тот же event с ext=.pdf не должен подхватить xlsx
    import pytest
    from bitrix_bot.events import PdfNotFound
    ev2 = BotEvent(dialog_id="task|42", message_id="1", user_id=7, text="",
                   task_id=42, bot_id=5, file_url=None, file_name=None)
    with pytest.raises(PdfNotFound):
        find_pdf(_Client(), ev2, ext=".pdf")
```

- [ ] **Step 2: Убедиться что падает**

Run: `python -m pytest tests/test_bitrix_server.py -k "routes_xlsx or finds_xlsx" -v`
Expected: FAIL — `find_pdf() got an unexpected keyword argument 'ext'` / xlsx отфильтрован `_looks_pdf`.

- [ ] **Step 3: Реализация events.py**

Заменить `_looks_pdf` и `_first_file` на параметризованные по расширению:

```python
def _looks_like(name: Optional[str], exts: tuple) -> bool:
    """Фильтр: без имени пропускаем (best effort), иначе проверяем расширение."""
    return not name or name.lower().endswith(exts)


def _first_file(params: dict, exts: tuple = (".pdf",)) -> Tuple[Optional[str], Optional[str]]:
    """Вытащить (url, имя) файла с нужным расширением из params сообщения."""
    files = params.get("FILES") or params.get("files") or []
    if isinstance(files, list) and files:
        f0 = files[0] or {}
        url = (f0.get("url") or f0.get("urlDownload") or f0.get("downloadUrl")
               or f0.get("DOWNLOAD_URL"))
        name = f0.get("name") or f0.get("FILE_NAME") or "document"
        if url and _looks_like(name, exts):
            return url, name
    for key in ("FILE_URL", "DOWNLOAD_URL", "ATTACH_URL"):
        if params.get(key):
            name = params.get("FILE_NAME") or "document"
            if _looks_like(name, exts):
                return params[key], name
    attach = params.get("ATTACH")
    if isinstance(attach, list):
        for block in attach:
            if isinstance(block, dict) and block.get("LINK"):
                name = block.get("NAME") or "document"
                if _looks_like(name, exts):
                    return block["LINK"], name
    return None, None
```

В `parse_event` заменить оба вызова `_first_file(...)` на `_first_file(..., exts=(".pdf", ".xlsx"))`
(reply-контекст и params). В `find_pdf` добавить параметр и фильтр по имени:

```python
def find_pdf(client, event: BotEvent, ext: str = ".pdf") -> Tuple[bytes, str]:
    """Скачать файл с расширением ext: из сообщения, иначе из истории."""
    if event.file_url and _looks_like(event.file_name, (ext,)):
        url = event.file_url
        # голые ссылки disk требуют сессии — берём подписанный URL
        m = re.search(r"fileId=(\d+)", url)
        if m and hasattr(client, "resolve_download_url"):
            url = client.resolve_download_url(int(m.group(1)))
        return client.download_file(url), event.file_name or f"document{ext}"
    for msg in client.get_dialog_messages(event.dialog_id, limit=30):
        url, name = _file_from_message(msg, ext)
        if url:
            return client.download_file(url), name or f"document{ext}"
    raise PdfNotFound(
        f"Не нашёл файл {ext}: ответьте (reply) на сообщение с файлом и упомяните меня ещё раз."
    )
```

И `_file_from_message` — добавить `ext: str = ".pdf"` и передать в `_first_file`.
(Старые вызовы без ext работают как раньше — `.pdf` по умолчанию.)

**Реализация server.py** — в webhook после `event = parse_event(payload)`:

```python
            file_ext = ("xlsx", "xls") if (
                event.file_name or "").lower().endswith((".xlsx", ".xls")) else (".pdf",)
            try:
                file_bytes, file_name = find_pdf(client, event, ext=file_ext[0] if len(file_ext) == 1 else ".pdf")
```

Проще и явнее — заменить блок `pdf_bytes, file_name = find_pdf(client, event)` на:

```python
            is_excel = (event.file_name or "").lower().endswith((".xlsx", ".xls"))
            try:
                if is_excel:
                    pdf_bytes, file_name = find_pdf(client, event, ext=".xlsx")
                else:
                    pdf_bytes, file_name = find_pdf(client, event)
            except PdfNotFound:
                background.add_task(client.send_message, event.dialog_id,
                                    WELCOME_TEXT, bot_id=event.bot_id)
                return JSONResponse({"ok": True})
```

И в `make_handler` (Task 5) добавить ветку round-trip — заменить строку `res = run_pipeline(...)` на:

```python
        if job.file_name.lower().endswith((".xlsx", ".xls")):
            res = recreate_order_from_report(
                data, cfg.execute_code_url, job.order_comment,
                timeout=cfg.request_timeout,
            )
        else:
            res = run_pipeline(
                data, job.file_name, job.order_comment,
                cfg.execute_code_url, timeout=cfg.request_timeout,
            )
```

(Импорт: `from bitrix_bot.pipeline import recreate_order_from_report, run_pipeline`.)

- [ ] **Step 4: Тесты**

Run: `python -m pytest tests/test_bitrix_server.py tests/test_bitrix_pipeline.py -v`
Expected: PASS. ВАЖНО: если существующие тесты `parse_event`/`find_pdf` ломаются из-за
расширения фильтра на `.xlsx` — обновить их под новое поведение (xlsx теперь
легитимное вложение).

- [ ] **Step 5: Commit**

```bash
git add bitrix_bot/events.py bitrix_bot/server.py tests/test_bitrix_server.py
git commit -m "feat(bot): accept edited xlsx report in chat to recreate order"
```

---

### Task 10: Веб: вкладка «Пересоздать заказ из отчёта»

**Files:**
- Modify: `web_app.py` (новая функция `_render_recreate_tab` + `main()`)
- Test: нет (Streamlit; ручной смоук в Step 4)

**Interfaces:**
- Consumes: `parse_edited_report`, `EditedReportError` (Task 7), `build_excel_report` (Task 2), `recreate_order_from_report` (Task 8), `get_config` из `config.py` (существующий).
- Produces: вкладка в `main()`.

- [ ] **Step 1: Импорты в `web_app.py`**:

```python
from config import get_config
from report_xlsx import EditedReportError, parse_edited_report
from bitrix_bot.pipeline import recreate_order_from_report
```

- [ ] **Step 2: Добавить функцию** (перед `def main()`):

```python
def _execute_code_url() -> str:
    """URL MCP execute_code — как в bitrix_bot.config, без секретов."""
    cfg = get_config()
    mcp_url = (cfg.get("mcp") or {}).get("url", "")
    if mcp_url:
        return mcp_url.rstrip("/").rsplit("/", 1)[0] + "/api/execute_code"
    return "http://127.0.0.1:6005/api/execute_code"


def _render_recreate_tab(tab):
    """Round-trip: загрузить исправленный отчёт -> пересоздать заказ в 1С."""
    with tab:
        st.subheader("Пересоздать заказ из отчёта")
        st.caption(
            "Загрузите Excel-отчёт, который вы скачали и правили "
            "(толщина, материал, удалённые/включённые позиции). "
            "Будет создан НОВЫЙ заказ; старый пометьте на удаление в 1С вручную."
        )
        uploaded = st.file_uploader(
            "Исправленный отчёт (.xlsx)", type=["xlsx"],
            key="recreate_report",
        )
        if uploaded is None:
            return

        content = uploaded.getvalue()
        try:
            edited = parse_edited_report(content)
        except EditedReportError as e:
            st.error(str(e))
            return

        c1, c2, c3 = st.columns(3)
        c1.metric("Позиций в новом заказе",
                  len(edited.loaded_rows) + len(edited.include_rows))
        c2.metric("Включено обратно", len(edited.include_rows))
        c3.metric("Заменяет заказ", edited.replaced_order or "—")

        if edited.include_rows:
            st.write("**Позиции на включение (повторный подбор аналога):**")
            st.dataframe(pd.DataFrame(edited.include_rows), width="stretch")

        if not edited.loaded_rows and not edited.include_rows:
            st.error("Нечего загружать: лист «Загружено» пуст и позиции на включение отсутствуют.")
            return

        if st.button("⚙️ Создать новый заказ", type="primary"):
            with st.spinner("Создаю заказ в 1С..."):
                try:
                    res = recreate_order_from_report(
                        content, _execute_code_url(),
                        base_comment="Пересоздан из отчёта (web)",
                    )
                except Exception as e:
                    st.error(f"Не удалось создать заказ: {e}")
                    return
            st.success(f"Создан заказ №{res.order_number} "
                       f"(загружено {len(res.loaded)}, пропущено {len(res.skipped)})")
            st.download_button(
                label="⬇️ Скачать новый отчёт.xlsx",
                data=build_excel_report(res),
                file_name=f"report_order_{res.order_number or 'new'}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            if res.errors_1c:
                st.warning("Ошибки 1С: " + "; ".join(res.errors_1c))
```

- [ ] **Step 3: Подключить вкладку в `main()`** — заменить блок tabs:

```python
    tab_main, tab_recreate, tab_prices = st.tabs(
        ["Спецификация → XML", "Пересоздать заказ из отчёта", "Цены на перекупное оборудование"]
    )

    _render_main_tab(tab_main)
    _render_recreate_tab(tab_recreate)

    with tab_prices:
        skipped_for_prices = st.session_state.get("skipped_for_prices", [])
        render_price_search_tab(skipped_for_prices)
```

- [ ] **Step 4: Проверка**

Run: `python -m py_compile web_app.py`
Expected: без ошибок.

Run: `streamlit run web_app.py` → вкладка «Пересоздать заказ из отчёта» →
загрузить отчёт, сохранённый на смоуке Task 6 → убедиться, что сводка и кнопка
появляются; при живом MCP (http://127.0.0.1:6005) — создать заказ.
Если MCP недоступен — ошибка должна показаться аккуратно (`st.error`), без трейсбека.

- [ ] **Step 5: Commit**

```bash
git add web_app.py
git commit -m "feat(web): recreate order from edited excel report tab"
```

---

### Task 11: Документация и финальный регресс

**Files:**
- Modify: `docs/BACKLOG.md` (закрыть пункт (а) у строки ~297, если реализован)
- Modify: `bitrix_bot/README.md` (кратко: бот шлёт Excel-отчёт файлом и принимает исправленный .xlsx)

**Interfaces:** нет (только доки).

- [ ] **Step 1: Обновить `docs/BACKLOG.md`** — у пункта «прикрепление файла-отчёта в чат вместо нарезки сообщений» (строка ~297) отметить: реализовано (`report_xlsx.py` + `BitrixClient.send_file`), см. спеку `docs/superpowers/specs/2026-09-07-excel-report-design.md`.

- [ ] **Step 2: Обновить `bitrix_bot/README.md`** — в раздел про отчёт добавить 2 строки:
  - «Отчёт приходит файлом `report_order_<N>.xlsx` (4 листа) + краткое резюме; при сбое доставки — текстовая нарезка (фолбэк)».
  - «Ответьте на сообщение исправленным .xlsx-отчётом — бот пересоздаст заказ (новый номер; старый удалите вручную)».

- [ ] **Step 3: Полный регресс**

Run: `python -m pytest tests/ -q`
Expected: все тесты PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/BACKLOG.md bitrix_bot/README.md
git commit -m "docs: excel report delivery and round-trip usage notes"
```

---

## Self-Review (выполнено при написании)

- **Spec coverage:** генерация 4 листов → Task 2; material/thickness в скипах → Task 1; соединения + «Включить в заказ» + «Номер заказа» в формате → Tasks 2/7; битрикс файл+резюме+фолбэк → Tasks 3-5; веб-кнопка → Task 6; parse_edited_report → Task 7; recreate (новый заказ, «Заменяет №N», включение через process_rows) → Task 8; приём xlsx в чате → Task 9; веб-вкладка → Task 10; тесты на всё — в каждом таске; доки → Task 11. Закрыто.
- **Type consistency:** `build_excel_report(res)->bytes`, `is_trading_skip(dict)->bool`, `build_summary(res)->str`, `send_file(dialog_id, file_name, content, caption, bot_id=None)`, `EditedReport(loaded_rows, include_rows, skipped_rows, replaced_order)`, `parse_edited_report(bytes)->EditedReport`, `recreate_order_from_report(content, execute_url, base_comment="", timeout=280.0)->PipelineResult`, `find_pdf(client, event, ext=".pdf")` — имена совпадают во всех тасках.
- **Известные отложенные проверки** (зафиксированы, не плейсхолдеры): формат `ATTACH` в Bitrix REST проверяется smoke-тестом Task 4 против реального портала, есть фолбэк-ссылка; Streamlit-части (Tasks 6, 10) верифицируются ручным запуском — автотестов на Streamlit-виджеты в проекте нет.
