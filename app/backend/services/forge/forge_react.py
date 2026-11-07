"""Short-task ReAct verify loop: one generate call, then the same triple-check as DAG stages.

When the model decides the task is "done", we never trust it blindly:
  ① program side-effect / structural check (side_effect_checks)
  ② a *different* model (LLM_VERIFY_MODEL) verifies the generated verify_items
  ③ program computes the score; below VERIFY_PASS_SCORE → regenerate targeted at missing
     items (≤ REACT_MAX_RETRY attempts); if still short, returns with a visible warning.

Like forge_dag, all LLM calls go through gateway.chat (budget / breaker / accounting).
"""

from __future__ import annotations

import logging
import os

from services.forge import gateway
from services.forge.codegen import generate
from services.forge.forge_dag import side_effect_checks, verify_html
from services.forge.gateway import RunContext
from services.forge.util import DomainError

logger = logging.getLogger(__name__)

VERIFY_PASS_SCORE = float(os.environ.get("VERIFY_PASS_SCORE", "0.7"))
REACT_MAX_RETRY = int(os.environ.get("REACT_MAX_RETRY", "2"))
# 核验模型必须可用：Qwen3-Coder-480B 在硅基流动 403 禁用，故默认 GLM-5.3（跨家核验）。
VERIFY_MODEL = os.environ.get("LLM_VERIFY_MODEL", "zai-org/GLM-5.3")

# short task: generate a compact verify checklist from the requirement in one tiny call
PLAN_CHECKS_SYSTEM = (
    "你是验收清单生成器。根据用户的一句话需求，生成 3-5 条这个单文件 HTML 应用必须满足的核验项。\n"
    "核验项要具体、可判真伪（如「能新增和删除待办」而非「功能完整」）。\n"
    '只输出 JSON：{"checks":["...","..."]}。'
)


async def plan_checks(ctx: RunContext, requirement: str) -> list[str]:
    """One light call → verify checklist for the requirement. Failure → sensible defaults."""
    defaults = [
        "是完整可运行的 HTML 页面（含 <!doctype> / <html> / <head> / <body>）",
        "内联所有 CSS 与 JS，不引用外部 JS/CSS（字体除外）",
        "数据用 localStorage 持久化（刷新后数据仍在）",
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
        return checks[:8] if checks else defaults
    except Exception as exc:  # noqa: BLE001 - checks planning must never block generation
        logger.warning("plan_checks 失败（%s），用默认核验项", exc)
        return defaults


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

    checks = await plan_checks(ctx, requirement)
    html = ""
    title = ""
    attempts = 0
    missing: list[str] = []
    while attempts <= REACT_MAX_RETRY:
        focus = f"\n特别补齐此前未通过的核验项：{'；'.join(missing[:4])}" if missing else ""
        await emit("verify", {"text": f"生成第 {attempts + 1} 轮" + (f"（补齐：{'、'.join(missing[:3])}）" if missing else ""), "score": 0.0, "passed": False, "attempt": attempts + 1})
        out = await generate(ctx, requirement + focus, current_html, model=model)
        html, title = out["html"], out["title"]

        # ① program side-effect / structural check
        violations = side_effect_checks(html)
        if violations:
            missing = violations
            await emit("verify", {"text": f"结构性校验未过：{'；'.join(violations[:4])}", "score": 0.0, "passed": False})
            attempts += 1
            continue

        # ② a different model checks each verify_item
        v = await verify_html(ctx, checks, html)
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
            return {"html": html, "title": title, "verify": v}
        missing = v["missing"]
        attempts += 1

    # Reached retry budget and still short → return with an honest warning.
    await emit("verify", {"text": f"重试 {REACT_MAX_RETRY} 次后核验分仍低于 {VERIFY_PASS_SCORE}，返回当前结果并注明未通过", "score": v.get("score", 0.0), "passed": False, "missing": missing})
    return {"html": html, "title": title, "verify": v}