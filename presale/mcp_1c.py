"""Батч-клиент внутренних цен 1С через MCP execute_code.

Реализация протокола PriceBackend (presale/pricing.py): один HTTP-вызов
execute_url на весь расчёт — BSL-пayload перебирает массив имён, ищет
номенклатуру по наименованию (точное совпадение, фолбэк — подобие) и
возвращает последнюю цену. Ответ — текст с хвостом после ' ## ': построчно
'ИМЯ<TAB>ЦЕНА' (тот же формат хвоста, что в bitrix_bot/pipeline._parse_price_tail).

Транспорт и envelope по образцу bitrix_bot/pipeline._load_via_execute_code:
нативный HTTP-API MCPToolkit ({"success", "data"|"error"}) или MCP-прокси
({"result"}). success=false и любой сетевой сбой (httpx.HTTPError) —
BackendUnavailableError: вызывающий код ловит её и помечает строки 🔴,
не падая. Бизнес-ответ без цен — пустой dict, не исключение.
"""
import base64
import json
import logging
from decimal import Decimal, InvalidOperation

import httpx

logger = logging.getLogger(__name__)


class BackendUnavailableError(RuntimeError):
    """1С недоступна (сеть/таймаут/платформа) — строки помечаются красным."""


# Тройные одинарные кавычки: внутри BSL встречаются """ (экранированные
# кавычки 1С), которые рвут строку в двойных кавычках. Имена передаются
# base64-JSON — тот же приём, что в tools/as_order_loader/build_execute_payload.py:
# так имя с кавычками/переводами строк не может сломать строковый литерал.
BSL_TEMPLATE = '''
Данные64 = "{b64}";
ДанныеСтрока = ПолучитьСтрокуИзДвоичныхДанных(ПолучитьДвоичныеДанныеИзBase64Строки(Данные64));
Чтение = Новый ЧтениеJSON;
Чтение.УстановитьСтроку(ДанныеСтрока);
Имена = ПрочитатьJSON(Чтение);

Части = Новый Массив;
Для Каждого Имя Из Имена Цикл
    Номенклатура = Неопределено;

    // Точное совпадение наименования (без учёта регистра поиск даёт СокрЛП).
    Найдено = Справочники.Номенклатура.НайтиПоНаименованию(Имя, Истина);
    Если ЗначениеЗаполнено(Найдено) Тогда
        Номенклатура = Найдено;
    Иначе
        // Подобие: первое вхождение по шаблону "%имя%".
        ЗапросПоиск = Новый Запрос("ВЫБРАТЬ ПЕРВЫЕ 1 Н.Ссылка ИЗ Справочник.Номенклатура КАК Н ГДЕ Н.Наименование ПОДОБНО &Шаблон УПОРЯДОЧИТЬ ПО Н.Наименование");
        ЗапросПоиск.УстановитьПараметр("Шаблон", "%" + СтрЗаменить(Имя, """%""", """"") + "%");
        ВыборкаПоиск = ЗапросПоиск.Выполнить().Выбрать();
        Если ВыборкаПоиск.Следующий() Тогда
            Номенклатура = ВыборкаПоиск.Ссылка;
        КонецЕсли;
    КонецЕсли;

    Если Не ЗначениеЗаполнено(Номенклатура) Тогда
        Продолжить;
    КонецЕсли;

    // Последняя цена: срез последних типового регистра КА; если регистр
    // в базе называется иначе — строка просто не попадёт в ответ.
    Цена = Неопределено;
    Попытка
        ЗапросЦена = Новый Запрос("ВЫБРАТЬ Цены.Цена ИЗ РегистрСведений.ЦеныНоменклатуры.СрезПоследних(&Дата, Номенклатура = &Ном) КАК Цены");
        ЗапросЦена.УстановитьПараметр("Дата", ТекущаяДатаСеанса());
        ЗапросЦена.УстановитьПараметр("Ном", Номенклатура);
        ВыборкаЦена = ЗапросЦена.Выполнить().Выбрать();
        Если ВыборкаЦена.Следующий() Тогда
            Цена = ВыборкаЦена.Цена;
        КонецЕсли;
    Исключение
        Цена = Неопределено;
    КонецПопытки;

    Если Цена <> Неопределено И Цена > 0 Тогда
        Части.Добавить(Имя + Символ(9) + Формат(Цена, "ЧРД=.; ЧГ="));
    КонецЕсли;
КонецЦикла;

Результат = "Успех=true | найдено=" + Части.Количество() + " ## " + СтрСоединить(Части, Символы.ПС);
'''


def parse_price_response(text: str) -> dict[str, Decimal]:
    """Цены из хвоста ответа 1С (после ' ## '): построчно 'ИМЯ<TAB>ЦЕНА'.

    Без хвоста — пустой dict. Битые строки (нет TAB, непарсящаяся цена)
    пропускаются с предупреждением в лог — не роняют весь разбор.
    """
    prices: dict[str, Decimal] = {}
    parts = text.split(" ## ")
    for part in parts[1:]:
        for line in part.splitlines():
            line = line.strip()
            if not line:
                continue
            name, sep, raw_price = line.partition("\t")
            name = name.strip()
            raw_price = raw_price.strip()
            if not sep or not name:
                logger.warning("1С цены: пропущена битая строка %r", line)
                continue
            try:
                prices[name] = Decimal(raw_price)
            except InvalidOperation:
                logger.warning("1С цены: пропущена строка с ценой %r", line)
    return prices


class OneCPriceBackend:
    """PriceBackend через MCP execute_code; один HTTP-вызов на весь расчёт."""

    def __init__(self, execute_url: str, timeout: float = 120.0):
        self._execute_url = execute_url
        self._timeout = timeout

    def search_1c(self, names: list[str]) -> dict[str, Decimal]:
        """Вернуть {имя: цена} для найденных в 1С позиций; {} при миссе."""
        names = [n for n in names if n and n.strip()]
        if not names:
            return {}
        b64 = base64.b64encode(
            json.dumps(names, ensure_ascii=False).encode()
        ).decode()
        payload = {"code": BSL_TEMPLATE.format(b64=b64)}
        try:
            resp = httpx.post(self._execute_url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BackendUnavailableError(
                f"1С execute_code недоступен: {exc}"
            ) from exc
        data = resp.json()
        # Два envelope ответа (как в bitrix_bot/pipeline._load_via_execute_code):
        # нативный HTTP-API MCPToolkit {"success", "data"|"error"} и
        # MCP-прокси (tunnel) {"result": ...}.
        if isinstance(data, dict) and data.get("success") is False:
            raise BackendUnavailableError(
                f"1С execute_code: {data.get('error', data)}"
            )
        if isinstance(data, dict):
            text = str(data.get("data") or data.get("result") or data)
        else:
            text = str(data)
        return parse_price_response(text)
