"""Molecules Forge HTTP API (single-user demo, no login)."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from models.agent_runs import AgentRuns
from models.chat_messages import ChatMessages
from models.llm_calls import LlmCalls
from models.projects import Projects
from models.races import Races
from services.forge import agent, extras, gateway, ops, repo, run_manager, spec
from services.forge.breaker import breakers
from services.forge.util import DomainError, dumps, loads

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/forge", tags=["forge"])


def _err(exc: Exception) -> HTTPException:
    status = 404 if "不存在" in str(exc) else 400
    return HTTPException(status_code=status, detail=str(exc))


def _project_row(project: dict) -> dict:
    return {
        "id": project["id"],
        "name": project["name"],
        "description": project.get("description"),
        "slug": project["slug"],
        "published": project.get("published"),
        "html": project.get("html"),
        "updated_at": project.get("updated_at"),
    }


# ---------- request bodies ----------

class CreateProject(BaseModel):
    prompt: Optional[str] = Field(default=None, max_length=4000)
    model: Optional[str] = None


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    model: Optional[str] = None


class RaceBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    models: Optional[list[str]] = None


class RunBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    project_id: Optional[int] = None
    model: Optional[str] = None


class SpecBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    model: Optional[str] = None


# ---------- projects ----------

@router.get("/projects")
async def projects() -> list[dict]:
    rows = await repo.find(Projects, order=Projects.id.desc(), limit=100)
    return [_project_row(r) for r in rows]


@router.post("/projects")
async def create_project(body: CreateProject) -> dict:
    try:
        if not body.prompt:
            raise DomainError("请提供应用描述")
        project = await agent.create_from_prompt(body.prompt, body.model)
    except (DomainError, gateway.GatewayError) as exc:
        raise _err(exc) from exc
    return _project_row(project)


@router.get("/projects/{project_id}")
async def get_project(project_id: int) -> dict:
    try:
        project = await ops.get_project(project_id)
    except DomainError as exc:
        raise _err(exc) from exc
    return _project_row(project)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: int) -> dict:
    await ops.get_project(project_id)
    await repo.remove(ChatMessages, ChatMessages.project_id == project_id)
    await repo.remove(AgentRuns, AgentRuns.project_id == project_id)
    await repo.remove(Projects, Projects.id == project_id)
    return {"ok": True}


@router.get("/projects/{project_id}/messages")
async def messages(project_id: int) -> list[dict]:
    rows = await repo.find(ChatMessages, ChatMessages.project_id == project_id, order=ChatMessages.id, limit=200)
    return [{**r, "meta": loads(r.get("meta_json"), {}), "meta_json": None} for r in rows]


@router.post("/projects/{project_id}/chat")
async def chat(project_id: int, body: ChatBody) -> dict:
    try:
        return await agent.chat(project_id, body.message, body.model)
    except (DomainError, gateway.GatewayError) as exc:
        raise _err(exc) from exc


# ---------- requirement understanding (pre-generation) ----------

@router.post("/spec")
async def understand_spec(body: SpecBody) -> dict:
    """解析一句话需求 → 结构化理解 + 是否需要追问。生成前的轻量步骤，失败也 fail-open。"""
    return await spec.understand(body.prompt, body.model)


# ---------- published runtime ----------

@router.get("/runtime/{slug}", response_class=HTMLResponse)
async def runtime_app(slug: str) -> HTMLResponse:
    """Serve the published single-file HTML app directly."""
    try:
        project = await ops.get_project(slug)
    except DomainError as exc:
        raise _err(exc) from exc
    if not project.get("published"):
        raise HTTPException(403, "该应用尚未发布")
    return HTMLResponse(project.get("html") or "<h1>空应用</h1>")


# ---------- asynchronous run + SSE ----------

@router.post("/runs")
async def start_run(body: RunBody) -> dict:
    """Start an async run (long DAG or short ReAct). Returns run_key immediately;
    progress streams via GET /runs/{run_key}/events."""
    import uuid

    run_key = uuid.uuid4().hex[:16]
    await repo.add(AgentRuns, run_key=run_key, project_id=body.project_id, status="running", user_input=body.message[:2000])
    run_manager.start_run(run_key, agent.start_forge(run_key, body.message, body.project_id, body.model))
    return {"run_key": run_key, "project_id": body.project_id}


@router.get("/runs/{run_key}/events")
async def run_events(run_key: str) -> StreamingResponse:
    """SSE stream of stage/dag/verify/done/error events for a run."""
    stream = await run_manager.make_sse_stream(run_key)
    return StreamingResponse(stream, media_type="text/event-stream")


# ---------- runs / usage / breakers ----------

@router.get("/runs")
async def runs() -> list[dict]:
    rows = await repo.find(AgentRuns, order=AgentRuns.id.desc(), limit=30)
    return [{"run_key": r["run_key"], "project_id": r["project_id"], "intent": r["intent"], "status": r["status"], "input": (r["user_input"] or "")[:80], "usage": loads(r["usage_json"], {}), "created_at": r["created_at"]} for r in rows]


@router.get("/usage")
async def usage() -> dict:
    recent = await repo.find(LlmCalls, order=LlmCalls.id.desc(), limit=20)
    return {**await gateway.usage_summary(), "recent": [{"purpose": c["purpose"], "model": c["model"], "tokens": (c["prompt_tokens"] or 0) + (c["completion_tokens"] or 0), "cost_cny": round(c["cost_cny"] or 0, 4), "latency_ms": c["latency_ms"], "status": c["status"], "error": c["error"]} for c in recent]}


@router.get("/breakers")
async def breaker_list() -> list[dict]:
    return await breakers.snapshot()


@router.post("/breakers/reset")
async def breaker_reset() -> dict:
    await breakers.reset()
    return {"ok": True}


# ---------- race ----------

@router.post("/races")
async def start_race(body: RaceBody) -> dict:
    """Start a race; returns {id, run_key} immediately. Progress streams via /races/{id}/events."""
    return await extras.start_race(body.prompt, body.models)


@router.get("/races")
async def races() -> list[dict]:
    rows = await repo.find(Races, order=Races.id.desc(), limit=30)
    return [{**r, "results": loads(r["results_json"], {}), "results_json": None} for r in rows]


@router.get("/races/{race_id}")
async def race(race_id: int) -> dict:
    row = await repo.get(Races, race_id)
    if not row:
        raise HTTPException(404, "对比任务不存在")
    return {**row, "results": loads(row["results_json"], {}), "results_json": None}


@router.get("/races/{race_id}/events")
async def race_events(race_id: int) -> StreamingResponse:
    """SSE stream of race_model/done/error events for one race (keyed by race-{id})."""
    row = await repo.get(Races, race_id)
    if not row:
        raise HTTPException(404, "对比任务不存在")
    stream = await run_manager.make_sse_stream(f"race-{race_id}")
    return StreamingResponse(stream, media_type="text/event-stream")


@router.post("/races/{race_id}/select/{index}")
async def select_race(race_id: int, index: int) -> dict:
    try:
        return _project_row(await extras.select_race(race_id, index))
    except DomainError as exc:
        raise _err(exc) from exc
