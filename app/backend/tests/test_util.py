import pytest

from services.forge.util import DomainError, dumps, extract_json, loads, sha, slugify


def test_extract_json_plain_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_surrounded_by_text():
    assert extract_json('前缀说明 {"a": 1, "b": [2]} 后缀') == {"a": 1, "b": [2]}


def test_extract_json_array():
    assert extract_json('[1, 2, 3]') == [1, 2, 3]


def test_extract_json_no_json_raises():
    with pytest.raises(ValueError):
        extract_json("这里没有任何 JSON")


def test_slugify_lower_and_dash():
    assert slugify("Hello World!") == "hello-world"


def test_slugify_empty_falls_back():
    assert slugify("") == "app"
    assert slugify("!!!") == "app"


def test_sha_deterministic_and_32_chars():
    a = sha("x", {"k": 1})
    b = sha("x", {"k": 1})
    assert a == b
    assert len(a) == 32
    assert sha("different") != a


def test_dumps_loads_roundtrip():
    obj = {"n": 1, "中文": "值"}
    assert loads(dumps(obj)) == obj


def test_loads_invalid_json_returns_default():
    assert loads("not-json", default=[]) == []
    assert loads(None, default={}) == {}


def test_domain_error_is_exception():
    with pytest.raises(DomainError):
        raise DomainError("项目不存在")
