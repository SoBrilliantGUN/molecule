"""Race mode: multi-model parallel HTML generation + cross-model scoring."""

import asyncio
import logging
import os
import uuid

from models.races import Races
from services.forge import codegen, ops, repo
from services.forge.budget import Budget
from services.forge.gateway import GatewayError, RunContext
from services.forge.util import DomainError, dumps, extract_json, loads

logger = logging.getLogger(__name__)

RACE_MODELS = [m.strip() for m in os.environ.get("RACE_MODELS", "deepseek-ai/DeepSeek-V4-Flash,Qwen/Qwen3-235B-A22B-Instruct,zai-org/GLM-5.3").split(",") if m.strip()]
VERIFY_MODEL = os.environ.get("LLM_VERIFY_MODEL", "Qwen/Qwen3-Coder-480B-A35B-Instruct")

SCORE_PROMPT = (
    "你是应用评审。对照需求，对下面的单文件 HTML 应用按三个维度打分（0–10 整数）："
    "quality（功能完整度、代码正确、可运行）、security（输入校验、无 XSS/注入隐患、localStorage 使用安全）、"
    "ux（视觉设计、交互清晰、可用性）。只输出 JSON：{\"quality\": N, \"security\": N, \"ux\": N, \"issues\": [\"...\"]}。"
)


def _ctx(project_id: int | None = None) -> RunContext:
    return RunContext(run_key=uuid.uuid4().hex[:16], project_id=project_id, budget=Budget())


async def _score(ctx: RunContext, prompt: str, html: str) -> dict:
    from services.forge import gateway

    messages = [
        {"role": "system", "content": SCORE_PROMPT},
        {"role": "user", "content": f"需求：{prompt}\n\nHTML（截断）：\n{html[:12000]}"},
    ]
    try:
        result = await gateway.chat(ctx, messages, purpose="race:score", model=VERIFY_MODEL, max_tokens=400, temperature=0)
        data = extract_json(result.content)
        score = {k: max(0, min(10, int(data.get(k, 0)))) for k in ("quality", "security", "ux")}
        issues = [str(i) for i in (data.get("issues") or [])][:6]
        return {"score": score, "total": round(sum(score.values()) / 3, 2), "issues": issues}
    except (ValueError, GatewayError, KeyError) as exc:
        return {"score": {"quality": 5, "security": 5, "ux": 5}, "total": 5.0, "issues": [str(exc)[:120]]}


async def _race_one(prompt: str, model: str) -> dict:
    ctx = _ctx()
    usage = ctx.budget.snapshot()
    try:
        out = await codegen.generate(ctx, prompt, model=model)
        check = await _score(ctx, prompt, out["html"])
        return {
            "model": model, "ok": True, "title": out["title"], "html": out["html"],
            "score": check["score"], "total": check["total"], "issues": check["issues"],
            "cost_cny": ctx.budget.snapshot()["cost_cny"], "tokens": ctx.budget.snapshot()["tokens"],
        }
    except (DomainError, GatewayError) as exc:
        return {"model": model, "ok": False, "error": str(exc)[:200], "total": 0, "cost_cny": ctx.budget.snapshot()["cost_cny"], "tokens": ctx.budget.snapshot()["tokens"]}


async def start_race(prompt: str, models: list[str] | None = None) -> dict:
    models = [m for m in (models or RACE_MODELS) if m][:3]
    results = await asyncio.gather(*(_race_one(prompt, m) for m in models))
    best = max(range(len(results)), key=lambda i: results[i].get("total", 0))
    return await repo.add(Races, prompt=prompt, results_json=dumps({"items": results, "recommended": best}), status="done", selected_index=None)


async def select_race(race_id: int, index: int) -> dict:
    race = await repo.get(Races, race_id)
    if not race:
        raise DomainError("对比任务不存在")
    items = loads(race["results_json"], {}).get("items", [])
    if not 0 <= index < len(items) or not items[index].get("ok"):
        raise DomainError("该方案不可选")
    chosen = items[index]
    project = await ops.create_project(chosen.get("title") or race["prompt"][:40], chosen["html"], description=race["prompt"][:200])
    await repo.update(Races, race_id, selected_index=index, project_id=project["id"])
    return project
