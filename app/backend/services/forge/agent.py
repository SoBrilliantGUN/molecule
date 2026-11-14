"""LangGraph single-agent graph: intent -> generate -> save.

One reactive graph with an intent router. `create` (new app from a one-liner) and
`iterate` (edit an existing app) share the same graph — they differ only in initial
state. Every run is recorded to `agent_runs` for observability.

intent -> generate -> save -> END
"""

import logging
import os
import uuid
from operator import add
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from models.agent_runs import AgentRuns
from models.chat_messages import ChatMessages
from services.forge import codegen, gateway, ops, repo, seed
from services.forge.budget import Budget, BudgetExceeded
from services.forge.gateway import GatewayError, RunContext
from services.forge.util import DomainError, dumps

logger = logging.getLogger(__name__)

FAST_MODEL = os.environ.get("LLM_INTENT_MODEL", "deepseek-ai/DeepSeek-V4-Flash")


class ForgeState(TypedDict, total=False):
    message: str
    project_id: int | None
    current_html: str | None
    intent: str
    html: str
    title: str
    reply: str
    run_key: str
    events: Annotated[list[dict[str, str]], add]


# ---------- nodes ----------

async def _intent_node(state: ForgeState, ctx: RunContext) -> dict:
    """Route intent: 'build' (new app) vs 'iterate' (edit existing). Fail-open to build."""
    message = state["message"]
    if state.get("project_id") is not None:
        intent = "iterate"
    else:
        intent = "build"
    return {"intent": intent, "events": [{"kind": "intent", "text": "新建应用" if intent == "build" else "迭代修改"}]}


async def _generate_node(state: ForgeState, ctx: RunContext) -> dict:
    out = await codegen.generate(ctx, state["message"], state.get("current_html"))
    return {"html": out["html"], "title": out["title"], "events": [{"kind": "generate", "text": "已生成单文件 HTML"}]}


async def _save_node(state: ForgeState, ctx: RunContext) -> dict:
    if state.get("project_id") is not None:
        project = await ops.update_project_html(state["project_id"], state["html"], state.get("title") or "")
        reply = "已根据你的修改重新生成应用，预览区已更新。"
    else:
        project = await ops.create_project(state["title"] or state["message"][:40], state["html"], description=state["message"][:200])
        reply = f"已根据你的描述创建「{project['name']}」。继续告诉我要怎么改。"
    return {"project_id": project["id"], "reply": reply, "events": [{"kind": "save", "text": f"已保存为应用 #{project['id']}"}]}


def _build_graph() -> StateGraph:
    g = StateGraph(ForgeState)

    async def intent(state: ForgeState, config):
        ctx = config["configurable"]["ctx"]
        return await _intent_node(state, ctx)

    async def generate(state: ForgeState, config):
        ctx = config["configurable"]["ctx"]
        return await _generate_node(state, ctx)

    async def save(state: ForgeState, config):
        ctx = config["configurable"]["ctx"]
        return await _save_node(state, ctx)

    g.add_node("intent", intent)
    g.add_node("generate", generate)
    g.add_node("save", save)
    g.add_edge(START, "intent")
    g.add_edge("intent", "generate")
    g.add_edge("generate", "save")
    g.add_edge("save", END)
    return g


# ---------- public entry points ----------

async def _execute(state: ForgeState) -> dict:
    ctx = RunContext(run_key=state.get("run_key") or uuid.uuid4().hex[:16], project_id=state.get("project_id"), budget=Budget())
    state = {**state, "run_key": ctx.run_key, "events": state.get("events", [])}

    graph = _build_graph().compile()
    result = await graph.ainvoke(state, config={"configurable": {"ctx": ctx}})

    await _record_run(ctx.run_key, state.get("project_id"), result, ctx)
    return {
        "run_key": ctx.run_key,
        "status": "done",
        "reply": result.get("reply", ""),
        "intent": result.get("intent"),
        "events": result.get("events", []),
        "project_id": result.get("project_id"),
        "usage": ctx.budget.snapshot(),
    }


async def _record_run(run_key: str, project_id: int | None, state: dict, ctx: RunContext) -> None:
    rows = await repo.find(AgentRuns, AgentRuns.run_key == run_key, limit=1)
    fields = dict(status="done", intent=state.get("intent"), checkpoint_json=dumps({k: v for k, v in state.items() if k != "history"}), usage_json=dumps(ctx.budget.snapshot()))
    if rows:
        await repo.update(AgentRuns, rows[0]["id"], **fields)
    else:
        await repo.add(AgentRuns, run_key=run_key, project_id=project_id, user_input=state.get("message", "")[:2000], **fields)


async def create_from_prompt(prompt: str, model: str | None = None) -> dict:
    """New app: one-liner -> single-file HTML -> project. Kept as the synchronous
    compat path (POST /projects); the streaming path goes through start_forge."""
    ctx = RunContext(run_key=uuid.uuid4().hex[:16], budget=Budget())
    mock = seed.match_mock(prompt)
    out = {"html": mock["html"], "title": mock["name"], "verify": {}} if mock else await _run_classified(ctx, _noop_emit, prompt, None, model)
    project = await _persist_project(ctx, out, prompt, None)
    return project


async def _noop_emit(_type: str, _data: dict) -> None:
    """Discard events in the synchronous compat path."""
    return None


async def start_forge(run_key: str, prompt: str, project_id: int | None, model: str | None) -> dict:
    """Background entry for a fresh run: emits stage/verify/done events bound to run_key.

    Called by the router in an asyncio task; events flow to run_manager.emit so SSE
    subscribers see progress live. Returns the final {project_id, status} on success.
    """
    return await _run_forge(run_key, prompt, project_id, model)


async def resume_forge(run_key: str) -> dict:
    """Re-run an interrupted run (status="running" after a crash).

    The ReAct path generates in a single attempt (no mid-run checkpoint), so resume is
    simply a fresh run from the stored user_input. Retired DAG stage checkpoints are gone.
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
    from services.forge.gateway import RunContext
    from services.forge.budget import Budget

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
        await emit("done", {"project_id": project["id"], "name": project["name"]})
        return {"project_id": project["id"], "status": "done"}
    except Exception as exc:  # noqa: BLE001 - surface error to the SSE stream
        logger.error("forge run %s failed: %s", run_key, exc, exc_info=True)
        row = await repo.find(AgentRuns, AgentRuns.run_key == run_key, limit=1)
        if row:
            await repo.update(AgentRuns, row[0]["id"], status="error", result_json=dumps({"error": str(exc)[:500]}))
        await emit("error", {"message": str(exc)})
        return {"project_id": project_id, "status": "error", "error": str(exc)}


async def _run_classified(ctx, emit, prompt: str, project_id: int | None, model: str | None) -> dict:
    """单次完整生成 + 三校核验（ReAct）。DAG 分阶段拼接片段已退役。

    之前按复杂度把新建/大改路由到 DAG，但 DAG 逐阶段拼接片段会产出「空 HTML / 半成品 /
    无限补丁」，反而更糟。统一走 codegen.generate 一次产出完整单文件 HTML + 跨模型核验。
    current_html 为空（新建占位项目）视为无现有内容 → 全新生成；有内容 → 在其上迭代。
    """
    from services.forge.forge_react import generate_verified

    current = None
    if project_id is not None:
        project = await ops.get_project(project_id)
        current = project.get("html") or None
    return await generate_verified(ctx, prompt, current, model=model, emit=emit)


async def _persist_project(ctx: RunContext, out: dict, prompt: str, project_id: int | None) -> dict:
    """Persist the final HTML into a project (+ chat messages). project_id None -> create new."""
    html = codegen.sanitize_html(out["html"])
    title = out.get("title") or prompt[:40]
    if project_id is not None:
        project = await ops.update_project_html(project_id, html, title)
    else:
        project = await ops.create_project(title, html, description=prompt[:200])
    await repo.add(ChatMessages, project_id=project["id"], role="user", content=prompt)
    meta = {"usage": ctx.budget.snapshot(), "verify": out.get("verify", {})}
    await repo.add(ChatMessages, project_id=project["id"], role="assistant", content=f"已生成「{project['name']}」。", meta_json=dumps(meta))
    return project


async def chat(project_id: int, message: str, model: str | None = None) -> dict:
    """Iterate: new requirement applied on top of the existing HTML."""
    project = await ops.get_project(project_id)
    history = (await repo.find(ChatMessages, ChatMessages.project_id == project_id, order=ChatMessages.id.desc(), limit=6))[::-1]
    await repo.add(ChatMessages, project_id=project_id, role="user", content=message)
    state: ForgeState = {
        "message": message,
        "project_id": project_id,
        "current_html": project.get("html"),
        "events": [],
    }
    out = await _execute(state)
    await repo.add(ChatMessages, project_id=project_id, role="assistant", content=out["reply"], meta_json=dumps({"events": out["events"], "usage": out["usage"], "intent": out["intent"], "run_key": out["run_key"]}))
    return out
