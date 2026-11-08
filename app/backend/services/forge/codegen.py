"""Single-file HTML generator: LLM produces a complete, self-contained app in one call.

The prompt embeds a design guide (derived from the frontend-design discipline) so the
generated app has a deliberate visual identity rather than the generic AI look. Data is
persisted with localStorage so the generated app survives a refresh / reopening.
"""

import html as _html
import logging
import os
import re

from services.forge import gateway
from services.forge.gateway import RunContext
from services.forge.util import DomainError

# 单文件 HTML 生成最长可能需要数分钟，把超时做成可配置并给足余量。
# 之前写死 240s，DeepSeek 生成稍慢就会被 asyncio.wait_for 掐断，导致任务失败、项目不落库。
LLM_GENERATE_TIMEOUT = float(os.environ.get("LLM_GENERATE_TIMEOUT", "900"))

# 生成用的 max_tokens。已在 gateway.chat 里对生成调用关闭思考模式（thinking=False），
# 所以这里是纯 content（HTML）的长度上限。16000 时复杂应用仍可能被截断（verify 报
# 「文档在 script 中途被截断」然后反复重生成），提到 32000 留足余量；配合上面的
# 「保持精简」约束避免模型借长额度写多文件工程。做成可配置。
LLM_GENERATE_MAX_TOKENS = int(os.environ.get("LLM_GENERATE_MAX_TOKENS", "32000"))

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
    """Strip markdown code fences wherever they appear (leading/trailing/internal)."""
    text = (html or "").strip()
    if not text:
        return text
    text = re.sub(r"^\s*```[^\n]*$", "", text, flags=re.MULTILINE)
    return text.strip()


# 模型在 DAG 分阶段生成时偶尔会输出「片段拼接指令」注释（如 ``<!-- 片段 A：接在… -->``），
# 这些是模型臆造的批注、不是真实内容，落库前必须清掉，否则会以原文形式显示在页面里。
_SPLICE_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_SPLICE_HINTS = ("片段", "接在", "拼接到", "补上", "补完", "收尾", "文档头", "文档收尾")


def sanitize_html(html: str) -> str:
    """Final cleanup before a generated HTML is stored/rendered.

    Removes markdown fences, fragment-splicing instruction comments, and any explanation
    text the model emits *before* the actual ``<!doctype html>`` / ``<html>`` (推理型模型
    常先写一句「下面是一个可直接运行的单文件实现…」再接代码)。只保留 HTML 本身。
    """
    text = _strip_fence(html)

    def _repl(m: re.Match) -> str:
        body = m.group(0)
        return "" if any(h in body for h in _SPLICE_HINTS) else body

    text = _SPLICE_COMMENT_RE.sub(_repl, text)

    # 截断到第一个 <!doctype / <html：丢弃模型在 HTML 之前写的解释性文字。
    m = re.search(r"<!doctype\b|<html\b", text, re.IGNORECASE)
    if m:
        text = text[m.start():]
    # 截断到最后一个 </html>：丢弃模型在 HTML 之后追加的解释/优化建议（如「### 核心功能… 优化建议：…」）。
    end = text.lower().rfind("</html>")
    if end != -1:
        text = text[:end + len("</html>")]
    return text.strip()


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return _html.unescape(m.group(1).strip())[:60]
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
        "- 只输出 HTML 文档本身，不要 markdown 围栏，不要任何解释性文字；\n"
        "- 保持精简：聚焦需求里的核心功能，不要堆无关功能、不要写成多文件工程，整体控制在一个单文件的合理长度内。\n\n"
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


NORMALIZE_SYSTEM = (
    "你是需求规范化器。把用户的一句话需求改写成清晰、具体、中性的需求描述，供前端代码生成使用。\n"
    "规则：\n"
    "- 保留功能内核（做什么、记录哪些数据、能怎么操作），不增删功能；\n"
    "- 把随意/戏谑/口语化措辞换成中性领域语言（如「海王管理多段关系」→「多联系人关系管理」，「记录拉屎」→「记录排便/肠道健康」）；\n"
    "- 让需求更具体：点明核心数据字段与主要交互；\n"
    "- 只输出改写后的需求文本本身，不要解释、不要 markdown。"
)


async def normalize_requirement(ctx: RunContext, requirement: str, *, model: str | None = None) -> str:
    """把一句话需求规范化为中性、具体的描述。失败则原样返回（fail-open，不阻塞生成）。"""
    try:
        result = await gateway.chat(
            ctx,
            [
                {"role": "system", "content": NORMALIZE_SYSTEM},
                {"role": "user", "content": f"原始需求：{requirement}"},
            ],
            purpose="normalize",
            model=model,
            max_tokens=600,
            temperature=0,
        )
        normalized = (result.content or "").strip()
        return normalized or requirement
    except Exception as exc:  # noqa: BLE001 - normalize 是可选增强，失败不阻塞
        logger.warning("normalize 失败（%s），用原始需求", exc)
        return requirement


async def generate(ctx: RunContext, requirement: str, current_html: str | None = None, *, model: str | None = None, provider: str = "deepseek") -> dict:
    """One LLM call → validated single-file HTML + title. Returns {"html", "title"}."""
    messages = build_messages(requirement, current_html)
    result = await gateway.chat(ctx, messages, purpose="generate", model=model, max_tokens=LLM_GENERATE_MAX_TOKENS, temperature=0.5, timeout=LLM_GENERATE_TIMEOUT, thinking=False, provider=provider)
    html = sanitize_html(result.content)
    if not html.strip():
        # 模型可能因内容过滤/拒绝返回了空内容，给一句更可操作的话。
        raise DomainError("模型返回了空内容（可能触发了内容过滤），请调整需求描述后重试")
    _validate_html(html)
    title = _extract_title(html)
    return {"html": html, "title": title}
