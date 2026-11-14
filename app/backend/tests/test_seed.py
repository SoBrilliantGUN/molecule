from services.forge.seed import match_mock


def test_match_mock_by_name():
    assert match_mock("做一个读书笔记管理")["name"] == "读书笔记"


def test_match_mock_by_alias_kanban():
    assert match_mock("做一个待办看板")["name"] == "项目看板"


def test_match_mock_by_alias_kanban_bare():
    assert match_mock("看板")["name"] == "项目看板"


def test_match_mock_no_match_returns_none():
    assert match_mock("做一个完全无关的东西") is None


def test_match_mock_returns_html():
    out = match_mock("健身打卡")
    assert out is not None
    assert "<html" in out["html"].lower()
