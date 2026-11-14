"""Short-task ReAct verify loop: one generate call, then the same triple-check as DAG stages.

When the model decides the task is "done", we never trust it blindly:
  ① program side-effect / structural check (side_effect_checks)
  ② a *different* model (LLM_VERIFY_MODEL) verifies the generated verify_items
  ③ program computes the score; below VERIFY_PASS_SCORE → regenerate targeted at missing
     items (≤ REACT_MAX_RETRY attempts); if still short, returns with a visible warning.

All LLM calls go through gateway.chat (budget / breaker / accounting).
"""

from __future__ import annotations

import logging
import os

from services.forge import gateway
from services.forge.codegen import generate, normalize_requirement
from services.forge.gateway import RunContext
from services.forge.util import DomainError
from services.forge.verify import side_effect_checks, slop_checks, slop_p0_messages, verify_html

logger = logging.getLogger(__name__)

VERIFY_PASS_SCORE = float(os.environ.get("VERIFY_PASS_SCORE", "0.7"))
REACT_MAX_RETRY = int(os.environ.get("REACT_MAX_RETRY", "2"))

# short task: generate a compact verify checklist from the requirement in one tiny call
PLAN_CHECKS_SYSTEM = (
    "你是验收清单生成器。根据用户的一句话需求，生成 3-5 条这个单文件 HTML 应用必须满足的核验项。\n"
    "核验项要具体、可判真伪（如「能新增和删除待办」而非「功能完整」）。\n"
    '只输出 JSON：{"checks":["...","..."]}。'
)


# 存储安全是硬约束：生成的单文件应用会以 iframe（sandbox）或导出后 file:// 方式打开，
# localStorage 在这些环境可能被禁用。核验清单无论如何生成，都必须包含这一项，防止
# 生成物因「localStorage 不可用」而卡在加载态 / 阻断录入。
_STORAGE_SAFETY = "数据用 localStorage 持久化，且读写有 try/catch 兜底、存储不可用时应用仍能完整使用（不卡加载、不阻断录入）"


async def plan_checks(ctx: RunContext, requirement: str) -> list[str]:
    """One light call → verify checklist for the requirement. Failure → sensible defaults."""
    defaults = [
        "是完整可运行的 HTML 页面（含 <!doctype> / <html> / <head> / <body>）",
        "内联所有 CSS 与 JS，不引用外部 JS/CSS（字体除外）",
        _STORAGE_SAFETY,
        "有真实交互：能新增/编辑/删除/勾选一项内容",
        "视觉贴合主题、非 AI 模板观感",
    ]
    try:
        result = await gateway.chat(
            ctx,
            [
                {"role": "system", "content": PLAN_CHECKS_SYSTEM},
                {"role": "user", "content": f"需求：{requirement}"},
            ],
            purpose="react:plan_checks",
            max_tokens=300,
            temperature=0,
        )
        from services.forge.util import extract_json

        data = extract_json(result.content)
        checks = [str(c) for c in (data.get("checks") or []) if str(c).strip()]
        if not checks:
            checks = list(defaults)
    except Exception as exc:  # noqa: BLE001 - checks planning must never block generation
        logger.warning("plan_checks 失败（%s），用默认核验项", exc)
        checks = list(defaults)

    # 无论核验清单怎么生成，都确保「存储安全」这一项在列（防止生成物因存储不可用而卡死）。
    if not any("localStorage" in c or "存储" in c for c in checks):
        checks.append(_STORAGE_SAFETY)
    return checks[:8]


async def generate_verified(
    ctx: RunContext,
    requirement: str,
    current_html: str | None = None,
    *,
    model: str | None = None,
    emit=None,
) -> dict:
    """Generate once; then triple-check with up to REACT_MAX_RETRY targeted regens.

    Returns {"html", "title", "verify": {"score", "passed", "missing"}}.
    """
    emit = emit or (lambda _e, _d: None)

    if current_html is None:
        # 新建应用：先把一句话需求规范化为中性、具体的描述，规避戏谑/口语措辞触发的内容过滤。
        await emit("verify", {"text": "正在规范化需求…", "score": 0.0, "passed": False})
        requirement = await normalize_requirement(ctx, requirement, model=model)

    await emit("verify", {"text": "正在分析需求、制定核验清单…", "score": 0.0, "passed": False})
    checks = await plan_checks(ctx, requirement)
    html = ""
    title = ""
    attempts = 0
    missing: list[str] = []
    slop_warnings: list[dict] = []
    # 结构校验可能连续失败多轮，loop 退出时 v 可能尚未赋值（UnboundLocalError），先给默认。
    v: dict = {"score": 0.0, "checked": {}, "missing": []}
    while attempts <= REACT_MAX_RETRY:
        focus = f"\n特别补齐此前未通过的核验项：{'；'.join(missing[:4])}" if missing else ""
        await emit("verify", {"text": f"第 {attempts + 1} 轮：正在生成完整应用（模型思考中，约需 1–2 分钟）…" + (f"（补齐：{'、'.join(missing[:3])}）" if missing else ""), "score": 0.0, "passed": False, "attempt": attempts + 1})
        out = await generate(ctx, requirement + focus, current_html, model=model)
        html, title = out["html"], out["title"]
        await emit("verify", {"text": f"第 {attempts + 1} 轮生成完成，开始结构校验…", "score": 0.0, "passed": False})

        # ① program side-effect / structural check
        violations = side_effect_checks(html)
        if violations:
            missing = violations
            await emit("verify", {"text": f"结构性校验未过：{'；'.join(violations[:4])}", "score": 0.0, "passed": False})
            attempts += 1
            continue

        # ①b 反 AI 味检查（确定性、零额度）：P0 必须改 → 回喂重生成；P1 记入警告
        slop = slop_checks(html)
        slop_warnings = [f for f in slop if f["severity"] != "P0"]
        p0 = slop_p0_messages(slop)
        if p0:
            missing = p0
            await emit("verify", {"text": f"反 AI 味检查未过：{'；'.join(p0[:3])}", "score": 0.0, "passed": False})
            attempts += 1
            continue

        # ② a different model checks each verify_item
        v = await verify_html(ctx, checks, html)
        # 核验模型不可用（degraded）≠ 通过：结构/反 AI 味已过，但跨模型核验没执行，
        # 直接带警示返回，别把重试额度浪费在「换模型也无法修复的核验故障」上。
        if v.get("degraded"):
            await emit("verify", {"text": "跨模型核验当前不可用，本次未执行核验（结构校验与反 AI 味检查已通过）", "score": 0.0, "passed": False, "missing": v["missing"], "degraded": True})
            return {"html": html, "title": title, "verify": v, "slop": slop_warnings}
        # ③ program score
        passed = v["score"] >= VERIFY_PASS_SCORE
        await emit(
            "verify",
            {
                "text": f"核验分 {v['score']:.2f}" + (f"，缺失：{'；'.join(v['missing'][:3])}" if v["missing"] else "，全部通过"),
                "score": v["score"],
                "passed": passed,
                "missing": v["missing"],
            },
        )
        if passed:
            return {"html": html, "title": title, "verify": v, "slop": slop_warnings}
        missing = v["missing"]
        attempts += 1

    # Reached retry budget and still short → return with an honest warning.
    await emit("verify", {"text": f"重试 {REACT_MAX_RETRY} 次后核验分仍低于 {VERIFY_PASS_SCORE}，返回当前结果并注明未通过", "score": v.get("score", 0.0), "passed": False, "missing": missing})
    return {"html": html, "title": title, "verify": v, "slop": slop_warnings}