"""Molecules Forge HTTP API (single-user demo, no login)."""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
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

# SSE 必需的头：禁止缓冲/缓存，否则事件会被 nginx 或客户端攒成一块才发出，导致「进度不及时」。
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


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
    client_key: Optional[str] = None  # 幂等键：同一 client_key 只开一场赛马


class RunBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    project_id: Optional[int] = None
    model: Optional[str] = None
    client_key: Optional[str] = None  # 幂等键：同一 client_key 只生成一次，前端刷新复用同一次生成


class SpecBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    model: Optional[str] = None


# ---------- projects ----------

@router.get("/projects")
async def projects() -> list[dict]:
    rows = await repo.find(Projects, order=Projects.id.desc(), limit=100)
    running = await repo.find(AgentRuns, AgentRuns.status == "running")
    run_by_project = {r["project_id"]: r["run_key"] for r in running if r.get("project_id")}
    out = []
    for r in rows:
        d = _project_row(r)
        d["generating"] = r["id"] in run_by_project
        d["run_key"] = run_by_project.get(r["id"])
        out.append(d)
    return out


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
    latest = await repo.find(AgentRuns, AgentRuns.project_id == project_id, order=AgentRuns.id.desc(), limit=1)
    run = latest[0] if latest else None
    status = run["status"] if run else None
    error = None
    if run and run["status"] == "error":
        error = (loads(run.get("result_json"), {}) or {}).get("error")
    return {
        **_project_row(project),
        "generating": bool(run and run["status"] == "running"),
        "run_key": run["run_key"] if run else None,
        "status": status,
        "error": error,
    }


@router.post("/projects/{project_id}/publish")
async def publish_project(project_id: int) -> dict:
    """发布：标记为已发布并返回 slug，前端据此拼出公开分享链接 /view/{slug}。"""
    try:
        return _project_row(await ops.publish_project(project_id))
    except DomainError as exc:
        raise _err(exc) from exc


@router.get("/pub/{slug}")
async def public_project(slug: str) -> dict:
    """公开分享视图：按 slug 返回应用（单用户 demo 本就无鉴权，直接可访问）。"""
    try:
        return _project_row(await ops.get_project(slug))
    except DomainError as exc:
        raise _err(exc) from exc


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


# ---------- asynchronous run + SSE ----------

@router.post("/runs")
async def start_run(body: RunBody) -> dict:
    """Start an async run (ReAct 单次生成 + 三校核验). Returns run_key immediately;
    progress streams via GET /runs/{run_key}/events.

    新建应用会立即创建占位项目（name=提示词前 40 字），让「我的项目」实时看到生成中的条目；
    用户点进去可续看进度，生成完成后直接展示应用。
    """
    import uuid

    run_key = body.client_key or uuid.uuid4().hex[:16]
    # 幂等：同一 client_key 若已存在（running 或 done），直接复用，不重复生成。
    # 这样前端在生成页刷新/重进时，后端返回同一次 run 和同一个占位项目，不会再造新应用。
    existing = await repo.find(AgentRuns, AgentRuns.run_key == run_key, limit=1)
    if existing:
        return {"run_key": run_key, "project_id": existing[0].get("project_id")}

    project_id = body.project_id
    if project_id is None:
        placeholder = await ops.create_project(body.message.strip()[:40] or "新应用", "", description=body.message[:200])
        project_id = placeholder["id"]
    await repo.add(AgentRuns, run_key=run_key, project_id=project_id, status="running", user_input=body.message[:2000])
    run_manager.start_run(run_key, agent.start_forge(run_key, body.message, project_id, body.model))
    return {"run_key": run_key, "project_id": project_id}


@router.get("/runs/{run_key}/events")
async def run_events(run_key: str) -> StreamingResponse:
    """SSE stream of stage/verify/done/error events for a run."""
    stream = await run_manager.make_sse_stream(run_key)
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


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
    return await extras.start_race(body.prompt, body.models, body.client_key)


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
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


class SelectBody(BaseModel):
    model: str = Field(min_length=1)


@router.post("/races/{race_id}/select")
async def select_race(race_id: int, body: SelectBody) -> dict:
    try:
        return _project_row(await extras.select_race(race_id, body.model))
    except DomainError as exc:
        raise _err(exc) from exc
