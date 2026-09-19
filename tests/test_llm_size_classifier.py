"""LLM-фолбэк классификации размеров. Все тесты без сети/subprocess."""
import pytest

import llm_size_classifier as lsc


def test_get_llm_config_defaults(monkeypatch):
    cfg = lsc.get_llm_config()
    assert cfg["enabled"] is False
    assert cfg["backend"] == "kimi-cli"
    assert cfg["batch_size"] >= 1
    assert cfg["timeout"] > 0
    assert cfg["max_retries"] >= 0


def test_llm_enabled_requires_flag():
    assert lsc.llm_enabled({"enabled": False, "backend": "kimi-cli", "command": "kimi"}) is False
    assert lsc.llm_enabled({"enabled": True, "backend": "other", "command": "kimi"}) is False
    assert lsc.llm_enabled({"enabled": True, "backend": "kimi-cli", "command": ""}) is False


def test_llm_enabled_requires_command_on_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert lsc.llm_enabled({"enabled": True, "backend": "kimi-cli", "command": "kimi"}) is False
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/kimi")
    assert lsc.llm_enabled({"enabled": True, "backend": "kimi-cli", "command": "kimi"}) is True


def test_reason_constant():
    assert lsc.REASON_LLM_REJECTED == "LLM-классификация отклонена"
import json

import size_notations as szn


def _ok_runner(payload_map=None, fail_times=0):
    """Фейк-runner: принимает prompt, возвращает «stdout stream-json».
    payload_map: {подстрока_промпта: ответный контент}. fail_times: сколько
    первых вызовов бросить TimeoutError (проверка retries)."""
    state = {"calls": 0, "fails_left": fail_times}

    def runner(prompt, cfg):
        state["calls"] += 1
        if state["fails_left"] > 0:
            state["fails_left"] -= 1
            raise TimeoutError("kimi timeout")
        for needle, content in (payload_map or {}).items():
            if needle in prompt:
                return json.dumps(
                    {"role": "meta", "type": "system.version", "version": "0.41.0"}
                ) + "\n" + json.dumps({"role": "assistant", "content": content}, ensure_ascii=False)
        raise AssertionError("prompt не распознан: " + prompt[:200])

    return runner, state


CFG = {"enabled": True, "backend": "kimi-cli", "command": "kimi",
       "model": "", "batch_size": 2, "timeout": 30, "max_retries": 1}


def test_round_diameter_extracted_from_source_spans():
    content = json.dumps({"results": [
        {"format_class": "round_diameter",
         "spans": [{"role": "diameter", "start": 1, "end": 4}]}
    ]}, ensure_ascii=False)
    runner, state = _ok_runner({"Ø315": content})
    out = lsc.classify_sizes_batch(["Ø315"], cfg=CFG, runner=runner)
    r = out["Ø315"]
    assert r.status == "ok" and r.format_class == "round_diameter"
    assert r.dims == {"D0": 315.0} and r.section == "round" and r.adoptable


def test_rect_axb_with_length():
    content = json.dumps({"results": [
        {"format_class": "rect_axb",
         "spans": [{"role": "a", "start": 0, "end": 4},
                   {"role": "b", "start": 5, "end": 8},
                   {"role": "length", "start": 9, "end": 13}]}
    ]})
    runner, _ = _ok_runner({"1250x800-3000": content})
    r = lsc.classify_sizes_batch(["1250x800-3000"], cfg=CFG, runner=runner)["1250x800-3000"]
    assert r.dims == {"A0": 1250.0, "B0": 800.0, "L0": 3000.0}
    assert r.section == "rectangular" and r.adoptable


def test_non_digit_span_rejected():
    content = json.dumps({"results": [
        {"format_class": "round_diameter",
         "spans": [{"role": "diameter", "start": 0, "end": 2}]}
    ]})
    runner, _ = _ok_runner({"Ø315": content})
    r = lsc.classify_sizes_batch(["Ø315"], cfg=CFG, runner=runner)["Ø315"]
    assert r.status == "rejected" and not r.dims


def test_out_of_range_rejected_by_domain_validation():
    content = json.dumps({"results": [
        {"format_class": "round_diameter",
         "spans": [{"role": "diameter", "start": 1, "end": 3}]}
    ]})
    runner, _ = _ok_runner({"Ø25": content})
    r = lsc.classify_sizes_batch(["Ø25"], cfg=CFG, runner=runner)["Ø25"]
    assert r.status == "rejected"
    assert "валидац" in r.detail.lower() or "диапазон" in r.detail.lower()


def test_tee_ok_but_not_adoptable():
    # "315/315-160": 0-2 "315", 4-6 "315", 8-10 "160" — три числа тройника.
    content = json.dumps({"results": [
        {"format_class": "tee_axbxc",
         "spans": [{"role": "a", "start": 0, "end": 3},
                   {"role": "b", "start": 4, "end": 7},
                   {"role": "c", "start": 8, "end": 11}]}
    ]})
    runner, _ = _ok_runner({"315/315-160": content})
    r = lsc.classify_sizes_batch(["315/315-160"], cfg=CFG, runner=runner)["315/315-160"]
    assert r.status == "ok"                      # распознан и провалидирован
    assert r.dims == {"D0": 315.0, "D1": 315.0, "D2": 160.0}
    assert r.adoptable is False                  # в воздуховоды не усыновляется
    assert "не усыновляется" in r.detail


def test_batching_respects_batch_size():
    import json as _json
    import re as _re

    def runner(prompt, cfg):
        # Строки в промпте нумерованы: «N: "строка"». Для каждой свой результат
        # со span на её ведущие цифры — так dims зависят от реального входа.
        strings = []
        for line in prompt.splitlines():
            m = _re.match(r"^\d+: (\".*\")$", line)
            if m:
                strings.append(_json.loads(m.group(1)))
        results = []
        for s in strings:
            m = _re.match(r"\d+", s)
            results.append({"format_class": "round_diameter",
                            "spans": [{"role": "diameter", "start": 0, "end": m.end()}]})
        return _json.dumps({"role": "assistant", "content": _json.dumps({"results": results})})

    out = lsc.classify_sizes_batch(["315a", "400a", "500a"], cfg=CFG, runner=runner)
    assert len(out) == 3
    assert all(r.status == "ok" and r.adoptable for r in out.values())
    assert {r.dims["D0"] for r in out.values()} == {315.0, 400.0, 500.0}


def test_runner_failure_rejects_all_without_raising():
    def runner(prompt, cfg):
        raise FileNotFoundError("kimi")

    out = lsc.classify_sizes_batch(["Ø315", "Ø400"], cfg=CFG, runner=runner)
    assert all(r.status == "rejected" for r in out.values())
    assert any("недоступн" in r.detail.lower() or "kimi" in r.detail.lower() for r in out.values())


def test_retries_then_success():
    payload = json.dumps({"results": [
        {"format_class": "round_diameter", "spans": [{"role": "diameter", "start": 1, "end": 4}]}
    ]})
    runner, state = _ok_runner({"Ø315": payload}, fail_times=1)
    r = lsc.classify_sizes_batch(["Ø315"], cfg=CFG, runner=runner)["Ø315"]
    assert r.status == "ok" and state["calls"] == 2


def test_malformed_json_rejected():
    runner, _ = _ok_runner({"Ø315": "не json вообще"})
    r = lsc.classify_sizes_batch(["Ø315"], cfg=CFG, runner=runner)["Ø315"]
    assert r.status == "rejected"
