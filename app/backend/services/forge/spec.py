"""需求理解器：一句话 → 结构化规格 + 是否需要追问。

在「生成前」跑一次轻量解析（复用意图模型），产出两层信息：
1. 已理解的部分（title / fields / view / confidence）——让用户一眼确认「它听懂了吗」；
2. 需要补问的封闭式问题（questions，每个带默认值 + 可选/可跳过）。

设计铁律：**澄清永远不阻塞生成**。解析失败、JSON 异常、模型宕机一律
fail-open 到 needs_clarify=false——最坏只是少问一次，绝不会把用户卡在表单前。
"""

import logging
import os
import uuid

from services.forge import gateway, seed
from services.forge.budget import Budget
from services.forge.gateway import RunContext
from services.forge.util import extract_json

logger = logging.getLogger(__name__)

# 可选覆盖：默认 None → 需求理解走 gateway 默认（DeepSeek 官方 deepseek-v4-flash）。
# 之前回退到 LLM_INTENT_MODEL（硅基流动命名），却仍走 provider=deepseek 官方，模型名错配。
SPEC_MODEL = os.environ.get("LLM_SPEC_MODEL") or None

# 追问必须是「有默认答案、用户可跳过」的封闭式问题，绝不能逐条连环审问。
SPEC_SYSTEM = (
    "你是需求理解器。把用户的一句话需求解析成结构化的应用规格，并判断是否需要追问。\n"
    "1. 判断是否足够清晰、能直接生成一个可用的单文件 HTML 应用：\n"
    "   - 清晰（领域、核心数据字段、主要交互都能推断）→ needs_clarify=false。\n"
    "   - 含糊（核心对象/字段/用途缺失，或存在影响生成结果的歧义，如「记账」是个人还是企业、"
    "「展示」是列表还是看板）→ needs_clarify=true，并给出 1–3 个关键追问。\n"
    "2. 抽取你已理解到的：title（应用名，简短）、fields（核心数据字段，name+type）、"
    "view（主视图形态：列表/表格/看板/卡片等）。\n"
    "3. 追问规则：每个问题必须是封闭式——type=select 时给 options 和 default；type=text 时给 default；"
    "问题要少而关键（最多 3 个），都有默认答案，用户不改也能直接生成。\n"
    "只输出 JSON（不要 markdown 围栏）：\n"
    "{\"needs_clarify\":false,\"title\":\"...\",\"fields\":[{\"name\":\"...\",\"type\":\"text|number|date|select|boolean\"}],"
    "\"view\":\"...\",\"confidence\":0.0,"
    "\"questions\":[{\"key\":\"...\",\"question\":\"...\",\"type\":\"select|text\",\"options\":[\"...\"],\"default\":\"...\"}]}\n"
    "confidence 表示你对「无需追问即可正确生成」的把握（0~1）。"
)


def _safe_questions(raw: list) -> list[dict]:
    """Normalize model questions into a minimal renderable shape, dropping junk."""
    out = []
    for q in raw or []:
        if not isinstance(q, dict) or not q.get("question"):
            continue
        qtype = "select" if q.get("type") == "select" else "text"
        options = [str(o) for o in (q.get("options") or []) if str(o).strip()]
        default = str(q.get("default") or "")
        if qtype == "select" and options and default not in options:
            default = options[0]  # 默认值兜底到首个选项
        out.append({
            "key": str(q.get("key") or f"q{len(out)}"),
            "question": str(q["question"]),
            "type": qtype,
            "options": options,
            "default": default,
        })
    return out[:3]


def _safe_fields(raw: list) -> list[dict]:
    out = []
    for f in raw or []:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        out.append({"name": str(f["name"]), "type": str(f.get("type") or "text")})
    return out[:12]


async def understand(prompt: str, model: str | None = None) -> dict:
    """One lightweight LLM call → normalized spec. Never raises (fail-open to no-clarify)."""
    mock = seed.match_mock(prompt)
    if mock:
        # 命中预生成示例：无需追问、无需调用模型，直接给一个确定性的规格。
        return {"needs_clarify": False, "title": mock["name"], "fields": [], "view": "", "confidence": 1.0, "questions": []}
    ctx = RunContext(run_key=uuid.uuid4().hex[:16], budget=Budget())
    try:
        result = await gateway.chat(
            ctx,
            [
                {"role": "system", "content": SPEC_SYSTEM},
                {"role": "user", "content": f"需求：{prompt}"},
            ],
            purpose="spec",
            model=model or SPEC_MODEL,
            max_tokens=900,
            temperature=0,
        )
        data = extract_json(result.content)
        questions = _safe_questions(data.get("questions"))
        return {
            "needs_clarify": bool(data.get("needs_clarify")) and bool(questions),
            "title": str(data.get("title") or "").strip() or prompt[:20],
            "fields": _safe_fields(data.get("fields")),
            "view": str(data.get("view") or "").strip(),
            "confidence": round(float(data.get("confidence", 0.5) or 0.5), 2),
            "questions": questions,
        }
    except Exception as exc:  # noqa: BLE001 - 解析/模型失败都 fail-open，绝不阻塞生成
        logger.warning("spec 解析失败（%s），fail-open 到不澄清", exc)
        return {
            "needs_clarify": False,
            "title": prompt[:20],
            "fields": [],
            "view": "",
            "confidence": 0.5,
            "questions": [],
        }
