"""Forge agent：真正的生成入口是 `_run_forge`（异步 run + ReAct 三校核验）。

前端统一走 `POST /runs` → `start_forge` → `_run_forge` → `_run_classified` →
`generate_verified`（LangGraph StateGraph 编排 + SQLite checkpoint 断点续跑）。
早期遗留的 LangGraph 直线图（intent → generate → save）及 `chat` / `create_from_prompt`
同步兼容路径已删除。
"""

import logging

from models.agent_runs import AgentRuns
from services.forge import ops, persist, repo, seed
from services.forge.budget import Budget
from services.forge.gateway import RunContext
from services.forge.util import dumps

logger = logging.getLogger(__name__)


async def start_forge(run_key: str, prompt: str, project_id: int | None, model: str | None) -> dict:
    """Background entry for a fresh run: emits stage/verify/done events bound to run_key.

    Called by the router in an asyncio task; events flow to run_manager.emit so SSE
    subscribers see progress live. Returns the final {project_id, status} on success.
    """
    return await _run_forge(run_key, prompt, project_id, model)


async def resume_forge(run_key: str) -> dict:
    """Re-run an interrupted run (status="running" after a crash).

    The ReAct path is a LangGraph graph with a SQLite checkpointer keyed by run_key, so
    resume re-enters the graph and continues from the most recent checkpoint (skipping
    completed nodes) rather than regenerating from scratch.
    """
    rows = await repo.find(AgentRuns, AgentRuns.run_key == run_key, limit=1)
    row = rows[0] if rows else None
    if not row:
        return {"run_key": run_key, "status": "error", "error": "run not found"}
    project_id = row.get("project_id")
    prompt = row.get("user_input") or ""
    return await _run_forge(run_key, prompt, project_id, None)


async def _run_forge(run_key: str, prompt: str, project_id: int | None, model: str | None) -> dict:
    """Shared background run core (fresh or resume): emits events bound to run_key.

    Called by the router in an asyncio task; events flow to run_manager.emit so SSE
    subscribers see progress live. Returns the final {project_id, status}.
    """
    from services.forge import run_manager

    ctx = RunContext(run_key=run_key, project_id=project_id, budget=Budget())

    async def emit(event_type: str, data: dict) -> None:
        await run_manager.emit(run_key, event_type, data)

    try:
        mock = seed.match_mock(prompt)
        if mock:
            await emit("stage", {"index": 0, "total": 0, "text": f"命中预生成示例「{mock['name']}」，直接返回（无需调用模型）"})
            out = {"html": mock["html"], "title": mock["name"], "verify": {}}
        else:
            await emit("stage", {"index": 0, "total": 0, "text": "接收需求，开始生成…"})
            out = await _run_classified(ctx, emit, prompt, project_id, model)
        project = await _persist_project(ctx, out, prompt, project_id)
        row = await repo.find(AgentRuns, AgentRuns.run_key == run_key, limit=1)
        if row:
            await repo.update(AgentRuns, row[0]["id"], status="done")
        await emit("done", {
            "project_id": project["id"],
            "name": project["name"],
            "verify_failed": bool(out.get("verify_failed")),
            "verify_score": float((out.get("verify") or {}).get("score", 0.0)),
        })
        return {"project_id": project["id"], "status": "done"}
    except Exception as exc:  # noqa: BLE001 - surface error to the SSE stream
        logger.error("forge run %s failed: %s", run_key, exc, exc_info=True)
        row = await repo.find(AgentRuns, AgentRuns.run_key == run_key, limit=1)
        if row:
            await repo.update(AgentRuns, row[0]["id"], status="error", result_json=dumps({"error": str(exc)[:500]}))
        await emit("error", {"message": str(exc)})
        return {"project_id": project_id, "status": "error", "error": str(exc)}


async def _run_classified(ctx: RunContext, emit, prompt: str, project_id: int | None, model: str | None) -> dict:
    """单次完整生成 + 三校核验（ReAct）。

    current_html 为空（新建占位项目）视为无现有内容 → 全新生成；有内容 → 在其上迭代。
    """
    from services.forge.forge_react import generate_verified

    current = None
    if project_id is not None:
        project = await ops.get_project(project_id)
        current = project.get("html") or None
    return await generate_verified(ctx, prompt, current, model=model, emit=emit)


async def _persist_project(ctx: RunContext, out: dict, prompt: str, project_id: int | None) -> dict:
    """Persist the final HTML into a project (+ idempotent chat messages). project_id None -> create new."""
    return await persist.persist_result(
        project_id=project_id,
        html=out["html"],
        title=out.get("title", ""),
        prompt=prompt,
        verify=out.get("verify", {}),
        verify_failed=bool(out.get("verify_failed")),
        usage=ctx.budget.snapshot(),
    )
