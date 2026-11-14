from services.forge.verify import (
    _verifier_view,
    side_effect_checks,
    slop_checks,
    slop_p0_messages,
)

CLEAN = (
    "<!doctype html><html><head><title>清单</title><style>"
    ":root{--bg:#ffffff;--surface:#fafafa;--fg:#111111;--muted:#777777;--border:#e2e2e2;--accent:#111111}"
    "body{background:var(--bg);color:var(--fg)}"
    "</style></head><body><header><h1>待办清单</h1></header><main><button>新增</button></main></body></html>"
)


def _ids(html):
    return [f["id"] for f in slop_checks(html)]


# ---------- slop_checks ----------

def test_clean_page_has_no_findings():
    assert slop_checks(CLEAN) == []


def test_slop_indigo_primary_p0():
    html = CLEAN + "<style>body{color:#6366f1}</style>"
    assert "ai-default-indigo" in _ids(html)


def test_slop_purple_gradient_p0():
    html = "<style>h1{background:linear-gradient(135deg,#7c3aed,#a855f7)}</style>"
    assert "purple-gradient" in _ids(html)


def test_slop_trust_gradient_p0():
    html = "<style>h1{background:linear-gradient(90deg,#3b82f6,#06b6d4)}</style>"
    assert "trust-gradient" in _ids(html)


def test_slop_emoji_icon_p0():
    html = CLEAN + "<button>✨ 新增</button>"
    assert "emoji-icon" in _ids(html)


def test_slop_left_accent_card_p0():
    html = "<style>.card{border-left:4px solid #111;border-radius:12px}</style>"
    assert "left-accent-card" in _ids(html)


def test_slop_invented_metric_p0():
    html = CLEAN + "<p>我们 10×更快</p>"
    assert "invented-metric" in _ids(html)


def test_slop_filler_copy_p0():
    html = CLEAN + "<p>lorem ipsum dolor</p>"
    assert "filler-copy" in _ids(html)


def test_slop_scroll_into_view_p0():
    html = CLEAN + "<script>el.scrollIntoView()</script>"
    assert "scroll-into-view" in _ids(html)


def test_slop_external_image_p1():
    html = CLEAN + '<img src="https://images.unsplash.com/photo.jpg">'
    assert "external-image" in _ids(html)


def test_slop_p0_messages_only_returns_p0():
    findings = [
        {"severity": "P0", "id": "emoji-icon", "message": "m", "fix": "f"},
        {"severity": "P1", "id": "external-image", "message": "m2", "fix": "f2"},
    ]
    msgs = slop_p0_messages(findings)
    assert len(msgs) == 1
    assert msgs[0].startswith("emoji-icon:")


# ---------- side_effect_checks ----------

def test_side_effect_empty_html():
    assert side_effect_checks("") == ["生成的 HTML 为空"]


def test_side_effect_valid_html_passes():
    assert side_effect_checks(CLEAN) == []


def test_side_effect_external_js_with_query_string():
    html = '<html><head><title>x</title></head><body><script src="https://cdn.com/x.js?v=2"></script></body></html>'
    assert any("外链 JS" in v for v in side_effect_checks(html))


def test_side_effect_protocol_relative_js():
    html = '<html><head><title>x</title></head><body><script src="//cdn.com/x.js"></script></body></html>'
    assert any("外链 JS" in v for v in side_effect_checks(html))


def test_side_effect_external_css():
    html = '<html><head><title>x</title><link href="https://cdn.com/x.css"></head><body></body></html>'
    assert any("外链 CSS" in v for v in side_effect_checks(html))


# ---------- _verifier_view ----------

def test_verifier_view_short_html_untouched():
    assert _verifier_view("<html>hi</html>") == "<html>hi</html>"


def test_verifier_view_long_html_keeps_head_and_tail():
    v = _verifier_view("a" * 50000)
    assert "省略" in v
    assert v.startswith("a")
    assert v.endswith("a")
    # 头 20000 + 占位注释 + 尾 20000，中间被截断
    assert len(v) < 50000
