from services.forge.codegen import sanitize_html, _extract_title, _strip_fence

DOCTYPE = "<!doctype html>"
SHELL = "<!doctype html><html><head><title>App</title></head><body><p>hi</p></body></html>"


def test_sanitize_strips_markdown_fence():
    raw = "```html\n" + SHELL + "\n```"
    assert sanitize_html(raw).startswith(DOCTYPE)
    assert "```" not in sanitize_html(raw)


def test_sanitize_truncates_preamble_text():
    raw = "下面是一个可直接运行的单文件实现：\n" + SHELL
    assert sanitize_html(raw).startswith(DOCTYPE)


def test_sanitize_truncates_trailing_explanation():
    raw = SHELL + "\n### 核心功能\n优化建议：可以再加导出。"
    out = sanitize_html(raw)
    assert out.endswith("</html>")


def test_sanitize_removes_splice_comment_but_keeps_normal():
    with_splice = "<!-- 片段 A：接在文档头 -->\n" + SHELL
    assert "片段" not in sanitize_html(with_splice)
    with_normal = SHELL.replace("<body>", "<body><!-- 正常注释 -->")
    assert "正常注释" in sanitize_html(with_normal)


def test_extract_title():
    assert _extract_title("<title>My App</title>") == "My App"
    assert _extract_title("<title> 有空格 </title>") == "有空格"
    assert _extract_title("<body>无标题</body>") == ""


def test_strip_fence_removes_leading_fence():
    assert _strip_fence("```html\n<!doctype html>").startswith("<!doctype html>")
