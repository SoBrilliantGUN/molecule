"""Race mode: multi-model parallel HTML generation (live SSE, user picks one)."""

import asyncio
import logging
import os

from models.agent_runs import AgentRuns
from models.races import Races
from services.forge import codegen, ops, repo
from services.forge.budget import Budget
from services.forge.gateway import GatewayError, RunContext
from services.forge.util import DomainError, dumps, loads

logger = logging.getLogger(__name__)

# 赛马候选模型：走硅基流动（模型名带 org/ 前缀）。去掉 GLM-5.3——它是「强制思考」型号，
# 无法关闭思考（发 enable_thinking=false 会 4xx），又慢，不适合赛马的并行快出方案。
# 第二个候选用 Qwen3-Coder-30B（快、专攻代码）；旧名 Qwen3-235B-A22B-Instruct 已不存在。
RACE_MODELS = [m.strip() for m in os.environ.get("RACE_MODELS", "deepseek-ai/DeepSeek-V4-Flash,Qwen/Qwen3-Coder-30B-A3B-Instruct").split(",") if m.strip()]


async def _race_one(run_key: str, prompt: str, model: str) -> dict:
    """Generate one candidate app for a model, emitting live ``race_model`` events."""
    from services.forge import run_manager

    async def emit(event_type: str, data: dict) -> None:
        await run_manager.emit(run_key, event_type, data)

    ctx = RunContext(run_key=f"{run_key}:{model}", project_id=None, budget=Budget())
    try:
        await emit("race_model", {"model": model, "status": "running", "text": "生成 HTML…"})
        out = await codegen.generate(ctx, prompt, model=model, provider="siliconflow")
        await emit("race_model", {"model": model, "status": "done", "title": out["title"], "html": out["html"]})
        return {"model": model, "ok": True, "title": out["title"], "html": out["html"]}
    except (DomainError, GatewayError) as exc:
        await emit("race_model", {"model": model, "status": "error", "text": str(exc)[:200]})
        return {"model": model, "ok": False, "error": str(exc)[:200]}


async def _race_worker(run_key: str, race_id: int, prompt: str, models: list[str]) -> None:
    from services.forge import run_manager

    results = await asyncio.gather(*(_race_one(run_key, prompt, m) for m in models))
    await repo.update(Races, race_id, results_json=dumps({"items": results}), status="done")
    await run_manager.emit(run_key, "done", {"race_id": race_id})


async def start_race(prompt: str, models: list[str] | None = None, client_key: str | None = None) -> dict:
    """Start a race (idempotent by client_key): persist a Races row, then run all models in parallel.

    Returns {id, run_key, models} immediately; per-model progress streams via GET /races/{id}/events.
    """
    from services.forge import run_manager

    models = [m for m in (models or RACE_MODELS) if m][:3]

    # 幂等：同一 client_key 已存在赛马则直接复用，避免刷新/重进时重复开赛。
    if client_key:
        existing = await repo.find(Races, Races.client_key == client_key, order=Races.id.desc(), limit=1)
        if existing:
            race = existing[0]
            return {"id": race["id"], "run_key": f"race-{race['id']}", "prompt": race["prompt"], "status": race["status"], "models": models}

    race = await repo.add(Races, prompt=prompt, results_json=dumps({"items": []}), status="running", selected_index=None, client_key=client_key)
    race_id = race["id"]
    run_key = f"race-{race_id}"
    # Reuse run_manager's SSE event store (keyed by run_key) for the race stream.
    await repo.add(AgentRuns, run_key=run_key, project_id=None, status="running", user_input=prompt[:2000])
    run_manager.start_run(run_key, _race_worker(run_key, race_id, prompt, models))
    return {"id": race_id, "run_key": run_key, "prompt": prompt, "status": "running", "models": models}


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
