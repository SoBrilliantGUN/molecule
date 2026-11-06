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
from services.forge import codegen, gateway, ops, repo
from services.forge.budget import Budget, BudgetExceeded
from services.forge.gateway import GatewayError, RunContext
from services.forge.util import DomainError, dumps, extract_json

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
    """New app: one-liner -> single-file HTML -> project."""
    ctx = RunContext(run_key=uuid.uuid4().hex[:16], budget=Budget())
    state: ForgeState = {"message": prompt, "project_id": None, "current_html": None, "events": [], "run_key": ctx.run_key}
    graph = _build_graph().compile()
    result = await graph.ainvoke(state, config={"configurable": {"ctx": ctx}})
    await _record_run(ctx.run_key, None, result, ctx)
    project = await ops.get_project(result["project_id"])
    await repo.add(ChatMessages, project_id=project["id"], role="user", content=prompt)
    await repo.add(ChatMessages, project_id=project["id"], role="assistant", content=result.get("reply", ""), meta_json=dumps({"usage": ctx.budget.snapshot(), "run_key": ctx.run_key}))
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
