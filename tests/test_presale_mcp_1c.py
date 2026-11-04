"""Тесты батч-клиента цен 1С через MCP execute_code (presale/mcp_1c.py)."""
import json
import logging
from decimal import Decimal

import httpx
import pytest

from presale.mcp_1c import (
    BackendUnavailableError,
    OneCPriceBackend,
    parse_price_response,
)

URL = "http://localhost:6005/api/execute_code"


def test_parse_price_response():
    text = "Успех=true ## Решетка LITENED 70-40\t1250.50\nВентилятор ВК 300\t18900"
    assert parse_price_response(text) == {
        "Решетка LITENED 70-40": Decimal("1250.50"),
        "Вентилятор ВК 300": Decimal("18900"),
    }


def test_parse_price_response_multi_separator():
    # хвост может быть и через " ## " между строками — как в pipeline.py
    text = "head ## А\t10 ## Б\t20"
    assert parse_price_response(text) == {"А": Decimal("10"), "Б": Decimal("20")}


def test_parse_price_response_no_tail():
    assert parse_price_response("Успех=true | строк=0") == {}


def test_parse_price_response_skips_malformed_lines(caplog):
    text = "head ## А\t10\nбез таба\n\t30\nБ\tне-цена\n\nВ\t0"
    with caplog.at_level(logging.WARNING, logger="presale.mcp_1c"):
        assert parse_price_response(text) == {"А": Decimal("10"), "В": Decimal("0")}
    assert any("не-цена" in r.message or "без таба" in r.message for r in caplog.records)


def test_search_1c_batches_single_http_call(httpx_mock):
    httpx_mock.add_response(json={"success": True, "data": "Успех=true ## А\t10\nБ\t20"})
    backend = OneCPriceBackend(URL)
    assert backend.search_1c(["А", "Б"]) == {"А": Decimal("10"), "Б": Decimal("20")}
    assert len(httpx_mock.get_requests()) == 1


def test_search_1c_sends_bsl_payload_with_names(httpx_mock):
    httpx_mock.add_response(json={"success": True, "data": "Успех=true ## А\t10"})
    backend = OneCPriceBackend(URL)
    backend.search_1c(["Решетка LITENED 70-40"])
    request = httpx_mock.get_requests()[0]
    assert request.url == URL
    body = json.loads(request.read())
    code = body["code"]
    assert "Решетка LITENED 70-40" not in code  # имена — через base64, без инъекций
    assert "ПрочитатьJSON" in code


def test_search_1c_tunnel_envelope_result(httpx_mock):
    httpx_mock.add_response(json={"result": "Успех=true ## А\t10"})
    assert OneCPriceBackend(URL).search_1c(["А"]) == {"А": Decimal("10")}


def test_envelope_error_raises_unavailable(httpx_mock):
    httpx_mock.add_response(json={"success": False, "error": "boom"})
    with pytest.raises(BackendUnavailableError):
        OneCPriceBackend("http://x").search_1c(["А"])


def test_network_failure_raises_unavailable(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(BackendUnavailableError):
        OneCPriceBackend(URL).search_1c(["А"])


def test_business_answer_without_prices_is_empty_dict(httpx_mock):
    httpx_mock.add_response(json={"success": True, "data": "Успех=true | не найдено"})
    assert OneCPriceBackend(URL).search_1c(["А"]) == {}


def test_empty_names_no_http_call(httpx_mock):
    assert OneCPriceBackend(URL).search_1c([]) == {}
    assert len(httpx_mock.get_requests()) == 0


def test_uses_configured_timeout(httpx_mock):
    httpx_mock.add_response(json={"success": True, "data": "Успех=true ## А\t10"})
    OneCPriceBackend(URL, timeout=5.0).search_1c(["А"])
    # httpx 0.28 раскладывает скаляр в {connect, read, write, pool}
    assert httpx_mock.get_requests()[0].extensions["timeout"]["connect"] == 5.0
