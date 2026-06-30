# Архитектура веб-приложения «PDF-спецификация → XML для 1С»

## Цель
Пользователь загружает PDF (или CSV) со спецификацией вентиляции, выбирает нужные страницы, видит распознанную таблицу, редактирует при необходимости и получает готовый XML для загрузки в 1С.

## Текущий конвейер (уже реализован)
- `process_specification_table.py` — читает CSV/Excel, распознаёт воздуховоды, фасонные изделия, КСД-адаптеры, генерирует XML и JSON с пропущенными строками.
- `generate_order_xml.py` — строит XML по строкам.
- `product_article_mapping.json` — 120 артикулов 1С с параметрами.
- `match_description_to_product.py` — текстовое сопоставление описания с топ-5 артикулов.

Ожидаемые столбцы входной таблицы:
- `name` — наименование (например, «Воздуховод из оцинкованной стали…», «Отвод круглый 90 градусов»).
- `size` — размер (`100`, `300x200`, `D160`, `4АПН 600x600 + КСД 200`, `ДПУ-М 100`).
- `unit` — единица (`м`, `шт`, `м2`).
- `quantity` — количество.
- `material` — тип материала (`оцинкованная`, `нержавеющая`, `черная`).
- `thickness` — толщина (`0.8`, `1.0`).

## Новые компоненты

### 1. `pdf_spec_extractor.py`
Модуль извлечения таблиц из PDF.

```python
def extract_tables_from_pdf(pdf_path: str, pages: Optional[List[int]] = None) -> Dict[int, List[pd.DataFrame]]:
    """Возвращает словарь {номер_страницы: [таблица1, таблица2, ...]}."""

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Переименовывает столбцы PDF-таблицы в name/size/unit/quantity/material/thickness по ключевым словам."""

def df_to_spec_rows(df: pd.DataFrame) -> List[dict]:
    """Превращает DataFrame в список словарей, пригодных для process_specification_table."""
```

Технология: **PyMuPDF** (`fitz.Page.find_tables()`), уже установлен в системе.
Fallback: если таблица не найдена, извлекаем текст страницы построчно и пытаемся разобрать через регулярные выражения.

### 2. `web_app.py`
Веб-интерфейс на **Streamlit**.

Экраны:
1. Загрузка файла (PDF/CSV/XLSX).
2. Выбор страниц PDF (мультиселект, по умолчанию все).
3. Предпросмотр извлечённых таблиц (таблица за таблицей, с возможностью выбора).
4. Редактирование объединённой таблицы (data_editor).
5. Генерация XML — вызов `process_specification_table.process_rows(rows, header)`.
6. Скачивание `order.xml` и `skipped.json`.

Зависимости веб-приложения: `streamlit`, `pandas`.

### 3. Интеграция с конвейером
В `process_specification_table.py` выделить функцию:

```python
def process_rows(rows: List[dict], header: Optional[dict] = None) -> Tuple[str, List[dict]]:
    """Принимает уже распарсенные строки, возвращает XML-строку и список пропущенных."""
```

Это позволит `web_app.py` не писать промежуточный CSV, а сразу передавать строки.

## Правила маппинга столбцов PDF

| Целевой столбец | Ключевые слова в заголовке PDF |
|-----------------|--------------------------------|
| `name` | наименование, изделие, описание, позиция, продукт |
| `size` | размер, сечение, диаметр, габарит, типоразмер |
| `unit` | ед.изм, единица, ед, unit, изм |
| `quantity` | количество, кол-во, qty, кол |
| `material` | материал, сталь, мат |
| `thickness` | толщина, толщ, мм |

Если столбец не найден — подставляется значение по умолчанию:
- `material` = «оцинкованная»
- `thickness` = 0.8
- `unit` = «шт» или «м» (определяется по `size`)

## Поток данных

```
PDF/CSV/XLSX
    ↓
[web_app.py]
    ↓
extract_tables_from_pdf() / pd.read_csv() / pd.read_excel()
    ↓
normalize_columns() → df_to_spec_rows()
    ↓
process_rows() → (xml_string, skipped)
    ↓
скачивание order.xml + skipped.json
```

## Ограничения
- Круглые диффузоры `ДПУ-М` пока не загружаются автоматически (нет артикула в `асПродукция`).
- Агрегатные строки фасонных изделий (`м2`, пустой размер) требуют ручной детализации.
- Точность распознавания таблиц из PDF зависит от качества PDF.

## Запуск

```bash
python -m venv venv
venv\Scripts\python -m pip install --upgrade pip
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\streamlit run web_app.py
```
