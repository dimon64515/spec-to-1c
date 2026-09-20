"""Тесты конфигурации битрикс-бота."""
import bitrix_bot.config as bc


def test_load_bot_config_merges_files(tmp_path):
    base = tmp_path / "config.yaml"
    base.write_text(
        """
bitrix:
  portal: "https://example.bitrix24.ru"
middleware:
  tmp_dir: "tmp/test"
  report_limit: 100
  request_timeout: 10
  task_comment_prefix: "Префикс"
""",
        encoding="utf-8",
    )
    local = tmp_path / "bitrix.local.yaml"
    local.write_text(
        """
bitrix:
  portal: "https://example.bitrix24.ru"
  incoming_webhook: "https://example.bitrix24.ru/rest/1/KEY/"
  app_client_id: "cid"
  app_client_secret: "sec"
middleware:
  webhook_verify_token: "tok"
  order_service_url: "https://srv1c/base/hs/Zakaz/NewZakaz"
  order_service_key: "secret"
  order_service_user: "HTTP_Service1"
  order_service_password: "pwd"
""",
        encoding="utf-8",
    )
    cfg = bc.load_bot_config(base, local)
    assert cfg.portal == "https://example.bitrix24.ru"
    assert cfg.incoming_webhook == "https://example.bitrix24.ru/rest/1/KEY/"
    assert cfg.client_id == "cid"
    assert cfg.client_secret == "sec"
    assert cfg.verify_token == "tok"
    assert cfg.report_limit == 100
    assert cfg.request_timeout == 10
    assert cfg.task_comment_prefix == "Префикс"
    assert cfg.execute_code_url == "http://127.0.0.1:6005/api/execute_code"
    assert cfg.tmp_dir == "tmp/test"
    assert cfg.order_service_url == "https://srv1c/base/hs/Zakaz/NewZakaz"
    assert cfg.order_service_key == "secret"
    assert cfg.order_service_user == "HTTP_Service1"
    assert cfg.order_service_password == "pwd"


def test_load_bot_config_order_service_defaults_empty(tmp_path):
    base = tmp_path / "config.yaml"
    base.write_text("bitrix:\n  portal: \"https://x.ru\"\nmiddleware: {}\n", encoding="utf-8")
    cfg = bc.load_bot_config(base, tmp_path / "nope.yaml")
    # без настройки HTTP-сервиса бот работает только через execute_code
    assert cfg.order_service_url == ""
    assert cfg.order_service_key == ""


def test_load_bot_config_missing_local_ok(tmp_path):
    base = tmp_path / "config.yaml"
    base.write_text("bitrix:\n  portal: \"https://x.ru\"\nmiddleware: {}\n", encoding="utf-8")
    cfg = bc.load_bot_config(base, tmp_path / "nope.yaml")
    assert cfg.verify_token == ""
    assert cfg.incoming_webhook == ""
    assert cfg.report_limit == 3500  # дефолт
    assert cfg.task_comment_prefix == "Задача Битрикс24"  # дефолт
