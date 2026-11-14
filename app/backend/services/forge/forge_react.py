"""ReAct 三校核验 —— 用 LangGraph StateGraph 编排 + SQLite checkpoint 断点续跑。

生成一次完整单文件 HTML，然后对模型产出「不轻信」，三重校验：
  ① ``side_effect_checks`` — 程序侧确定性骨架/结构校验（零额度）；
  ② ``slop_checks`` — 确定性反 AI 味检查（P0 回喂重生成）；
  ③ ``verify_html`` — 换一个不同家的模型（LLM_VERIFY_MODEL）逐项核验，程序重算分。
低于阈值则针对缺失项定向重生成（≤ ``REACT_MAX_RETRY`` 次）。

图结构（每个节点边界 = 一个 checkpoint 点，进程崩溃后同 thread_id 从最近节点续跑）::

    START → normalize(仅新建) → plan_checks → generate → structure_check
                                                              ↓
                ┌── 结构违规 / 反 AI 味 P0 / 核验不通过（有额度）── retry → generate
                └── 通过 / 额度耗尽 / degraded ── finish → END

所有 LLM 调用都走 ``gateway.chat``（budget / breaker / accounting）。checkpoint 持久化到
PostgreSQL（``LANGGRAPH_CHECKPOINT_DSN``，psycopg DSN），``thread_id = run_key``，崩溃重启后
同 run_key 续跑，跳过已完成节点；单次 ``codegen.generate``（最贵的一步）中断仍会从头重跑
（节点级恢复）。checkpoint 表由 ``AsyncPostgresSaver.setup()`` 建（与主库同库）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from services.forge import gateway
from services.forge.codegen import generate, normalize_requirement
from services.forge.gateway import RunContext
from services.forge.verify import side_effect_checks, slop_checks, slop_p0_messages, verify_html

logger = logging.getLogger(__name__)

VERIFY_PASS_SCORE = float(os.environ.get("VERIFY_PASS_SCORE", "0.7"))
REACT_MAX_RETRY = int(os.environ.get("REACT_MAX_RETRY", "2"))

# short task: generate a compact verify checklist from the requirement in one tiny call
PLAN_CHECKS_SYSTEM = (
    "你是验收清单生成器。根据用户的一句话需求，生成 3-5 条这个单文件 HTML 应用必须满足的核验项。\n"
    "核验项要具体、可判真伪（如「能新增和删除待办」而非「功能完整」）。\n"
    '只输出 JSON：{"checks":["...","..."]}。'
)

# 存储安全是硬约束：生成的单文件应用会以 iframe（sandbox）或导出后 file:// 方式打开，
# localStorage 在这些环境可能被禁用。核验清单无论如何生成，都必须包含这一项，防止
# 生成物因「localStorage 不可用」而卡在加载态 / 阻断录入。
_STORAGE_SAFETY = "数据用 localStorage 持久化**完整工作状态**（所有记录/当前游戏局面，而非只存一个统计值如最高分），且读写有 try/catch 兜底、存储不可用时应用仍能完整使用（不卡加载、不阻断录入）"


class ForgeState(TypedDict, total=False):
    requirement: str
    current_html: str | None
    checks: list[str]
    html: str
    title: str
    missing: list[str]
    attempts: int
    slop_warnings: list[dict]
    verify: dict
    model: str | None
    provider: str


async def plan_checks(ctx: RunContext, requirement: str) -> list[str]:
    """One light call → verify checklist for the requirement. Failure → sensible defaults."""
    defaults = [
        "是完整可运行的 HTML 页面（含 <!doctype> / <html> / <head> / <body>）",
        "内联所有 CSS 与 JS，不引用外部 JS/CSS（字体除外）",
        _STORAGE_SAFETY,
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
        if not checks:
            checks = list(defaults)
    except Exception as exc:  # noqa: BLE001 - checks planning must never block generation
        logger.warning("plan_checks 失败（%s），用默认核验项", exc)
        checks = list(defaults)

    # 无论核验清单怎么生成，都确保「存储安全」这一项在列（防止生成物因存储不可用而卡死）。
    if not any("localStorage" in c or "存储" in c for c in checks):
        checks.append(_STORAGE_SAFETY)
    return checks[:8]


# ---------- 节点（每个节点边界 = 一个 checkpoint 点） ----------


async def _normalize(state: ForgeState, config) -> dict:
    if state.get("current_html") is not None:
        return {}  # 迭代修改：跳过需求规范化（守住已有风格）
    ctx = config["configurable"]["ctx"]
    emit = config["configurable"].get("emit")
    if emit:
        await emit("verify", {"text": "正在规范化需求…", "score": 0.0, "passed": False})
    requirement = await normalize_requirement(ctx, state.get("requirement", ""), model=state.get("model"))
    return {"requirement": requirement}


async def _plan_checks(state: ForgeState, config) -> dict:
    ctx = config["configurable"]["ctx"]
    emit = config["configurable"].get("emit")
    if emit:
        await emit("verify", {"text": "正在分析需求、制定核验清单…", "score": 0.0, "passed": False})
    checks = await plan_checks(ctx, state.get("requirement", ""))
    return {"checks": checks}


async def _generate(state: ForgeState, config) -> dict:
    ctx = config["configurable"]["ctx"]
    emit = config["configurable"].get("emit")
    missing = state.get("missing") or []
    attempts = state.get("attempts", 0)
    focus = f"\n特别补齐此前未通过的核验项：{'；'.join(missing[:4])}" if missing else ""
    if emit:
        await emit("verify", {
            "text": f"第 {attempts + 1} 轮：正在生成完整应用（模型思考中，约需 1–2 分钟）…" + (f"（补齐：{'、'.join(missing[:3])}）" if missing else ""),
            "score": 0.0, "passed": False, "attempt": attempts + 1,
        })
    out = await generate(ctx, state.get("requirement", "") + focus, state.get("current_html"), model=state.get("model"), provider=state.get("provider", "deepseek"))
    if emit:
        await emit("verify", {"text": f"第 {attempts + 1} 轮生成完成，开始结构校验…", "score": 0.0, "passed": False})
    return {"html": out["html"], "title": out["title"]}


async def _structure_check(state: ForgeState, config) -> dict:
    violations = side_effect_checks(state.get("html", ""))
    emit = config["configurable"].get("emit")
    if emit and violations:
        await emit("verify", {"text": f"结构性校验未过：{'；'.join(violations[:4])}", "score": 0.0, "passed": False})
    return {"missing": violations}


async def _slop_check(state: ForgeState, config) -> dict:
    slop = slop_checks(state.get("html", ""))
    warnings = [f for f in slop if f["severity"] != "P0"]
    p0 = slop_p0_messages(slop)
    emit = config["configurable"].get("emit")
    if emit and p0:
        await emit("verify", {"text": f"反 AI 味检查未过：{'；'.join(p0[:3])}", "score": 0.0, "passed": False})
    return {"slop_warnings": warnings, "missing": p0}


async def _verify(state: ForgeState, config) -> dict:
    ctx = config["configurable"]["ctx"]
    emit = config["configurable"].get("emit")
    v = await verify_html(ctx, state.get("checks", []), state.get("html", ""))
    # 核验模型不可用（degraded）≠ 通过：结构/反 AI 味已过，但跨模型核验没执行，
    # 直接带警示返回，别把重试额度浪费在「换模型也无法修复的核验故障」上。
    if v.get("degraded"):
        if emit:
            await emit("verify", {"text": "跨模型核验当前不可用，本次未执行核验（结构校验与反 AI 味检查已通过）", "score": 0.0, "passed": False, "missing": v["missing"], "degraded": True})
        return {"verify": v, "missing": v["missing"]}
    if emit:
        await emit("verify", {
            "text": f"核验分 {v['score']:.2f}" + (f"，缺失：{'；'.join(v['missing'][:3])}" if v["missing"] else "，全部通过"),
            "score": v["score"], "passed": v["score"] >= VERIFY_PASS_SCORE, "missing": v["missing"],
        })
    return {"verify": v, "missing": v["missing"]}


async def _retry(state: ForgeState, config) -> dict:
    return {"attempts": state.get("attempts", 0) + 1}


async def _finish(state: ForgeState, config) -> dict:
    emit = config["configurable"].get("emit")
    v = state.get("verify", {})
    if emit and not v.get("degraded") and v.get("score", 0.0) < VERIFY_PASS_SCORE:
        missing = state.get("missing") or []
        await emit("verify", {"text": f"重试 {REACT_MAX_RETRY} 次后核验分仍低于 {VERIFY_PASS_SCORE}，返回当前结果并注明未通过", "score": v.get("score", 0.0), "passed": False, "missing": missing})
    return {}


# ---------- 条件边路由 ----------


def _route_structure(state: ForgeState) -> str:
    if state.get("missing"):
        return "retry" if state.get("attempts", 0) < REACT_MAX_RETRY else "end"
    return "slop_check"


def _route_slop(state: ForgeState) -> str:
    if state.get("missing"):
        return "retry" if state.get("attempts", 0) < REACT_MAX_RETRY else "end"
    return "verify"


def _route_verify(state: ForgeState) -> str:
    v = state.get("verify", {})
    if v.get("degraded") or v.get("score", 0.0) >= VERIFY_PASS_SCORE:
        return "end"
    return "retry" if state.get("attempts", 0) < REACT_MAX_RETRY else "end"


def _build_graph() -> StateGraph:
    g = StateGraph(ForgeState)
    g.add_node("normalize", _normalize)
    g.add_node("plan_checks", _plan_checks)
    g.add_node("generate", _generate)
    g.add_node("structure_check", _structure_check)
    g.add_node("slop_check", _slop_check)
    g.add_node("verify", _verify)
    g.add_node("retry", _retry)
    g.add_node("finish", _finish)

    g.add_edge(START, "normalize")
    g.add_edge("normalize", "plan_checks")
    g.add_edge("plan_checks", "generate")
    g.add_edge("generate", "structure_check")
    g.add_conditional_edges("structure_check", _route_structure, {"slop_check": "slop_check", "retry": "retry", "end": "finish"})
    g.add_conditional_edges("slop_check", _route_slop, {"verify": "verify", "retry": "retry", "end": "finish"})
    g.add_conditional_edges("verify", _route_verify, {"retry": "retry", "end": "finish"})
    g.add_edge("retry", "generate")
    g.add_edge("finish", END)
    return g


# ---------- 编译 + checkpoint 惰性初始化（单进程复用同一连接池/图） ----------

_graph = None
_checkpointer = None
_init_lock = asyncio.Lock()


def _checkpoint_dsn() -> str:
    """Resolve the psycopg DSN for the LangGraph Postgres checkpointer.

    LangGraph 的 Postgres saver 用 psycopg3，不理解 SQLAlchemy 的 ``+asyncpg`` 驱动后缀，
    所以优先读专用 ``LANGGRAPH_CHECKPOINT_DSN``，否则从 ``DATABASE_URL`` 剥掉异步驱动前缀推导。
    与主库同库（不另起一个数据库），checkpoint 表名独立，互不干扰。
    """
    dsn = os.environ.get("LANGGRAPH_CHECKPOINT_DSN", "").strip()
    if not dsn:
        dsn = re.sub(
            r"^(postgres|postgresql)\+[a-z0-9]+://",
            r"postgresql://",
            os.environ.get("DATABASE_URL", "").strip(),
        )
    if not dsn.startswith(("postgresql://", "postgres://")):
        raise RuntimeError(
            "LangGraph checkpoint 需要 PostgreSQL：请设置 LANGGRAPH_CHECKPOINT_DSN（psycopg DSN，如 postgresql://user:pass@host:5432/db）"
        )
    return dsn


async def _get_graph():
    global _graph, _checkpointer
    if _graph is not None:
        return _graph
    async with _init_lock:
        if _graph is not None:
            return _graph
        # 懒导入：psycopg/checkpoint-postgres 只在真正初始化 checkpoint 时加载，
        # 模块级 import 保持轻量（测试直接 import forge_react 无需装这些包）。
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        # 与 AsyncPostgresSaver.from_conn_string 相同的连接选项（autocommit 让 .setup()
        # 的建表 DDL 立即提交；prepare_threshold=0 禁用服务端预编译，兼容 PgBouncer）。
        pool = AsyncConnectionPool(
            _checkpoint_dsn(),
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            open=False,
        )
        await pool.open()
        _checkpointer = AsyncPostgresSaver(pool)
        await _checkpointer.setup()
        _graph = _build_graph().compile(checkpointer=_checkpointer)
        return _graph


async def _noop_emit(_event_type: str, _data: dict) -> None:
    """默认无事件消费者时的空实现（async，避免 `await` 到同步 lambda 报错）。"""
    return None


async def generate_verified(
    ctx: RunContext,
    requirement: str,
    current_html: str | None = None,
    *,
    model: str | None = None,
    emit=None,
    provider: str = "deepseek",
) -> dict:
    """ReAct 单次生成 + 三校核验（LangGraph 图 + checkpoint）。

    ``thread_id = ctx.run_key``：进程崩溃后同 run_key 重新调用，从最近 checkpoint 续跑，
    跳过已完成节点。返回 ``{"html", "title", "verify", "slop"}``。
    """
    emit = emit or _noop_emit
    graph = await _get_graph()
    config = {"configurable": {"thread_id": ctx.run_key, "ctx": ctx, "emit": emit}}

    initial: ForgeState = {
        "requirement": requirement,
        "current_html": current_html,
        "checks": [],
        "html": "",
        "title": "",
        "missing": [],
        "attempts": 0,
        "slop_warnings": [],
        "verify": {"score": 0.0, "checked": {}, "missing": []},
        "model": model,
        "provider": provider,
    }

    # 断点续跑：该 thread_id 已有 checkpoint（上次崩溃遗留）→ 传 None 从最近 checkpoint 续跑、
    # 跳过已完成节点；否则传 initial 全新开始。若传 initial，LangGraph 会把它当作新输入
    # 覆盖 state 并从头重跑，丢掉 checkpoint 的恢复价值。
    existing = await _checkpointer.aget_tuple(config)
    result = await graph.ainvoke(None if existing else initial, config)
    v = result.get("verify") or {"score": 0.0, "checked": {}, "missing": []}
    verify_failed = (not v.get("degraded")) and v.get("score", 0.0) < VERIFY_PASS_SCORE
    return {
        "html": result.get("html", ""),
        "title": result.get("title", ""),
        "verify": v,
        "slop": result.get("slop_warnings", []),
        "verify_failed": verify_failed,
    }
