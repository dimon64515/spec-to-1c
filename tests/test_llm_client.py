from llm_client import assistant_content, extract_json_object


def test_assistant_content_finds_role():
    stdout = '{"role":"assistant","content":"{\\"a\\":1}"}\n{"role":"system","content":"x"}'
    assert '"a"' in assistant_content(stdout)


def test_extract_json_object_stops_at_trailing_text():
    obj, end = extract_json_object('{"rows":[]} garbage tail')
    assert obj == {"rows": []}
    assert end == 11  # len('{"rows":[]}') — raw_decode end offset
