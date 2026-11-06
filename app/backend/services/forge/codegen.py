"""Single-file HTML generator: LLM produces a complete, self-contained app in one call.

The prompt embeds a design guide (derived from the frontend-design discipline) so the
generated app has a deliberate visual identity rather than the generic AI look. Data is
persisted with localStorage so the generated app survives a refresh / reopening.
"""

import logging
import re

from services.forge import gateway
from services.forge.gateway import RunContext
from services.forge.util import DomainError

logger = logging.getLogger(__name__)

# 生成应用的「反模板化」设计指导。要点：避免 AI 默认观感，让设计贴合应用主题，
# 用一个克制的视觉焦点表达性格，其余保持秩序与可读性。
DESIGN_GUIDE = """【视觉设计铁律 —— 生成的界面必须看起来是「为这个应用专门设计的」，而不是 AI 模板】

- 确定一个具体主题与受众，配色/字体/布局都从它长出来。一个「健身打卡」应用和一个「客户对账」应用的观感应当截然不同。
- 避免这些 AI 生成页的常见雷区：
  · 米白/奶油底（近 #F4F1EA）+ 高对比衬线标题 + 陶土色点缀（近 #D97757）；
  · 近黑底（#0B0B0B/#111）+ 单一荧光绿或朱红点缀；
  · SaaS 卡片套件：内容切成一堆一模一样圆角卡片、处处同一圆角、统一软灰阴影、渐变当装饰；
  · 每个标题上都压一行全大写 eyebrow 小标签；用「A · B · C」中点串词；「词 — 片段」的 em dash 标签；链接/按钮后跟「→」。
- 字体是性格的载体：别用默认字体栈。选 1–2 个有辨识度的字体（可用系统里有的优质字体或 Google Fonts），并定清晰的字号层级与字重。
- 视觉结构即信息：分隔线、编号、标签只在你真的需要表达顺序/分组时才用，别为了装饰。
- 把「大胆」花在一处：让一个元素成为记忆点（一个有态度的标题、一个数据焦点、一次克制的动效），其余保持安静克制。砍掉一切不服务主题的装饰。
- 质量底线不声张：响应式到移动端、可见的键盘焦点、尊重 prefers-reduced-motion、对比度 ≥ 4.5:1。
- 文案从使用者视角写、说人话：CTA 说清楚会发生什么（「保存更改」而非「提交」），空状态是行动邀请，错误说明发生了什么及如何修。
- 配色用 4–6 个有名字的 hex 值，避免五彩渐变；用 hairline 分隔线或留白分层，而非堆叠圆角卡片。
"""


def _strip_fence(html: str) -> str:
    html = (html or "").strip()
    if html.startswith("```"):
        html = html.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return html


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()[:60]
    return ""


def _validate_html(html: str) -> None:
    if "<html" not in html.lower() and "<!doctype" not in html.lower():
        raise DomainError("模型没有返回有效的 HTML 页面，请重试")


def build_messages(requirement: str, current_html: str | None = None) -> list[dict]:
    """Prompt for a self-contained single-file app. `current_html` enables iterative edit."""
    system = (
        "你是一位资深前端设计师兼工程师。根据用户的一句话需求，直接产出一个**完整、自包含的单文件 HTML 应用**。\n"
        "硬性要求：\n"
        "- 内联所有 CSS 与 JS，不引用任何外部 JS/CSS 依赖（字体除外，可引用 Google Fonts）；\n"
        "- 数据用 localStorage 持久化（刷新/重开浏览器数据仍在）；\n"
        "- 有真实交互：能新增/编辑/删除/勾选等，不是静态展示页；\n"
        "- 只输出 HTML 文档本身，不要 markdown 围栏，不要任何解释性文字。\n\n"
        + DESIGN_GUIDE
    )
    user = (
        (f"现有应用 HTML（在此基础上修改）：\n{current_html}\n\n" if current_html else "")
        + f"需求：{requirement}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def generate(ctx: RunContext, requirement: str, current_html: str | None = None, *, model: str | None = None) -> dict:
    """One LLM call → validated single-file HTML + title. Returns {"html", "title"}."""
    messages = build_messages(requirement, current_html)
    result = await gateway.chat(ctx, messages, purpose="generate", model=model, max_tokens=8000, temperature=0.5, timeout=240)
    html = _strip_fence(result.content)
    _validate_html(html)
    title = _extract_title(html)
    return {"html": html, "title": title}
