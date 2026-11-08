"""Long-task generator: multi-stage pipeline, each stage is ONE generation call.

Each stage = one LLM call producing that stage's HTML fragment (structure / style /
interaction / polish), spliced into an accumulating full HTML. This replaces the old
per-stage DAG (plan + N nodes) with far fewer calls — the dominant cost was LLM latency
multiplied by ~30 sequential calls, which blew the wall-clock budget.

Guards:
- Stage completion → ① program side-effect check → ② a *different* model (LLM_VERIFY_MODEL,
  via SiliconFlow) verifies each verify_item → ③ program scores; below VERIFY_PASS_SCORE
  the stage is patched in-place (never a full regen).
- Any failure keeps the completed HTML and continues — never restart from scratch.

Every stage completion writes the accumulated HTML to AgentRuns.checkpoint_json, so a
crash resumes from the last completed stage — not from scratch.
All LLM calls go through gateway.chat (budget / breaker / accounting preserved).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable

from services.forge import gateway
from services.forge.codegen import DESIGN_GUIDE, _strip_fence, _extract_title, _validate_html, sanitize_html
from services.forge.gateway import RunContext
from services.forge.util import extract_json

logger = logging.getLogger(__name__)

# ---- tunables (env) ----
VERIFY_PASS_SCORE = float(os.environ.get("VERIFY_PASS_SCORE", "0.7"))
PATCH_MAX_RETRY = int(os.environ.get("PATCH_MAX_RETRY", "3"))   # in-stage patch retry budget
STAGE_TOKENS = int(os.environ.get("LLM_STAGE_TOKENS", "8000"))  # one stage's output cap（全量文档，需给足）
PLAN_MODEL = os.environ.get("LLM_PLAN_MODEL") or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
VERIFY_MODEL = os.environ.get("LLM_VERIFY_MODEL", "zai-org/GLM-5.3")

# stage actions understood by the generator/splicer
ACTIONS = ("structure", "style", "interaction", "polish")


# ======================================================================
# Stage planning (model decides stage breakdown, each stage has verify items)
# ======================================================================

STAGE_PLAN_SYSTEM = (
    "你是前端生成任务的阶段规划器。把一条需求拆成**多个阶段**，每个阶段完成一类工作。\n"
    "典型的阶段划分（按需求增减）：structure（HTML 结构与布局骨架）、style（视觉样式与主题）、"
    "interaction（用户交互与数据逻辑）、polish（细节打磨与收尾）。\n"
    "每个阶段给出：stage（阶段名）、instruction（该阶段要达成的目标）、"
    "verify_items（3-6 条该阶段完成后必须满足的核验项）。整体不得超过 6 个阶段。\n"
    "只输出 JSON：[{\"stage\":\"structure\",\"instruction\":\"...\",\"verify_items\":[\"...\",\"...\"]}, ...]\n"
    f"硬性要求：生成的是**单文件 HTML 应用**——内联所有 CSS/JS，不引外部 JS/CSS（可引 Google Fonts），"
    "数据用 localStorage 持久化，有真实交互（新增/编辑/删除/勾选等），"
    f"且必须符合下列视觉铁律：\n{DESIGN_GUIDE}"
)


async def plan_stages(ctx: RunContext, requirement: str) -> list[dict]:
    """One LLM call → stage list JSON. Parse failure → fall back to default 4 stages."""
    default = [
        {"stage": "structure", "instruction": "搭建 HTML 结构与布局骨架", "verify_items": ["有 <!doctype html> 与 <html>/<head>/<body>", "结构元素和语义标签完整"]},
        {"stage": "style", "instruction": "实现视觉主题与全部样式", "verify_items": ["CSS 全部内联，无外链样式", "配色贴合主题、非模板观感"]},
        {"stage": "interaction", "instruction": "实现全部用户交互与数据逻辑", "verify_items": ["所有交互可点可操作（新增/编辑/删除/勾选等）", "数据用 localStorage 持久化"]},
        {"stage": "polish", "instruction": "打磨细节：响应式/焦点/动效/文案", "verify_items": ["响应式适配移动端", "可见键盘焦点、尊重 prefers-reduced-motion"]},
    ]
    try:
        result = await gateway.chat(
            ctx,
            [
                {"role": "system", "content": STAGE_PLAN_SYSTEM},
                {"role": "user", "content": f"需求：{requirement}"},
            ],
            purpose="stage:plan",
            model=PLAN_MODEL,
            max_tokens=1800,
            temperature=0,
        )
        data = extract_json(result.content)
        stages = data if isinstance(data, list) else (data.get("stages") or data.get("items"))
        stages = [s for s in (stages or []) if isinstance(s, dict) and s.get("stage")]
        if stages and len(stages) <= 6:
            for s in stages:
                s.setdefault("verify_items", [])
            return stages
        logger.warning("stage plan 解析异常（%d 个阶段），回退默认 4 阶段", len(stages or []))
    except Exception as exc:  # noqa: BLE001 - planning is not a hard dependency
        logger.warning("stage plan 失败（%s），回退默认 4 阶段", exc)
    return default


# ======================================================================
# Per-stage generation (single call)
# ======================================================================

STAGE_SYSTEM = (
    "你是资深前端设计师兼工程师。根据用户需求产出一个完整、自包含的单文件 HTML 应用。\n"
    "硬性要求：内联所有 CSS/JS（不引外部依赖，字体除外）；数据用 localStorage 持久化；"
    "有真实交互（新增/编辑/删除/勾选等）。\n"
    "输出规则：只输出完整 HTML 文档本身，不要 markdown 围栏，不要任何解释性文字。\n"
    f"统一约束：{DESIGN_GUIDE}"
)


def _is_full_doc(html: str) -> bool:
    low = (html or "").lower()
    return "<html" in low and "<body" in low


async def _generate_stage(ctx: RunContext, stage: dict, requirement: str, current: str) -> str:
    """One LLM call → a COMPLETE single-file HTML app, refining ``current`` when present.

    每个阶段都产出**完整文档**（而非片段），在已有 ``current`` 基础上完善；已完成的内容
    作为起点保留、绝不丢弃。若模型没给出完整文档，则退回 ``current``（宁缺勿烂）。
    """
    action = stage.get("stage", "structure")
    user = (
        f"整体需求：{requirement}\n"
        f"当前阶段：{action}\n阶段目标：{stage.get('instruction', '')}\n"
        + (f"\n现有完整应用 HTML（在此基础上继续完善，输出完整新版）：\n{current[-6000:]}\n" if current else "")
        + "输出完整、自包含的单文件 HTML 应用（内联 CSS/JS，localStorage 持久化）。"
    )
    result = await gateway.chat(
        ctx,
        [{"role": "system", "content": STAGE_SYSTEM}, {"role": "user", "content": user}],
        purpose="stage:generate",
        model=PLAN_MODEL,
        max_tokens=STAGE_TOKENS,
        temperature=0.3,
    )
    html = sanitize_html(result.content)
    return html if _is_full_doc(html) else current


def _splice(current: str, action: str, segment: str) -> str:
    """Splice a stage's fragment into the accumulating HTML by action (best-effort).

    Guarantees an enclosed single-document skeleton: if `current` has no <html> root yet,
    structure fragments are wrapped into a fresh <!doctype html><html><head><body> frame so
    the accumulator is always a self-contained document (side_effect_checks can pass once the
    required sections exist).
    """
    seg = segment.strip()
    if not seg:
        return current
    low = seg.lower()
    has_root = "<html" in current.lower() and "<body" in current.lower()

    # style → merge into <style> block; interaction → merge into <script> block.
    if action == "style":
        m = re.search(r"<style[^>]*>(.*?)</style>", seg, re.DOTALL | re.IGNORECASE)
        css = m.group(1) if m else seg
        if "<style>" in current:
            return re.sub(
                r"(</style>)",
                lambda mo: css + "\n" + mo.group(0),
                current,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
        if not has_root:
            return _skeleton(css_body=css)
        return current.replace("</head>", f"<style>\n{css}\n</style>\n</head>", 1)
    if action == "interaction":
        m = re.search(r"<script[^>]*>(.*?)</script>", seg, re.DOTALL | re.IGNORECASE)
        js = m.group(1) if m else seg
        if "</script>" in current:
            return re.sub(
                r"(</script>)",
                lambda mo: js + "\n" + mo.group(0),
                current,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
        if not has_root:
            return _skeleton(js_body=js)
        return current.replace("</body>", f"<script>\n{js}\n</script>\n</body>", 1)

    # structure/polish content. If segment itself is a full document, adopt it wholesale.
    if low.startswith("<!doctype") or "<html" in low:
        return seg if not has_root else _merge_full_doc(current, seg)

    # If we don't yet have a skeleton, wrap the fragment into one.
    if not has_root:
        return _skeleton(body=seg)
    if "</body>" in current:
        return current.replace("</body>", seg + "\n</body>", 1)
    return current + "\n" + seg


def _skeleton(*, body: str = "", css_body: str = "", js_body: str = "") -> str:
    """A minimal enclosed single-document skeleton with optional parts."""
    head_extra = ""
    if css_body:
        head_extra += f"\n<style>\n{css_body}\n</style>\n"
    if js_body:
        js_body = f"\n<script>\n{js_body}\n</script>\n"
    return (
        "<!doctype html>\n<html lang=\"zh\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>应用</title>{head_extra}\n</head>\n"
        f"<body>\n{body}\n{js_body}\n</body>\n</html>"
    )


def _merge_full_doc(current: str, segment: str) -> str:
    """Both current and segment are full documents → prefer the newest (segment)."""
    return segment


# ======================================================================
# Program-side side-effect / structural checks
# ======================================================================


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
    for pat, label in (
        (r'src\s*=\s*["\']\s*https?://[^"\']+\.js', "外链 JS"),
        (r'href\s*=\s*["\']\s*https?://[^"\']+\.css', "外链 CSS"),
    ):
        if re.search(pat, low):
            violations.append("存在" + label + "（应全部内联）")
    return violations


# ======================================================================
# Cross-model verification + program scoring
# ======================================================================

VERIFY_SYSTEM = (
    "你是独立的验收评审。对照核验项（verify_items）检查生成的 HTML：逐项给出是否满足。\n"
    "只输出 JSON：{\"checked\":{\"项原文\":true/false},\"missing\":[\"不满足的项\"],\"score\":0.0}。\n"
    "score = 已满足项 / 总项数（0~1）。只根据 HTML 内容实判，不宽松放水。"
)


async def verify_html(ctx: RunContext, verify_items: list[str], html: str) -> dict:
    """A *different* model checks each verify_item against the HTML → score."""
    if not verify_items:
        return {"checked": {}, "missing": [], "score": 1.0}
    try:
        result = await gateway.chat(
            ctx,
            [
                {"role": "system", "content": VERIFY_SYSTEM},
                {"role": "user", "content": "核验项：\n" + "\n".join(f"- {v}" for v in verify_items) + "\n\nHTML：\n" + html[:12000]},
            ],
            purpose="stage:verify",
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
    except Exception as exc:  # noqa: BLE001 - verifier failure is not fatal; degrade conservatively
        logger.warning("verify_html 失败（%s），降级为通过", exc)
        return {"checked": {}, "missing": [], "score": 1.0}


# ======================================================================
# Stage executor
# ======================================================================


def _snapshot(stages: list[dict], stage_idx: int, current: str, requirement: str) -> dict:
    """A resume snapshot: stage_idx is the stage about to run (0-based); `current` is the
    HTML accumulated from all *completed* stages, so the two agree on resume."""
    return {
        "requirement": requirement,
        "stages": stages,
        "stage_idx": stage_idx,
        "current": current,
    }


async def _run_scalar(ctx: RunContext, emit, checkpoint, stages: list[dict], requirement: str, current: str = "", start_idx: int = 0) -> dict:
    """Execute all stages in a scalar loop; each stage = one generation call + verify.

    `current` / `start_idx` are only non-default on crash-resume: continue from the
    accumulated HTML at `start_idx` — completed stages are never re-run.
    """
    total = len(stages)
    for idx in range(start_idx, total):
        stage = stages[idx]
        stage_no = idx + 1
        stage_name = stage.get("stage", f"阶段{stage_no}")
        await emit("stage", {"index": stage_no, "total": total, "text": f"阶段 {stage_no}/{total}：{stage_name}", "stage": stage_name})
        await emit("stage", {"index": stage_no, "total": total, "text": f"正在生成「{stage_name}」（模型思考中，约需 1–2 分钟）…", "stage": stage_name})
        await checkpoint(_snapshot(stages, idx, current, requirement))
        current = await _generate_stage(ctx, stage, requirement, current)
        await emit("stage", {"index": stage_no, "total": total, "text": f"「{stage_name}」生成完成", "stage": stage_name})
        # stage triple-check
        violations = side_effect_checks(current)
        if violations:
            await emit("verify", {"text": f"结构性校验未过：{'；'.join(violations)}", "score": 0.0, "passed": False})
            current = await _patch_skeleton(ctx, emit, stage, requirement, current, violations)
            continue
        v = await verify_html(ctx, stage.get("verify_items") or [], current)
        await emit("verify", {"text": f"核验分 {v['score']:.2f}" + (f"，缺失：{'；'.join(v['missing'][:3])}" if v["missing"] else "，全部通过"), "score": v["score"], "passed": v["score"] >= VERIFY_PASS_SCORE, "missing": v["missing"]})
        if v["score"] < VERIFY_PASS_SCORE:
            await emit("stage", {"index": stage_no, "total": total, "text": f"分数不足 {VERIFY_PASS_SCORE}，在阶段内补缺失节点"})
            # 打回 = 在本阶段内补缺失节点，保留已完成产出继续跑，绝不从头重生成。
            current = await _patch_missing(ctx, emit, stage, requirement, current, v["missing"])
    await checkpoint(_snapshot(stages, total, current, requirement))
    _validate_html(current)
    title = _extract_title(current) or requirement[:40]
    return {"html": current, "title": title}


PATCH_SYSTEM = (
    "你是补丁工程师。当前阶段已产出大部分内容，但阶段验收缺了一项验收项 / 骨架不完整。"
    "你只负责那一个缺失点，输出对应的 HTML 片段（structure→HTML 标签；style→CSS；"
    "interaction→JS；polish→细节）。已存在的部分不要重写，只补缺失。\\n"
    "只输出 HTML 片段本身。"
)


async def _patch(
    ctx: RunContext, emit, stage: dict, requirement: str, current: str,
    focus: str, node_action: str,
) -> str:
    """Generate one patch fragment for a missing item and splice it in (never full regen)."""
    user = (
        f"整体需求：{requirement}\\n当前阶段：{stage.get('stage', '?')}\\n"
        f"需要补齐：{focus}\\n当前 HTML：\\n{current[-3000:]}\\n产出补丁片段。"
    )
    try:
        result = await gateway.chat(
            ctx,
            [
                {"role": "system", "content": PATCH_SYSTEM},
                {"role": "user", "content": user},
            ],
            purpose="stage:patch",
            model=PLAN_MODEL,
            max_tokens=STAGE_TOKENS,
            temperature=0.2,
        )
        seg = sanitize_html(result.content)
        return _splice(current, node_action, seg)
    except Exception as exc:  # noqa: BLE001 - patch failure is non-fatal; keep prior HTML
        logger.warning("patch 失败（%s），保留当前 HTML", exc)
        await emit("verify", {"text": f"补丁失败：{exc}，保留当前结果", "score": 0.0, "passed": False})
        return current


async def _patch_missing(
    ctx: RunContext, emit, stage: dict, requirement: str, current: str, missing: list[str],
) -> str:
    """Score below threshold → patch missing items in-place (never full regen)."""
    return await _patch_stage(ctx, emit, stage, requirement, current, missing, "polish")


async def _patch_skeleton(
    ctx: RunContext, emit, stage: dict, requirement: str, current: str, violations: list[str],
) -> str:
    """Skeleton failure → patch structure fragments in place (never full regen)."""
    return await _patch_stage(ctx, emit, stage, requirement, current, violations, "structure")


async def _patch_stage(
    ctx: RunContext, emit, stage: dict, requirement: str, current: str,
    items: list[str], prefer_action: str,
) -> str:
    """Patch the stage in place for missing items, re-verify, and return the final HTML.

    Appends small patch fragments for *only* the missing items, splicing them into the
    accumulated HTML; completed output is preserved. Stops once verification passes, or
    after PATCH_MAX_RETRY rounds to avoid an infinite loop.
    """
    rounds = 0
    pending = items
    while pending and rounds < PATCH_MAX_RETRY:
        rounds += 1
        patched_any = False
        for item in pending[:4]:
            action = "structure" if any(k in item.lower() for k in ("html", "style", "script", "骨架", "标签结构")) else prefer_action
            await emit("dag", {"node": "patch", "action": action, "text": f"阶段内补节点：{item}"})
            before = current
            current = await _patch(ctx, emit, stage, requirement, current, item, action)
            if current != before:
                patched_any = True
        if not patched_any:
            break  # nothing moved; avoid an infinite patch loop
        violations = side_effect_checks(current)
        if not violations:
            v = await verify_html(ctx, stage.get("verify_items") or [], current)
            await emit("verify", {"text": f"补丁后核验分 {v['score']:.2f}" + (f"，缺失：{'；'.join(v['missing'][:3])}" if v["missing"] else ""), "score": v["score"], "passed": v["score"] >= VERIFY_PASS_SCORE, "missing": v["missing"]})
            if v["score"] >= VERIFY_PASS_SCORE or not v["missing"]:
                return current
            pending = v["missing"]
        else:
            await emit("verify", {"text": f"补丁后结构校验未过：{'；'.join(violations[:3])}", "score": 0.0, "passed": False})
            pending = violations
    return current


async def run_stages(ctx: RunContext, emit: Callable[..., Any], checkpoint: Callable[..., Any], requirement: str) -> dict:
    """Public entry: plan stages → scalar stage execution → triple-verified full HTML.

    ``ctx`` / ``emit`` / ``checkpoint`` are passed down explicitly (no globals), so multiple
    runs can stream concurrently without cross-talk.
    """
    await emit("stage", {"index": 0, "total": 0, "text": "正在拆解需求为阶段…"})
    stages = await plan_stages(ctx, requirement)
    await emit("stage", {"index": 0, "total": len(stages), "text": f"已拆解为 {len(stages)} 个阶段"})
    # checkpoint the plan immediately, so a crash before the first stage still resumes
    # from this stage list (instead of re-planning from scratch).
    await checkpoint(_snapshot(stages, 0, "", requirement))
    return await _run_scalar(ctx, emit, checkpoint, stages, requirement)


async def run_stages_resume(ctx: RunContext, emit: Callable[..., Any], checkpoint: Callable[..., Any], requirement: str, state: dict) -> dict:
    """Resume a long run from a saved checkpoint — never restart completed stages.

    Reconstructs the accumulated HTML from `state`, then continues from `stage_idx`.
    Falls back to a fresh run if `state` has no stages.
    """
    stages = state.get("stages") or []
    if not stages:
        return await run_stages(ctx, emit, checkpoint, requirement)
    current = state.get("current") or ""
    stage_idx = max(0, int(state.get("stage_idx") or 0))
    await emit("stage", {"index": 0, "total": len(stages), "text": f"断点续跑：从阶段 {stage_idx + 1}/{len(stages)} 继续"})
    return await _run_scalar(ctx, emit, checkpoint, stages, requirement, current=current, start_idx=stage_idx)
