"""Race mode: multi-model parallel HTML generation (live SSE, user picks one)."""

import asyncio
import logging
import os

from models.agent_runs import AgentRuns
from models.chat_messages import ChatMessages
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
    """Generate one candidate app for a model via the ReAct graph (per-model checkpoint)."""
    from services.forge import run_manager
    from services.forge.forge_react import generate_verified

    ctx = RunContext(run_key=f"{run_key}:{model}", project_id=None, budget=Budget())

    async def emit_race(status: str, text: str = "", **extra) -> None:
        await run_manager.emit(run_key, "race_model", {"model": model, "status": status, "text": text, **extra})

    async def emit(event_type: str, data: dict) -> None:
        # 把 ReAct 图内部的 stage/verify 事件映射成赛马进度事件（前端赛马页按 race_model 展示）。
        await emit_race("running", data.get("text", ""))

    try:
        await emit_race("running", "生成 HTML…")
        # 复用单路 ReAct 图（三校核验 + 回喂重生成），赛马产物与单路产物同质量；每路独立 checkpoint。
        out = await generate_verified(ctx, prompt, None, model=model, emit=emit, provider="siliconflow")
        await emit_race("done", title=out["title"], html=out["html"])
        return {"model": model, "ok": True, "title": out["title"], "html": out["html"]}
    except (DomainError, GatewayError) as exc:
        await emit_race("error", str(exc)[:200])
        return {"model": model, "ok": False, "error": str(exc)[:200]}


async def _race_worker(run_key: str, race_id: int, prompt: str, models: list[str]) -> None:
    from services.forge import run_manager

    results = await asyncio.gather(*(_race_one(run_key, prompt, m) for m in models))
    await repo.update(Races, race_id, results_json=dumps({"items": results, "models": models}), status="done")
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

    race = await repo.add(Races, prompt=prompt, results_json=dumps({"items": [], "models": models}), status="running", selected_index=None, client_key=client_key)
    race_id = race["id"]
    run_key = f"race-{race_id}"
    # Reuse run_manager's SSE event store (keyed by run_key) for the race stream.
    await repo.add(AgentRuns, run_key=run_key, project_id=None, status="running", user_input=prompt[:2000])
    run_manager.start_run(run_key, _race_worker(run_key, race_id, prompt, models))
    return {"id": race_id, "run_key": run_key, "prompt": prompt, "status": "running", "models": models}


async def resume_race(run_key: str, race_id: int) -> None:
    """后端崩溃后重拉赛马：读 Races 行拿 prompt/models，重新 gather 每路（每路从 checkpoint 续跑）。

    已完成的路其 checkpoint 在 SQLite，重新 ainvoke 会立即返回；未完成的路从断点续跑。
    """
    from services.forge import run_manager

    race = await repo.get(Races, race_id)
    if not race:
        return
    data = loads(race["results_json"], {})
    models = data.get("models") or RACE_MODELS
    run_manager.start_run(run_key, _race_worker(run_key, race_id, race["prompt"], models))


async def select_race(race_id: int, model: str) -> dict:
    race = await repo.get(Races, race_id)
    if not race:
        raise DomainError("对比任务不存在")
    items = loads(race["results_json"], {}).get("items", [])
    # 按模型名匹配，而不是按下标——前端候选顺序可能因 SSE 事件到达顺序不同而与 items 顺序不一致，
    # 按下标会选错（选 A 落 B）。按 model 名精确定位。
    chosen = None
    chosen_index = None
    for idx, item in enumerate(items):
        if item.get("model") == model and item.get("ok"):
            chosen = item
            chosen_index = idx
            break
    if chosen is None:
        raise DomainError("该方案不可选")
    # 落库前与单模型 _persist_project 对齐：清理围栏/解释性前缀，避免残留文字随应用一起展示。
    project = await ops.create_project(chosen.get("title") or race["prompt"][:40], codegen.sanitize_html(chosen["html"]), description=race["prompt"][:200])
    await repo.update(Races, race_id, selected_index=chosen_index, project_id=project["id"])
    # 与单模型 `_persist_project` 对齐：把原始需求也落进聊天历史，否则赛马项目聊天框为空，
    # 退出重进后「之前的对话内容消失」。
    await repo.add(ChatMessages, project_id=project["id"], role="user", content=race["prompt"])
    await repo.add(ChatMessages, project_id=project["id"], role="assistant", content=f"已生成「{project['name']}」。")
    return project
