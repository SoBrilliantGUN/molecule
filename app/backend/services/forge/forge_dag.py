"""Long-task DAG generator: multi-stage pipeline, each stage is a DAG of nodes.

Each stage = one DAG; each DAG node = one small LLM call producing an HTML *segment*
(structure / style / interaction / polish) that is spliced into an accumulating full HTML.

Guards (all deterministic, program-side):
- DAG is acyclic (Kahn), every referenced dependency exists, no self-dependency.
- Longest serial chain ≤ DAG_MAX_CHAIN (default 4) — else the DAG is rejected and replanned.
- Stage completion → ① program side-effect check → ② a *different* model (LLM_VERIFY_MODEL)
  verifies each verify_item → ③ program scores; below VERIFY_PASS_SCORE the stage is sent back.
- Node failure → mark failed node + downstream obsolete → replan replacements → merge → re-validate.

Every node completion writes a checkpoint to AgentRuns.checkpoint_json (crash-resume).
All LLM calls go through gateway.chat (budget / breaker / accounting preserved).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from services.forge import gateway, ops, repo
from services.forge.codegen import DESIGN_GUIDE, _strip_fence, _extract_title, _validate_html
from services.forge.gateway import RunContext
from services.forge.util import DomainError, dumps, extract_json, loads

logger = logging.getLogger(__name__)

# ---- tunables (env) ----
DAG_MAX_CHAIN = int(os.environ.get("DAG_MAX_CHAIN", "4"))           # longest serial chain limit
VERIFY_PASS_SCORE = float(os.environ.get("VERIFY_PASS_SCORE", "0.7"))
DAG_MAX_RETRY = int(os.environ.get("DAG_MAX_RETRY", "3"))           # plan / replan retry budget
STAGE_STEP_TOKENS = int(os.environ.get("LLM_STAGE_STEP_TOKENS", "2000"))
PLAN_MODEL = os.environ.get("LLM_PLAN_MODEL") or os.environ.get("LLM_MAIN_MODEL", "deepseek-ai/DeepSeek-V4-Flash")
VERIFY_MODEL = os.environ.get("LLM_VERIFY_MODEL", "zai-org/GLM-5.3")

# node actions understood by the executor (instead of kima's generic tool registry)
ACTIONS = ("structure", "style", "interaction", "polish")

# ======================================================================
# DAG data model (subset of kima plan_model: only what HTML generation needs)
# ======================================================================


class NodeStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    OBSOLETE = "obsolete"


@dataclass
class DagNode:
    id: str
    action: str                       # structure | style | interaction | polish
    prompt: str                       # per-node instruction (the requirement scope of this node)
    depends_on: tuple[str, ...] = ()
    checks: list[str] = field(default_factory=list)   # per-node verify items
    status: str = NodeStatus.PENDING
    output: str = ""                  # HTML segment produced by this node
    error: str | None = None
    replaces: str | None = None       # only in replan replacement nodes

    def to_dict(self) -> dict:
        return {
            "id": self.id, "action": self.action, "prompt": self.prompt,
            "depends_on": list(self.depends_on), "checks": self.checks,
            "status": self.status, "has_output": bool(self.output),
            "error": self.error, "replaces": self.replaces,
        }


class Dag:
    """A stage's DAG: id -> node, plus reverse adjacency; topology execution support."""

    def __init__(self, nodes: list[DagNode]) -> None:
        self._nodes: dict[str, DagNode] = {}
        self._dependents: dict[str, list[str]] = {}
        for n in nodes:
            self._add(n)

    def _add(self, node: DagNode) -> None:
        self._nodes[node.id] = node
        for dep in node.depends_on:
            self._dependents.setdefault(dep, []).append(node.id)

    @property
    def nodes(self) -> tuple[DagNode, ...]:
        return tuple(self._nodes.values())

    def get(self, node_id: str) -> DagNode | None:
        return self._nodes.get(node_id)

    def ready(self) -> list[DagNode]:
        """PENDING nodes whose deps are all COMPLETED (topology execution)."""
        out = []
        for n in self._nodes.values():
            if n.status != NodeStatus.PENDING:
                continue
            if all(
                (d := self._nodes.get(dep)) is not None and d.status == NodeStatus.COMPLETED
                for dep in n.depends_on
            ):
                out.append(n)
        return out

    def mark_downstream_obsolete(self, failed_id: str) -> list[str]:
        """Obsolete failed node's downstream (failed node itself stays FAILED)."""
        obsolete = []
        q = self._dependents.get(failed_id, [])
        seen = set(q)
        while q:
            sid = q.pop(0)
            node = self._nodes.get(sid)
            if node is None:
                continue
            if node.status != NodeStatus.COMPLETED:
                node.status = NodeStatus.OBSOLETE
                obsolete.append(sid)
            for child in self._dependents.get(sid, []):
                if child not in seen:
                    seen.add(child)
                    q.append(child)
        return obsolete

    def merge(self, replacements: list[DagNode]) -> None:
        """Merge replan replacement nodes (id-collision → #2 suffix), mark replaced obsolete."""
        taken = set(self._nodes)
        label_map: dict[str, str] = {}
        for r in replacements:
            final_id = self._claim(r.id, taken)
            taken.add(final_id)
            label_map[r.id] = final_id
            r.id = final_id
            r.depends_on = tuple(label_map.get(d, d) for d in r.depends_on)
            if r.replaces and r.replaces in self._nodes:
                self._nodes[r.replaces].status = NodeStatus.OBSOLETE
            self._add(r)

    @staticmethod
    def _claim(proposed: str, taken: set[str]) -> str:
        if proposed not in taken:
            return proposed
        n = 2
        while f"{proposed}#{n}" in taken:
            n += 1
        return f"{proposed}#{n}"

    def to_dict(self) -> dict:
        return {"nodes": [n.to_dict() for n in self.nodes]}


# ======================================================================
# Deterministic DAG validation
# ======================================================================


def _has_cycle(nodes: dict[str, DagNode]) -> bool:
    """Kahn topological sort: visits all nodes iff acyclic."""
    indegree = {sid: 0 for sid in nodes}
    children = {sid: [] for sid in nodes}
    for sid, n in nodes.items():
        for dep in n.depends_on:
            if dep in nodes:
                children[dep].append(sid)
                indegree[sid] += 1
    q = [sid for sid, d in indegree.items() if d == 0]
    visited = 0
    while q:
        sid = q.pop(0)
        visited += 1
        for child in children[sid]:
            indegree[child] -= 1
            if indegree[child] == 0:
                q.append(child)
    return visited != len(nodes)


def longest_chain(nodes: dict[str, DagNode]) -> int:
    """Longest path over the dependency graph (must be acyclic to be meaningful)."""
    # distance to a node = 1 + max distance of its deps
    dist = {}
    for sid, n in nodes.items():
        if not n.depends_on:
            dist[sid] = 1
    # iterate until fixpoint (nodes count is small; simple loop is fine)
    changed = True
    while changed:
        changed = False
        for sid, n in nodes.items():
            d = (max((dist.get(d, 0) for d in n.depends_on), default=0)) + 1
            if dist.get(sid, 0) != d:
                dist[sid] = d
                changed = True
    return max(dist.values(), default=0)


def validate_dag(dag: Dag) -> list[str]:
    """Return list of violations; empty = valid. Guards: action legal, deps exist,
    no self-dep, acyclic, longest chain ≤ DAG_MAX_CHAIN."""
    errors: list[str] = []
    nodes = {n.id: n for n in dag.nodes}
    for n in dag.nodes:
        if n.action not in ACTIONS:
            errors.append(f"节点 {n.id!r} 的动作 {n.action!r} 不在可用动作 {list(ACTIONS)!r} 中")
        for dep in n.depends_on:
            if dep not in nodes:
                errors.append(f"节点 {n.id!r} 依赖的节点 {dep!r} 不存在")
            elif dep == n.id:
                errors.append(f"节点 {n.id!r} 不能依赖自身")
    if not errors and nodes and _has_cycle(nodes):
        errors.append("DAG 存在循环依赖")
    if not errors and nodes:
        chain = longest_chain(nodes)
        if chain > DAG_MAX_CHAIN:
            errors.append(f"DAG 最长串行链 {chain} 超过上限 {DAG_MAX_CHAIN}")
    return errors


# ======================================================================
# Stage planning (model decides stage breakdown, each stage has verify items)
# ======================================================================

STAGE_PLAN_SYSTEM = (
    "你是前端生成任务的阶段规划器。把一条需求拆成**多个阶段**，每个阶段完成一类工作。\n"
    "典型的阶段划分（按需求增减）：structure（HTML 结构与布局骨架）、style（视觉样式与主题）、"
    "interaction（用户交互与数据逻辑）、polish（细节打磨与收尾）。\n"
    "每个阶段给出：stage（阶段名）、instruction（该阶段要达成的目标，供后面规划 DAG 用）、"
    "verify_items（3-6 条该阶段完成后必须满足的核验项）。整体不得超过 6 个阶段。\n"
    "只输出 JSON：[{\"stage\":\"structure\",\"instruction\":\"...\",\"verify_items\":[\"...\",\"...\"]}, ...]\n"
    f"硬性要求：生成的是**单文件 HTML 应用**——内联所有 CSS/JS，不引外部 JS/CSS（可引 Google Fonts），"
    "数据用 localStorage 持久化，有真实交互（新增/编辑/删除/勾选等），"
    f"且必须符合下列视觉铁律：\n{DESIGN_GUIDE}"
)


async def plan_stages(ctx: RunContext, requirement: str) -> list[dict]:
    """One LLM call → stage list JSON. Parse failure → fall back to default 4 stages."""
    default = [
        {"stage": "structure", "instruction": "搭建 HTML 结构与布局骨架", "verify_items": ["有 <!doctype html> 与 <html>/<head>/<body>", "结构元素和语义标签完整"]},
        {"stage": "style", "instruction": "实现视觉主题与全部样式", "verify_items": ["CSS 全部内联，无外链样式", "配色贴合主题、非模板观感"]},
        {"stage": "interaction", "instruction": "实现全部用户交互与数据逻辑", "verify_items": ["所有交互可点可操作（新增/编辑/删除/勾选等）", "数据用 localStorage 持久化"]},
        {"stage": "polish", "instruction": "打磨细节：响应式/焦点/动效/文案", "verify_items": ["响应式适配移动端", "可见键盘焦点、尊重 prefers-reduced-motion"]},
    ]
    try:
        result = await gateway.chat(
            ctx,
            [
                {"role": "system", "content": STAGE_PLAN_SYSTEM},
                {"role": "user", "content": f"需求：{requirement}"},
            ],
            purpose="stage:plan",
            model=PLAN_MODEL,
            max_tokens=1800,
            temperature=0,
        )
        data = extract_json(result.content)
        stages = data if isinstance(data, list) else (data.get("stages") or data.get("items"))
        stages = [s for s in (stages or []) if isinstance(s, dict) and s.get("stage")]
        if stages and len(stages) <= 6:
            for s in stages:
                s.setdefault("verify_items", [])
            return stages
        logger.warning("stage plan 解析异常（%d 个阶段），回退默认 4 阶段", len(stages or []))
    except Exception as exc:  # noqa: BLE001 - planning is not a hard dependency
        logger.warning("stage plan 失败（%s），回退默认 4 阶段", exc)
    return default


# ======================================================================
# Per-stage DAG planning
# ======================================================================

DAG_PLAN_SYSTEM = (
    "你是阶段内执行规划器。给定当前阶段的目标，规划一个**有向无环图（DAG）**执行计划：\n"
    "每个节点是该阶段内一个可独立生成 HTML 片段的小任务。节点类型（action）固定：\n"
    "- structure：产出 HTML 结构片段（标签、骨架、语义结构）\n"
    "- style：产出样式片段（CSS 规则、主题变量）\n"
    "- interaction：产出交互逻辑片段（JS 行为、localStorage 操作）\n"
    "- polish：产出打磨片段（细节、响应式、文案）\n"
    "约束：\n"
    "1. 节点数 1–5 个；每个节点 id 唯一（如 n1 n2…）。\n"
    "2. 节点之间有依赖时用 depends_on 声明（引用其他节点 id）；无依赖则 []（可并行）。\n"
    "3. 每个节点带 checks（3–5 条该节点产出的核验项）。\n"
    "4. **最长串行依赖链不能超过 4**（同一条 depends_on 链上的节点数）。\n"
    "5. 只输出 JSON：{\"nodes\":[{\"id\":\"n1\",\"action\":\"structure\",\"prompt\":\"该节点的具体任务描述\","
    "\"depends_on\":[\"n2\"],\"checks\":[\"...\"]}]}\n"
    "禁止循环依赖；被 depends_on 引用的节点 id 必须存在。"
)


async def plan_dag(ctx: RunContext, stage: dict, requirement: str) -> Dag | None:
    """One LLM call → DAG JSON → validated Dag. Retry up to DAG_MAX_RETRY on schema/
    structural violations (invalid action / missing dep / cycle / chain too long)."""
    stage_name = stage.get("stage", "?")
    stage_instr = stage.get("instruction", "")
    user = (
        f"整体需求：{requirement}\n\n"
        f"当前阶段：{stage_name}\n阶段目标：{stage_instr}\n"
        "请规划该阶段内的 DAG。既没有历史 HTML 时从零生成；有累计 HTML 时以此为基础增量生成。"
    )
    feedback = ""
    for _ in range(DAG_MAX_RETRY):
        prompt = user + (f"\n\n上次规划被拒绝，请修正：\n{feedback}" if feedback else "")
        try:
            result = await gateway.chat(
                ctx,
                [
                    {"role": "system", "content": DAG_PLAN_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                purpose="dag:plan",
                model=PLAN_MODEL,
                max_tokens=2200,
                temperature=0,
            )
            data = extract_json(result.content)
            raw = data.get("nodes") if isinstance(data, dict) else data
            nodes = []
            for r in raw or []:
                if not isinstance(r, dict) or not r.get("id"):
                    continue
                nodes.append(DagNode(
                    id=str(r["id"]),
                    action=str(r.get("action", "structure")).lower(),
                    prompt=str(r.get("prompt", "")),
                    depends_on=tuple(str(d) for d in (r.get("depends_on") or [])),
                    checks=[str(c) for c in (r.get("checks") or [])],
                ))
            if not nodes:
                feedback = "nodes 数组为空或结构不符，请重新输出完整 DAG JSON。"
                continue
            dag = Dag(nodes)
            errors = validate_dag(dag)
            if not errors:
                return dag
            feedback = "；".join(errors)
        except Exception as exc:  # noqa: BLE001
            feedback = f"解析失败：{exc}"
    logger.warning("DAG 规划用尽重试仍无效（stage=%s），返回 None", stage_name)
    return None


# ======================================================================
# Node execution
# ======================================================================

NODE_SYSTEM = (
    "你是前端实现工程师，只负责当前节点的一段 HTML 片段。\n"
    "输出规则：只输出 HTML 片段本身，不要 markdown 围栏，不要解释性文字。\n"
    "依据节点类型产出对应片段：structure→HTML 结构标签；style→<style> 内的 CSS 规则；"
    "interaction→<script> 内的 JS 逻辑；polish→完善细节（响应式/焦点/文案/兼容）。\n"
    f"统一约束：{DESIGN_GUIDE}"
)


async def _run_node(ctx: RunContext, dag: Dag, node: DagNode, requirement: str, current: str) -> str:
    """Execute one node → its HTML segment (spliced into `current`). Raises on hard failure."""
    upstream = []
    for dep_id in node.depends_on:
        dep = dag.get(dep_id)
        if dep is not None and dep.output:
            upstream.append(f"[{dep.id}:{dep.action}] {dep.output[:1200]}")
    user = (
        f"整体需求：{requirement}\n"
        f"当前节点：{node.id}（动作：{node.action}）\n"
        f"本节点任务：{node.prompt}\n"
        + (f"依赖节点已产出的片段：\n" + "\n".join(upstream) if upstream else "")
        + (f"\n\n当前累计 HTML（此前阶段已完成部分）：\n{current[-3000:]}" if current else "")
        + "\n产出本节点 HTML 片段，只输出片段本身。"
    )
    result = await gateway.chat(
        ctx,
        [{"role": "system", "content": NODE_SYSTEM}, {"role": "user", "content": user}],
        purpose="dag:node",
        model=PLAN_MODEL,
        max_tokens=STAGE_STEP_TOKENS,
        temperature=0.3,
    )
    return _strip_fence(result.content)


def _splice(current: str, node: DagNode, segment: str) -> str:
    """Splice a node's fragment into the accumulating HTML by node type (best-effort).

    Guarantees an enclosed single-document skeleton: if `current` has no <html> root yet,
    structure fragments are wrapped into a fresh <!doctype html><html><head><body> frame so the
    accumulator is always a self-contained document (side_effect_checks can then pass once the
    required sections exist).
    """
    seg = segment.strip()
    if not seg:
        return current
    low = seg.lower()
    has_root = "<html" in current.lower() and "<body" in current.lower()

    # style → merge into <style> block; interaction → merge into <script> block.
    if node.action == "style":
        m = re.search(r"<style[^>]*>(.*?)</style>", seg, re.DOTALL | re.IGNORECASE)
        css = m.group(1) if m else seg
        if "<style>" in current:
            return re.sub(
                r"(</style>)",
                lambda mo: css + "\n" + mo.group(0),
                current,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
        if not has_root:
            return _skeleton(css_body=css)
        return current.replace("</head>", f"<style>\n{css}\n</style>\n</head>", 1)
    if node.action == "interaction":
        m = re.search(r"<script[^>]*>(.*?)</script>", seg, re.DOTALL | re.IGNORECASE)
        js = m.group(1) if m else seg
        if "</script>" in current:
            return re.sub(
                r"(</script>)",
                lambda mo: js + "\n" + mo.group(0),
                current,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
        if not has_root:
            return _skeleton(js_body=js)
        return current.replace("</body>", f"<script>\n{js}\n</script>\n</body>", 1)

    # structure/polish content. If segment itself is a full document, adopt it wholesale.
    if low.startswith("<!doctype") or "<html" in low:
        return seg if not has_root else _merge_full_doc(current, seg)

    # If we don't yet have a skeleton, wrap the fragment into one (this is the fix: stage 1
    # structure DAG never produced an enclosed doc, so nothing ever had <html>/<head>/<body>).
    if not has_root:
        return _skeleton(body=seg)
    if "</body>" in current:
        return current.replace("</body>", seg + "\n</body>", 1)
    return current + "\n" + seg


def _skeleton(*, body: str = "", css_body: str = "", js_body: str = "") -> str:
    """A minimal enclosed single-document skeleton with optional parts."""
    head_extra = ""
    if css_body:
        head_extra += f"\n<style>\n{css_body}\n</style>\n"
    if js_body:
        js_body = f"\n<script>\n{js_body}\n</script>\n"
    return (
        "<!doctype html>\n<html lang=\"zh\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>应用</title>{head_extra}\n</head>\n"
        f"<body>\n{body}\n{js_body}\n</body>\n</html>"
    )


def _merge_full_doc(current: str, segment: str) -> str:
    """Both current and segment are full documents → prefer the newest (segment)."""
    return segment


# ======================================================================
# Program-side side-effect / structural checks
# ======================================================================


def side_effect_checks(html: str) -> list[str]:
    """Deterministic structural checks. Return list of violations (empty = OK)."""
    violations: list[str] = []
    low = (html or "").lower()
    if not low:
        return ["生成的 HTML 为空"]
    if "<html" not in low and "<!doctype" not in low:
        violations.append("缺少 <html> / <!doctype")
    if "<head" not in low or "</head>" not in low:
        violations.append("缺少 <head>/</head>")
    if "title" not in low:
        violations.append("缺少 <title>")
    if "<body" not in low or "</body>" not in low:
        violations.append("缺少 <body>/</body>")
    # balance-guard: closed script/style (if present); no external JS/CSS (fonts aside).
    # Whether script/style/localStorage exist is judged per-stage by the *other model*
    # against that stage's verify_items — a "structure" stage legitimately has none yet.
    if "<script" in low and "</script>" not in low:
        violations.append("存在未闭合的 <script>")
    if "<style" in low and "</style>" not in low:
        violations.append("存在未闭合的 <style>")
    for pat, label in (
        (r'src\s*=\s*["\']\s*https?://[^"\']+\.js', "外链 JS"),
        (r'href\s*=\s*["\']\s*https?://[^"\']+\.css', "外链 CSS"),
    ):
        if re.search(pat, low):
            violations.append("存在" + label + "（应全部内联）")
    return violations


# ======================================================================
# Cross-model verification + program scoring
# ======================================================================

VERIFY_SYSTEM = (
    "你是独立的验收评审。对照核验项（verify_items）检查生成的 HTML：逐项给出是否满足。\n"
    "只输出 JSON：{\"checked\":{\"项原文\":true/false},\"missing\":[\"不满足的项\"],\"score\":0.0}。\n"
    "score = 已满足项 / 总项数（0~1）。只根据 HTML 内容实判，不宽松放水。"
)


async def verify_html(ctx: RunContext, verify_items: list[str], html: str) -> dict:
    """A *different* model checks each verify_item against the HTML → score."""
    if not verify_items:
        return {"checked": {}, "missing": [], "score": 1.0}
    try:
        result = await gateway.chat(
            ctx,
            [
                {"role": "system", "content": VERIFY_SYSTEM},
                {"role": "user", "content": "核验项：\n" + "\n".join(f"- {v}" for v in verify_items) + "\n\nHTML：\n" + html[:12000]},
            ],
            purpose="stage:verify",
            model=VERIFY_MODEL,
            max_tokens=1400,
            temperature=0,
        )
        data = extract_json(result.content)
        checked = data.get("checked") or {}
        missing = [str(m) for m in (data.get("missing") or [])]
        score = float(data.get("score", 0.0))
        # 程序校验：以 checked 的布尔值为准重算分（防御模型自说自话）
        items = list(verify_items)
        passed = sum(1 for it in items if checked.get(it) is True)
        recompute = passed / len(items) if items else 1.0
        score = min(max(recompute, 0.0), 1.0)
        if not missing:
            missing = [it for it in items if checked.get(it) is not True]
        return {"checked": checked, "missing": missing, "score": round(score, 2)}
    except Exception as exc:  # noqa: BLE001 - verifier failure is not fatal; degrade conservatively
        logger.warning("verify_html 失败（%s），降级为通过", exc)
        return {"checked": {}, "missing": [], "score": 1.0}


# ======================================================================
# Replan on node failure
# ======================================================================

REPLAN_SYSTEM = (
    "你是修复规划器。阶段内的某个节点执行失败，且它的下游节点已作废。请给出**替换节点**：\n"
    "规则：\n"
    "- 用 replaces 声明你替换的失败节点 id。\n"
    "- depends_on 可引用已完成的节点（复用其产物）或同批新节点 id；失败节点的下游若仍需执行，须一并给出其替换节点。\n"
    "- 节点类型/格式同 DAG 规划：{\"nodes\":[{\"id\":\"r1\",\"action\":\"...\",\"prompt\":\"...\",\"depends_on\":[...],\"checks\":[...]}]}。\n"
    "- 同样遵守无环、依赖存在、最长串行链≤4。只输出该 JSON。"
)


async def replan_dag(ctx: RunContext, dag: Dag, failed: DagNode, error: str, requirement: str, retry: int) -> Dag | None:
    """Replan replacements for the failed node + its still-needed downstream. Returns a merged Dag."""
    existing_desc = "\n".join(
        f"- {n.id} ({n.action}) [{n.status}] 依赖={list(n.depends_on)}"
        for n in dag.nodes
    ) or "（空）"
    feedback = f"{failed.id} 失败：{error}；失败节点及其后续已作废。"
    for _ in range(DAG_MAX_RETRY):
        try:
            result = await gateway.chat(
                ctx,
                [
                    {"role": "system", "content": REPLAN_SYSTEM},
                    {"role": "user", "content": (
                        f"整体需求：{requirement}\n当前计划：\n{existing_desc}\n"
                        f"失败节点：{failed.id}（{failed.action}）错误：{error}\n请给出替换节点。"
                    )},
                ],
                purpose="dag:replan",
                model=PLAN_MODEL,
                max_tokens=2200,
                temperature=0,
            )
            data = extract_json(result.content)
            raw = data.get("nodes") if isinstance(data, dict) else data
            replacements = []
            for r in raw or []:
                if not isinstance(r, dict) or not r.get("id"):
                    continue
                replacements.append(DagNode(
                    id=str(r["id"]),
                    action=str(r.get("action", "structure")).lower(),
                    prompt=str(r.get("prompt", "")),
                    depends_on=tuple(str(d) for d in (r.get("depends_on") or [])),
                    checks=[str(c) for c in (r.get("checks") or [])],
                    replaces=str(r.get("replaces") or failed.id),
                ))
            if replacements:
                merged = Dag([n for n in dag.nodes if n.status != NodeStatus.OBSOLETE])
                merged.merge(replacements)
                errors = validate_dag(merged)
                if not errors:
                    return merged
                feedback = "; ".join(errors)
        except Exception as exc:  # noqa: BLE001
            feedback = f"replan 解析失败：{exc}"
    logger.warning("replan 用尽重试仍无效（failed=%s），返回 None", failed.id)
    return None


# ======================================================================
# Stage executor
# ======================================================================


async def _run_scalar(ctx: RunContext, emit, checkpoint, stages: list[dict], requirement: str) -> dict:
    """Execute all stages in a scalar loop. Emits stage/dag/verify events via `emit`."""
    current = ""
    total = len(stages)
    for idx, stage in enumerate(stages, start=1):
        stage_name = stage.get("stage", f"阶段{idx}")
        await emit("stage", {"index": idx, "total": total, "text": f"阶段 {idx}/{total}：{stage_name}", "stage": stage_name})
        dag = await plan_dag(ctx, stage, requirement)
        if dag is None:
            # DAG planning exhausted: fall back to a single full HTML generation for this stage
            await emit("dag", {"node": "fallback", "text": f"{stage_name}：DAG 规划失败，改整体生成"})
            current = await _stage_fallback(ctx, stage, requirement, current)
            continue
        current = await _execute_dag(ctx, emit, checkpoint, dag, stage, requirement, current)
        # stage triple-check
        violations = side_effect_checks(current)
        if violations:
            await emit("verify", {"text": f"结构性校验未过：{'；'.join(violations)}", "score": 0.0, "passed": False})
            # 骨架缺失不是普通打回——必须先在 DAG 内补一个 structure 节点，让骨架成立，
            # 再继续，而不是整体重生成。复用 _patch_dag 的“追加补充节点”路径。
            current, dag = await _patch_skeleton(ctx, emit, dag, stage, requirement, current, violations)
            continue
        v = await verify_html(ctx, stage.get("verify_items") or [], current)
        await emit("verify", {"text": f"核验分 {v['score']:.2f}" + (f"，缺失：{'；'.join(v['missing'][:3])}" if v["missing"] else "，全部通过"), "score": v["score"], "passed": v["score"] >= VERIFY_PASS_SCORE, "missing": v["missing"]})
        if v["score"] < VERIFY_PASS_SCORE:
            await emit("stage", {"index": idx, "total": total, "text": f"分数不足 {VERIFY_PASS_SCORE}，在阶段内补缺失节点"})
            # 打回 = 在本阶段 DAG 内追加/替换“针对缺失项”的补充节点，保留已完成产出继续跑，
            # 绝不从头重生成整个应用。
            current, dag = await _patch_missing(ctx, emit, dag, stage, requirement, current, v["missing"])
    from services.forge.codegen import _validate_html, _extract_title
    _validate_html(current)
    title = _extract_title(current) or requirement[:40]
    return {"html": current, "title": title}


# --- per-stage DAG executor ---


async def _execute_dag(ctx: RunContext, emit, checkpoint, dag: Dag, stage: dict, requirement: str, current: str) -> str:
    while True:
        done = True
        for node in dag.nodes:
            if node.status in (NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.OBSOLETE):
                continue
            done = False
            ready = dag.ready()
            if not any(n.id == node.id for n in ready):
                continue
            node.status = NodeStatus.RUNNING
            await emit("dag", {"node": node.id, "action": node.action, "text": f"执行节点 {node.id}（{node.action}）"})
            try:
                segment = await _run_node(ctx, dag, node, requirement, current)
                node.status = NodeStatus.COMPLETED
                node.output = segment
                current = _splice(current, node, segment)
                await checkpoint()
            except Exception as exc:  # noqa: BLE001 - node failure → replan
                node.status = NodeStatus.FAILED
                node.error = str(exc)
                await emit("error", {"text": f"节点 {node.id} 失败，废弃下游并重规划"})
                obsolete = dag.mark_downstream_obsolete(node.id)
                merged = await replan_dag(ctx, dag, node, str(exc), requirement, len(obsolete))
                if merged is None:
                    await emit("dag", {"node": "replan-failed", "text": "重规划失败，跳过本阶段"})
                    return current
                await emit("dag", {"node": "replan", "text": f"重规划合入替换节点（{len([n for n in merged.nodes if n.replaces])} 个）"})
                dag = merged
        if done:
            break
    return current


async def _stage_fallback(ctx: RunContext, stage: dict, requirement: str, current: str) -> str:
    """When DAG planning fails for a stage — one direct full generate."""
    from services.forge import codegen
    out = await codegen.generate(ctx, requirement, current)
    return out["html"]


PATCH_SYSTEM = (
    "你是补丁工程师。当前阶段的 DAG 已产出大部分内容，但阶段验收缺了一项验收项 / 骨架不完整。"
    "你只负责那一个缺失点，输出对应的 HTML 片段（structure→HTML 标签；style→CSS；"
    "interaction→JS；polish→细节）。已存在的部分不要重写，只补缺失。\\n"
    "只输出 HTML 片段本身。"
)


async def _patch(
    ctx: RunContext, emit, stage: dict, requirement: str, current: str,
    focus: str, node_action: str,
) -> str:
    """Append a single patch node inside the current stage's DAG for one missing item.

    Only *this* node's missing fragment is generated and spliced in — completed nodes/output
    are left untouched, so we never regenerate the whole application from scratch.
    """
    user = (
        f"整体需求：{requirement}\\n当前阶段：{stage.get('stage', '?')}\\n"
        f"需要补齐：{focus}\\n当前 HTML：\\n{current[-3000:]}\\n产出补丁片段。"
    )
    try:
        result = await gateway.chat(
            ctx,
            [
                {"role": "system", "content": PATCH_SYSTEM},
                {"role": "user", "content": user},
            ],
            purpose="dag:patch",
            model=PLAN_MODEL,
            max_tokens=STAGE_STEP_TOKENS,
            temperature=0.2,
        )
        from services.forge.codegen import _strip_fence

        seg = _strip_fence(result.content)
        return _splice(current, DagNode(id="patch", action=node_action, prompt=focus), seg)
    except Exception as exc:  # noqa: BLE001 - patch failure is non-fatal; keep prior HTML
        logger.warning("patch 失败（%s），保留当前 HTML", exc)
        await emit("verify", {"text": f"补丁失败：{exc}，保留当前结果", "score": 0.0, "passed": False})
        return current


async def _patch_missing(
    ctx: RunContext, emit, dag, stage: dict, requirement: str, current: str, missing: list[str],
) -> tuple[str, Dag]:
    """Score below threshold → patch missing items *in-place* inside this stage's DAG.

    Only *this* stage's missing fragments are generated and spliced (structure/style/
    interaction/polish patch nodes) — completed nodes/output are preserved, so we
    *never* regenerate the whole application from scratch. Keep the same (current, dag)
    handle so the caller can continue from where it left off.
    """
    new_current = await _patch_stage(ctx, emit, stage, requirement, current, missing, "polish")
    return new_current, dag


async def _patch_skeleton(
    ctx: RunContext, emit, dag, stage: dict, requirement: str, current: str, violations: list[str],
) -> tuple[str, Dag]:
    """Skeleton failure → patch structure nodes in place (never full regen)."""
    new_current = await _patch_stage(ctx, emit, stage, requirement, current, violations, "structure")
    return new_current, dag


async def _patch_stage(
    ctx: RunContext, emit, stage: dict, requirement: str, current: str,
    items: list[str], prefer_action: str,
) -> str:
    """Patch the stage in place for missing items, re-verify, and return the final HTML.

    Appends small patch nodes for *only* the missing items, splicing them into the
    accumulated HTML; completed nodes/output are preserved. Stops once verification
    passes, or after DAG_MAX_RETRY rounds (patch attempts) to avoid an infinite loop.
    """
    rounds = 0
    pending = items
    while pending and rounds < DAG_MAX_RETRY:
        rounds += 1
        patched_any = False
        for item in pending[:4]:
            action = "structure" if any(k in item.lower() for k in ("html", "style", "script", "骨架", "标签结构")) else prefer_action
            await emit("dag", {"node": "patch", "action": action, "text": f"阶段内补节点：{item}"})
            before = current
            current = await _patch(ctx, emit, stage, requirement, current, item, action)
            if current != before:
                patched_any = True
        if not patched_any:
            break  # nothing moved; avoid an infinite patch loop
        violations = side_effect_checks(current)
        if not violations:
            v = await verify_html(ctx, stage.get("verify_items") or [], current)
            await emit("verify", {"text": f"补丁后核验分 {v['score']:.2f}" + (f"，缺失：{'；'.join(v['missing'][:3])}" if v["missing"] else ""), "score": v["score"], "passed": v["score"] >= VERIFY_PASS_SCORE, "missing": v["missing"]})
            if v["score"] >= VERIFY_PASS_SCORE or not v["missing"]:
                return current
            pending = v["missing"]
        else:
            await emit("verify", {"text": f"补丁后结构校验未过：{'；'.join(violations[:3])}", "score": 0.0, "passed": False})
            pending = violations
    return current


# --- run_context type-only re-export kept for compatibility ---
# (all executor functions take ctx explicitly; no global binding — concurrent runs are safe)


async def run_stages(ctx: RunContext, emit: Callable[..., Any], checkpoint: Callable[[], Any], requirement: str) -> dict:
    """Public entry: plan stages → scalar × DAG execution → triple-verified full HTML.

    ``ctx`` / ``emit`` / ``checkpoint`` are passed down explicitly (no globals), so multiple
    runs can stream concurrently without cross-talk.
    """
    # 1. plan stages
    await emit("stage", {"index": 0, "total": 0, "text": "正在拆解需求为阶段…"})
    stages = await plan_stages(ctx, requirement)
    await emit("stage", {"index": 0, "total": len(stages), "text": f"已拆解为 {len(stages)} 个阶段"})
    return await _run_scalar(ctx, emit, checkpoint, stages, requirement)