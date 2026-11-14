"""生成后的核验闭环：程序侧结构校验 + 跨模型逐项验收。

单链路生成（forge_react.generate_verified）对模型的产出「不轻信」，采用三重校验：
  ① ``side_effect_checks`` — 程序侧的确定性骨架/结构校验（不消耗额度）；
  ② ``verify_html`` — 换一个不同家的模型（LLM_VERIFY_MODEL）逐项核对 verify_items；
  ③ 调用方按结果重算分，低于阈值则针对缺失项定向重生成。

``side_effect_checks`` 只查骨架（doctype/html/head/title/body 闭合、外链 JS/CSS），
外加一个确定性的 localStorage 持久化检查（有脚本却无 localStorage 即判违规）。
「交互是否真实、存储的 try/catch 语义是否正确」这类语义项仍交给 ② 按核验项判断——
避免把每个阶段都误判为「骨架缺失」。
"""

from __future__ import annotations

import logging
import os
import re

from services.forge import gateway
from services.forge.gateway import RunContext
from services.forge.util import extract_json

logger = logging.getLogger(__name__)

# 核验模型必须可用：Qwen3-Coder-480B 在硅基流动 403 禁用，故默认 GLM-5.3（跨家核验）。
VERIFY_MODEL = os.environ.get("LLM_VERIFY_MODEL", "zai-org/GLM-5.3")


def side_effect_checks(html: str) -> list[str]:
    """Deterministic structural checks. Return list of violations (empty = OK)."""
    violations: list[str] = []
    low = (html or "").lower()
    if not low:
        return ["生成的 HTML 为空"]
    if "<html" not in low and "<!doctype" not in low:
        violations.append("缺少 <html> / <!doctype")
    if "<head" not in low or "</head>" not in low:
        violations.append("缺少 <head>/</head>")
    if "title" not in low:
        violations.append("缺少 <title>")
    if "<body" not in low or "</body>" not in low:
        violations.append("缺少 <body>/</body>")
    if "<script" in low and "</script>" not in low:
        violations.append("存在未闭合的 <script>")
    if "<style" in low and "</style>" not in low:
        violations.append("存在未闭合的 <style>")
    # localStorage 持久化是核心验收项（「能存数据」）。这是确定性字符串检查，零额度、可靠，
    # 不依赖跨模型核验（后者对长 HTML 可能漏判「缺少 localStorage」）。有脚本却完全没有
    # localStorage，说明生成物没有持久化能力，直接回喂重生成。
    if "<script" in low and "localstorage" not in low:
        violations.append("缺少 localStorage 持久化（有脚本但未做数据持久化）")
    # 外链检测：`(?:https?:)?//` 同时覆盖 `https://` 与协议相对 `//`；`\.js\b` 用词边界，
    # 让带查询串的 `x.js?v=2` 也能命中（原来要求 `.js` 结尾，`?v=2` 会漏）。
    for pat, label in (
        (r'src\s*=\s*["\']\s*(?:https?:)?//[^"\']*\.js\b', "外链 JS"),
        (r'href\s*=\s*["\']\s*(?:https?:)?//[^"\']*\.css\b', "外链 CSS"),
    ):
        if re.search(pat, low, re.IGNORECASE):
            violations.append("存在" + label + "（应全部内联）")
    return violations


VERIFY_SYSTEM = (
    "你是独立的验收评审。对照核验项（verify_items）检查生成的 HTML：逐项给出是否满足。\n"
    "只输出 JSON：{\"checked\":{\"项原文\":true/false},\"missing\":[\"不满足的项\"],\"score\":0.0}。\n"
    "score = 已满足项 / 总项数（0~1）。只根据 HTML 内容实判，不宽松放水。"
)


def _verifier_view(html: str, head: int = 20000, tail: int = 20000) -> str:
    """给核验模型的 HTML 视图：保留头部（doctype/head/首屏结构）+ 尾部（脚本/表单/空状态）。

    之前只传 `html[:12000]`，长文件底部的脚本、表单、空状态（恰好是「交互真实 /
    存储安全 / 空状态」这些核验项要查的地方）整段不被核验。头尾各留 20000 字符，
    中间用注释占位，既控长度又保证关键区段都能被看到。
    """
    h = html or ""
    if len(h) <= head + tail:
        return h
    return h[:head] + "\n<!-- …中间部分已省略（为控制核验长度）… -->\n" + h[-tail:]


async def verify_html(ctx: RunContext, verify_items: list[str], html: str) -> dict:
    """A *different* model checks each verify_item against the HTML → score."""
    if not verify_items:
        return {"checked": {}, "missing": [], "score": 1.0}
    try:
        result = await gateway.chat(
            ctx,
            [
                {"role": "system", "content": VERIFY_SYSTEM},
                {"role": "user", "content": "核验项：\n" + "\n".join(f"- {v}" for v in verify_items) + "\n\nHTML：\n" + _verifier_view(html)},
            ],
            purpose="verify",
            model=VERIFY_MODEL,
            max_tokens=1400,
            temperature=0,
            provider="siliconflow",
        )
        data = extract_json(result.content)
        checked = data.get("checked") or {}
        missing = [str(m) for m in (data.get("missing") or [])]
        score = float(data.get("score", 0.0))
        # 程序校验：以 checked 的布尔值为准重算分（防御模型自说自话）
        items = list(verify_items)
        passed = sum(1 for it in items if checked.get(it) is True)
        recompute = passed / len(items) if items else 1.0
        score = min(max(recompute, 0.0), 1.0)
        if not missing:
            missing = [it for it in items if checked.get(it) is not True]
        return {"checked": checked, "missing": missing, "score": round(score, 2)}
    except Exception as exc:  # noqa: BLE001 - verifier failure must fail CLOSED, never fake a pass
        # 核验模型不可用是「没查」，不是「通过」。fail-open 到 score=1.0 会让三重校验
        # 在最需要兜底的时刻静默放行。这里 fail-closed：记 degraded、按不通过返回，
        # 调用方据此给用户一个「核验未执行」的可见警示，而不是假装已验证。
        logger.warning("verify_html 失败（%s），核验未执行，按不通过处理", exc)
        return {"checked": {}, "missing": ["核验模型不可用，本次未执行跨模型核验"], "score": 0.0, "degraded": True}


# ======================================================================
# Anti-slop 检查：确定性、零额度的「反 AI 味」质量闸门。
# 生成物除了结构完整，还不能踩这些模板雷区（靛蓝主色、紫蓝渐变、emoji 图标、
# 圆角卡片+左色条、编造指标、lorem 填充、scrollIntoView、外链占位图、主色滥用）。
# 每条结果带 severity（P0=必须改 / P1=建议改）与 fix，供生成回路回喂给模型自纠。
# ======================================================================

_PURPLE_HEXES = [
    "#a855f7", "#9333ea", "#7c3aed", "#6d28d9", "#581c87",
    "#8b5cf6", "#a78bfa", "#c4b5fd", "#ddd6fe", "#ede9fe",
    "#6366f1", "#4f46e5", "#4338ca", "#3730a3", "#312e81",
    "#818cf8", "#a5b4fc", "#c7d2fe", "#e0e7ff", "#eef2ff",
]
_AI_DEFAULT_INDIGO = ["#6366f1", "#4f46e5", "#4338ca", "#3730a3", "#8b5cf6", "#7c3aed", "#a855f7"]
_TRUST_BLUE = ["#3b82f6", "#2563eb", "#1d4ed8", "#1e40af", "#1e3a8a", "#60a5fa", "#93c5fd", "#bfdbfe", "#0ea5e9", "#0284c7", "#0369a1", "#38bdf8", "#7dd3fc"]
_TRUST_CYAN = ["#06b6d4", "#0891b2", "#0e7490", "#155e75", "#164e63", "#22d3ee", "#67e8f9", "#a5f3fc"]
_SLOP_EMOJI = ["✨", "🚀", "🎯", "⚡", "🔥", "💡", "📈", "🎨", "🛡️", "🌟", "💪", "🎉", "👋", "🙌", "✅", "⭐", "🏆"]
_INVENTED_METRIC = [
    r"10×\s*(更快|更好|更简单|faster|better)",
    r"100×\s*(更快|faster)",
    r"99\.\d+%\s*(可用|在线|uptime)",
    r"3×\s*(更高效|更高产|productive|efficient)",
    r"零[-\s]?宕机",
    r"\b10x\s+faster\b",
]
_FILLER = [r"lorem\s+ipsum", r"功能\s*[一二三123]\b", r"示例内容", r"占位文本", r"待补充", r"placeholder\s+text", r"sample\s+content"]


def _clip(s: str, n: int = 120) -> str:
    t = re.sub(r"\s+", " ", s).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def slop_checks(html: str) -> list[dict]:
    """确定性反 AI 味检查。返回 [{"severity","id","message","fix"}, …]（空列表=通过）。

    与 side_effect_checks 不同：这里不查「能不能跑」，只查「是不是 AI 模板味」——
    靛蓝主色、紫蓝渐变、emoji 当图标、圆角卡片+左色条、编造指标、lorem 填充、
    scrollIntoView（会戳穿 iframe 预览）、外链占位图、原始 hex 过多、主色滥用。
    """
    findings: list[dict] = []
    if not html:
        return findings
    body = re.sub(r"<!--[\s\S]*?-->", "", html)
    lower = body.lower()

    def add(severity: str, fid: str, message: str, fix: str, snippet: str = "") -> None:
        findings.append({"severity": severity, "id": fid, "message": message, "fix": fix, "snippet": _clip(snippet)})

    # P0-1 紫/靛渐变
    if not any(f["id"] == "purple-gradient" for f in findings):
        for hex_ in _PURPLE_HEXES:
            m = re.search(rf"linear-gradient\([^)]*{re.escape(hex_)}[^)]*\)", lower)
            if m:
                add("P0", "purple-gradient", f"发现紫/靛渐变（{hex_}）——AI 模板招牌色", "换成扁平底色（var(--bg)/var(--surface)）或单一主色，不要用渐变", m.group(0))
                break
        else:
            m = re.search(r"linear-gradient\([^)]*\b(purple|violet)\b[^)]*\)", lower)
            if m:
                add("P0", "purple-gradient", "渐变色用了 purple/violet 关键字", "去掉渐变或换成单色", m.group(0))

    # P0-1c 蓝→青信任渐变
    if not any(f["id"] in ("purple-gradient", "trust-gradient") for f in findings):
        for m in re.finditer(r"linear-gradient\([^)]*\)", lower):
            g = m.group(0)
            has_blue = any(h in g for h in _TRUST_BLUE) or bool(re.search(r"\bblue\b", g))
            has_cyan = any(h in g for h in _TRUST_CYAN) or bool(re.search(r"\bcyan\b", g))
            if has_blue and has_cyan:
                add("P0", "trust-gradient", "蓝→青两段「信任渐变」——SaaS hero 陈词滥调", "换成扁平底色或单一设计 token 色", g)
                break

    # P0-1b 默认靛蓝当主色（剥离 token 定义后仍命中才算）
    if not any(f["id"] == "purple-gradient" for f in findings):
        stripped = re.sub(r":root\s*\{[^}]*\}", "", body)
        stripped = re.sub(r"--accent\s*:[^;]+;?", "", stripped)
        for hex_ in _AI_DEFAULT_INDIGO:
            if hex_ in stripped:
                add("P0", "ai-default-indigo", f"用了 LLM 默认靛蓝 {hex_} 当主色（最典型的 AI 味）", "改用 --accent 指向的 token；确需靛蓝就把它声明成 --accent", hex_)
                break

    # P0-2 emoji 当功能图标
    for e in _SLOP_EMOJI:
        m = re.search(rf"<(?:h[1-6]|button|li|span[^>]*class=\"[^\"]*icon[^\"]*\")[^>]*>[^<]*{re.escape(e)}", body, re.I)
        if m:
            add("P0", "emoji-icon", f"用 emoji「{e}」当功能图标", "换成 1.6–1.8px 细描边内联 SVG（currentColor），或去掉图标", m.group(0))
            break

    # P0-3 圆角卡片 + 左侧彩色竖条
    m = re.search(r"\.[a-z-]+\s*\{[^}]*border-left\s*:\s*\d+px\s+solid\s+[^;]+;[^}]*border-radius\s*:\s*[1-9]", lower)
    if m:
        add("P0", "left-accent-card", "圆角卡片 + 左侧彩色竖条——AI 仪表盘卡片形状", "去掉圆角或去掉左竖条（用全边 hairline 边框）", m.group(0))

    # P0-5 编造指标
    for pat in _INVENTED_METRIC:
        m = re.search(pat, lower)
        if m:
            add("P0", "invented-metric", f"疑似编造指标「{m.group(0)}」", "去掉数字，或换成真实的占位符", m.group(0))
            break

    # P0-6 填充文案
    for pat in _FILLER:
        m = re.search(pat, lower)
        if m:
            add("P0", "filler-copy", f"检测到填充文案「{m.group(0)}」", "换成贴合需求的具体文案，或直接删掉该段", m.group(0))
            break

    # P0-7 scrollIntoView（会带动宿主页一起滚，戳穿 iframe 预览）
    if ".scrollintoview(" in lower:
        add("P0", "scroll-into-view", "用了 Element.scrollIntoView()——iframe 预览会被顶走", "改成在真正的滚动容器上 scrollTo({top,behavior})")

    # P1-1 外链占位图
    m = re.search(r'<img[^>]+src=["\']https?://(?:images\.unsplash\.com|placehold\.co|placekitten\.com|via\.placeholder\.com|picsum\.photos|loremflickr\.com)', body, re.I)
    if m:
        add("P1", "external-image", "外链占位图 CDN——404 后很假", "用内联 SVG 或本地占位样式", m.group(0))

    # P1-2 :root 外原始 hex 过多（token 未被遵守）
    style = re.search(r"<style[^>]*>([\s\S]*?)</style>", body, re.I)
    if style:
        css = re.sub(r":root\s*\{[^}]*\}", "", style.group(1))
        hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
        if len(hexes) > 12:
            add("P1", "raw-hex", f":root 外有 {len(hexes)} 个原始 hex，token 可能没被遵守", "把颜色收进 :root 的 token（--bg/--surface/--fg/--muted/--border/--accent），其余用 var()", " ".join(hexes[:6]))

    # P1-3 主色滥用
    body_only = re.sub(r"<style[\s\S]*?</style>", "", body, flags=re.I)
    accent_uses = len(re.findall(r"var\(--accent\)", body_only, re.I))
    if accent_uses > 6:
        add("P1", "accent-overuse", f"var(--accent) 在渲染区用了 {accent_uses} 次，主色可能过滥", "一屏主色可见使用压到 2 处，其余降级到 --fg/--muted")

    # P1-0 全大写无字距（简化：只查 CSS 里 text-transform:uppercase 且缺 letter-spacing）
    if not any(f["id"] == "all-caps-no-tracking" for f in findings):
        for sb in re.finditer(r"<style[^>]*>([\s\S]*?)</style>", body, re.I):
            css = re.sub(r"/\*[\s\S]*?\*/", "", sb.group(1))
            for um in re.finditer(r"([^{}]*)\{([^{}]*text-transform\s*:\s*uppercase[^{}]*)\}", css, re.I):
                decl = um.group(2)
                if not re.search(r"letter-spacing\s*:\s*(?:[0-9.]+(?:em|px|rem)|var\()", decl, re.I):
                    add("P1", "all-caps-no-tracking", f"选择器「{um.group(1).strip()[:50]}」设了全大写但没加字距", "同规则加 letter-spacing: 0.06em~0.1em", f"{um.group(1).strip()} {{ {decl.strip()} }}")
                    break
            else:
                continue
            break

    return findings


def slop_p0_messages(findings: list[dict]) -> list[str]:
    """提取 P0 级违规的人类可读消息（用于回喂模型重生成时的「补充修正项」）。"""
    return [f"{f['id']}: {f['message']}（建议：{f['fix']}）" for f in findings if f["severity"] == "P0"]
