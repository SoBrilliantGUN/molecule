from services.forge.design_templates import (
    pick_templates,
    style_block,
    _explicit_theme,
)


def _ids(templates):
    return [t["id"] for t in templates]


def test_explicit_theme_colon_form():
    assert _explicit_theme("风格：商务正式") == "dashboard"


def test_explicit_theme_direct_name():
    assert _explicit_theme("做一个活泼明快的打卡应用") == "neobrutalism"


def test_explicit_theme_missing_returns_none():
    assert _explicit_theme("做一个记账应用") is None


def test_pick_templates_explicit_returns_that_and_a_contrast():
    ids = _ids(pick_templates("风格：极客冷峻"))
    assert ids[0] == "mono"
    assert len(ids) == 2


def test_pick_templates_keyword_hits():
    ids = _ids(pick_templates("做一个待办看板"))
    assert "dashboard" in ids  # 看板 → 数据面板


def test_pick_templates_default_fallback():
    ids = _ids(pick_templates("一个谁都猜不到主题的应用"))
    assert ids == ["swiss", "warm-editorial"]


def test_style_block_injects_craft_and_candidates():
    block = style_block("做一个健身打卡应用")
    assert "视觉铁律" in block
    assert "候选" in block
    # 健身/打卡 命中 → 候选里包含「粘土柔和」方向（recipe 里写的是 name 而非 id）
    assert "粘土柔和" in block
