"""ReAct 图（LangGraph StateGraph）的 checkpoint 断点续跑集成测试。

不调真实 LLM / DB：mock 掉 normalize_requirement / plan_checks / generate / verify_html，
用临时 SQLite 文件做 checkpoint，验证「图跑一半崩溃 → 同 thread_id 续跑 → 跳过已完成节点」。
"""

import asyncio

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from services.forge import forge_react
from services.forge.gateway import RunContext

# 一个能同时通过 side_effect_checks 与 slop_checks P0 的合法单文件 HTML（内联 + localStorage try/catch + 无 AI 味）。
VALID_HTML = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>待办</title>
<style>:root{--bg:#111;--fg:#eee;--surface:#1a1a1a;--muted:#888;--border:#333;--accent:#d24b1f}</style>
</head>
<body>
<header><h1>待办</h1></header>
<main>
<ul id="list"></ul>
<input id="inp" aria-label="新任务"><button id="add">添加</button>
</main>
<script>
(function(){
  var store={load:function(){try{return JSON.parse(localStorage.getItem('todos')||'[]')}catch(e){return []}},save:function(v){try{localStorage.setItem('todos',JSON.stringify(v))}catch(e){}}};
  var list=document.getElementById('list'),inp=document.getElementById('inp');
  function render(){var v=store.load();list.innerHTML='';v.forEach(function(t){var li=document.createElement('li');li.textContent=t;list.appendChild(li)});}
  document.getElementById('add').addEventListener('click',function(){var v=store.load();v.push(inp.value);store.save(v);render();inp.value=''});
  render();
})();
</script>
</body>
</html>"""


def _initial(requirement="做一个待办应用"):
    return {
        "requirement": requirement,
        "current_html": None,
        "checks": [],
        "html": "",
        "title": "",
        "missing": [],
        "attempts": 0,
        "slop_warnings": [],
        "verify": {"score": 0.0, "checked": {}, "missing": []},
        "model": None,
        "provider": "deepseek",
    }


def _config(thread_id, ctx):
    return {"configurable": {"thread_id": thread_id, "ctx": ctx}}


def _patch(monkeypatch, calls, fail_first_generate=False):
    """把图里所有 LLM 调用替换成计数器 mock，返回合法产出。"""
    state = {"first_generate": True}

    async def fake_normalize(ctx, requirement, *, model=None):
        calls["normalize"] += 1
        return requirement + "（规范化）"

    async def fake_plan(ctx, requirement):
        calls["plan"] += 1
        return ["是完整可运行的 HTML 页面"]

    async def fake_generate(ctx, requirement, current_html=None, *, model=None, provider="deepseek"):
        calls["generate"] += 1
        if fail_first_generate and state["first_generate"]:
            state["first_generate"] = False
            raise RuntimeError("模拟生成途中崩溃")
        return {"html": VALID_HTML, "title": "待办"}

    async def fake_verify(ctx, checks, html):
        calls["verify"] += 1
        return {"checked": {c: True for c in checks}, "missing": [], "score": 1.0}

    monkeypatch.setattr(forge_react, "normalize_requirement", fake_normalize)
    monkeypatch.setattr(forge_react, "plan_checks", fake_plan)
    monkeypatch.setattr(forge_react, "generate", fake_generate)
    monkeypatch.setattr(forge_react, "verify_html", fake_verify)


def test_graph_runs_to_completion(tmp_path, monkeypatch):
    """正常跑通：各节点恰好调用一次，产出 html/title + 核验通过。"""
    calls = {"normalize": 0, "plan": 0, "generate": 0, "verify": 0}
    _patch(monkeypatch, calls)

    async def run():
        db = tmp_path / "cp.sqlite"
        async with AsyncSqliteSaver.from_conn_string(str(db)) as cp:
            graph = forge_react._build_graph().compile(checkpointer=cp)
            ctx = RunContext(run_key="run-1")
            return await graph.ainvoke(_initial(), _config("run-1", ctx))

    result = asyncio.run(run())
    assert result["title"] == "待办"
    assert result["html"] == VALID_HTML
    assert result["verify"]["score"] == 1.0
    assert calls == {"normalize": 1, "plan": 1, "generate": 1, "verify": 1}


def test_graph_resumes_from_checkpoint(tmp_path, monkeypatch):
    """崩溃续跑：generate 第一次抛异常，第二次同 thread_id 续跑，跳过已完成的 normalize/plan_checks。"""
    calls = {"normalize": 0, "plan": 0, "generate": 0, "verify": 0}
    _patch(monkeypatch, calls, fail_first_generate=True)

    async def run():
        db = tmp_path / "cp.sqlite"
        async with AsyncSqliteSaver.from_conn_string(str(db)) as cp:
            graph = forge_react._build_graph().compile(checkpointer=cp)
            ctx = RunContext(run_key="run-2")
            config = _config("run-2", ctx)

            # 第一次：generate 节点崩溃
            with pytest.raises(Exception, match="崩溃"):
                await graph.ainvoke(_initial(), config)

            # 第二次：同 thread_id 续跑，从最近 checkpoint 恢复（传 None，state 已在 checkpoint 里）
            return await graph.ainvoke(None, config)

    result = asyncio.run(run())
    assert result["title"] == "待办"
    assert result["verify"]["score"] == 1.0
    # 关键：normalize/plan_checks 只跑一次（续跑跳过），generate 跑两次（崩溃一次 + 续跑一次）
    assert calls["normalize"] == 1
    assert calls["plan"] == 1
    assert calls["generate"] == 2
    assert calls["verify"] == 1


def test_graph_concurrent_runs_share_checkpointer(tmp_path, monkeypatch):
    """同一 checkpointer 上并发跑两个 thread_id（赛马 2 路并行的形态），两者都完成且互不串状态。"""
    calls = {"normalize": 0, "plan": 0, "generate": 0, "verify": 0}
    _patch(monkeypatch, calls)

    async def run():
        db = tmp_path / "cp.sqlite"
        async with AsyncSqliteSaver.from_conn_string(str(db)) as cp:
            graph = forge_react._build_graph().compile(checkpointer=cp)

            async def one(tid):
                ctx = RunContext(run_key=tid)
                return await graph.ainvoke(_initial(f"需求 {tid}"), _config(tid, ctx))

            return await asyncio.gather(one("run-a"), one("run-b"))

    r1, r2 = asyncio.run(run())
    assert r1["title"] == "待办"
    assert r2["title"] == "待办"
    assert r1["html"] == VALID_HTML and r2["html"] == VALID_HTML
    # 两条线程各自跑完 normalize/plan/generate/verify 各一次，互不串 state
    assert calls == {"normalize": 2, "plan": 2, "generate": 2, "verify": 2}
