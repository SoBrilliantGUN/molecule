# Atoms Forge 开发计划：DAG 分阶段生成 + ReAct 校验 + SSE 实时 + 六层上下文

> 进度状态：**主干已实现并通过关键验证，剩余"长任务完整落库确认 + 并行优化 + 六层上下文实施"**。
> 上次会话退出时：短任务链路完整验证通过（done + 落库），长任务主链路逐节点工作已验证（见末节「进度」）。

---

## 一、核心问题

原始实现 `codegen.generate` 一次 LLM 调用产出完整单文件 HTML（`max_tokens=8000`），DeepSeek 生成慢，
240s/600s 仍不返回被 `asyncio.wait_for` 掐掉 → 任务失败、`Projects` 不落库、前端 502/504。
超时调大治标不治本，且一次长生成用户体验差。

## 二、目标架构（用户确认的决策）

- **长任务 = 多个阶段**；**每个阶段 = 一个 DAG**；DAG 有多个节点（节点 = 该阶段内分步 LLM 调用）。
- DAG 产出后程序校验：**无环、最长串行链 ≤4、依赖节点都存在**。
- 节点执行失败 → **废弃失败节点和后续节点 → 生成新节点合入 → 再校验结构**（绝不从头重来）。
- **每个节点带核验项（checks）**。
- **阶段完成 → 程序副作用校验（骨架）→ 换另一个模型逐项核验 → 程序算分 → 分低打回**（阶段内补节点）。
- **短任务（ReAct）**：单次生成 + 同款三重校验，分低重生成（≤2 次）。
- **复杂度路由**：每次消息先分类。新建（build）→ 长任务；有项目小改 → 短任务。
- **一切 LLM 交互非同步、SSE 实时可见**。
- 参考 `D:\Code\kima` 的实现结构（只借结构，不搬领域代码）。

**避坑指南合规**：不违反三条铁律（可开/真交互/持久化）；DAG 拆阶段 + 实时进度 + 校验闭环正是
"任务拆解 / 复杂度控制 / 工程思维" 评分加分项。额度纪律要求 L5 history 必须压缩（见六层上下文）。

## 三、已实现（对应文件）

### 后端

| 文件 | 作用 |
|---|---|
| `app/backend/services/forge/router.py` | 复杂度路由 `classify()`：新建→long，有项目→short（模型判复杂度） |
| `app/backend/services/forge/forge_dag.py` | 长任务：阶段拆解→每阶段一个 DAG（多节点）→程序校验（无环/依赖/最长链≤4）→逐节点执行→失败废弃下游+replan合入→阶段三重校验，分低阶段内补节点 |
| `app/backend/services/forge/forge_react.py` | 短任务：单次生成 + 程序骨架校验 + 跨模型核验 + 算分打回（≤2 次重生成） |
| `app/backend/services/forge/run_manager.py` | 后台 RunManager：事件持久化（`AgentRuns.events_json`）+ SSR 订阅尾随 |
| `app/backend/services/forge/gateway.py` | 唯一 LLM 出口；默认超时 120→**600s**（DeepSeek 长生成不再被掐断重试） |
| `app/backend/models/agent_runs.py` | 加 `events_json`（SSE 事件持久化回放） |
| `app/backend/alembic/versions/0002_agent_runs_events.py` | migration：加 `events_json` 列 |
| `app/backend/routers/forge.py` | `POST /api/v1/forge/runs`（立即返回 run_key）+ `GET /runs/{key}/events`（SSE） |
| `app/backend/services/forge/agent.py` | 入口分流：`start_forge` 起后台任务，classify→长/短→persist |

### 前端

| 文件 | 作用 |
|---|---|
| `app/frontend/src/lib/forge.ts` | `startRun`（POST /runs）+ `streamRun`（EventSource 收 stage/dag/verify/done/error） |
| `app/frontend/src/components/ChatPanel.tsx` | 迭代走流式，实时渲染进度事件 |
| `app/frontend/src/pages/Home.tsx` | 新建走流式，实时渲染阶段进度 |
| `app/frontend/nginx.conf` | `proxy_buffering off`（SSE 不被缓冲）+ 超时 900s |

### 已修复的运行时问题（关键坑）

1. **骨架校验误报**：原 `side_effect_checks` 把"缺 script/style/localStorage"当骨架缺失，每个阶段都报错。
   → 改为**只查骨架**（doctype/html/head/title/body 闭合），script/style/localStorage 是否满足交给
   跨模型核验按阶段 verify_items 判断（structure 阶段本就不该有 script/style）。
2. **Qwen3-Coder-480B 在硅基流动 403 禁用**：`verify`/`race` 打分模型不可用。
   → 换成 `zai-org/GLM-5.3`（跨家核验，实测 HTTP 200）。`.env` 与代码默认值均已改。
   （硅基流动可用：`DeepSeek-V4-Flash`、`GLM-5.3`、`Qwen3-VL-8B`；不可用：`Qwen3-Coder-480B`=403、`Qwen3-235B`=400 不存在、`DeepSeek-V3-0324`=400 不存在。）
3. **gateway 默认超时 120s 频繁掐断 DeepSeek**：→ 提到 600s。
4. **失败恢复原则**：节点失败只废弃失败节点+下游（`mark_downstream_obsolete`），replan 合入后继续；
   阶段校验打回在**阶段内补缺失节点**（`_patch_stage`），绝不整体重生成。/ `/replan` 均遵守。

## 四、SSE 事件契约

- `POST /api/v1/forge/runs` body `{message, project_id?, model?}` → `{run_key, project_id}`（立即返回）
- `GET /api/v1/forge/runs/{run_key}/events` → `text/event-stream`
  - `event: stage` `data:{index,total,text,stage}`
  - `event: dag` `data:{node,action,text}`
  - `event: verify` `data:{text,score,passed,missing}`
  - `event: done` `data:{project_id,name}`
  - `event: error` `data:{message}`
  - 心跳 `: ping\n\n`（15s）保活

## 五、验证（部分已完成）

- [x] 短任务：`POST /runs {"message":"把标题改成红色","project_id":1}` → classify=short →
      `/events` 收到 stage+verify(1.00 通过)+done → `Projects.html` 更新（落库 id=1）
- [x] 长任务主链路：`POST /runs {看板需求}` → classify=long → 阶段拆解(4~5) → 每阶段 DAG 多节点执行 →
      结构校验只查骨架后各阶段 verify 1.0 通过；SSE 事件逐条实时到达（每节点 DeepSeek 40~240s）
- [ ] **长任务完整 done 落库确认**（上次会话结束时 dd836c5a 仍在 running；DeepSeek 慢，
      5 阶段×每阶段多节点总耗时 15~30min）——**下次会话第一件事**
- [ ] 节点失败→replan→继续（构造失败场景验证）
- [ ] 断点续跑（中途 kill backend，checkpoint_json 恢复）
- [ ] 校验打回（verify<0.7 → 阶段内补节点 → 通过）
- [ ] 持久化 + iframe 渲染 + 刷新数据仍在

## 六、下一步（待办）

1. **长任务完整落库确认**：查上次 dd836c5a 是否 done + 看板项目落库；重跑一次新长任务到 done。
2. **DAG 并行优化（性能）**：当前 `_execute_dag` 无依赖节点**串行**执行（一次只跑一个 ready），
   kima 用 `get_parallel_ready` + `Send` **并行扇出**同层节点。并行化可把 structure 阶段
   4 个节点从 10+ 分钟压到 ~1 个节点耗时。这是长任务慢的表因。
3. **六层上下文实施**（设计见 `FORGE_DAG_PLAN.md` 第七节 + `.claude/plans/twelve-layer-context-design.md`）：
   **只做 L0/L1/L3/L5**，不做 L2（记忆/RAG）/L4（宪法重放）。L5 用两级压缩控制 token，遵守额度纪律。
4. **ECS 部署同步**：`/atoms2/.env` 的 `LLM_VERIFY_MODEL` 改 `zai-org/GLM-5.3`；同步代码；
   安全组 80 已放行；重新 `docker compose up -d --build`。

## 七、六层上下文设计（L0/L1/L3/L5 做，L2/L4 不做）

参考 kima 六层窗口（L0 system / L1 state / L2 memory / L3 skills / L4 reminder / L5 history）。

- **L0 System（固定，永不压缩）**：`DESIGN_GUIDE` + 单文件 HTML 硬性要求（内联 CSS/JS、localStorage、
  真交互、只输出 HTML）。抽出为单一权威 system 块，由 `gateway.chat` 统一拼入所有调用。
- **L1 State（固定，轻量快照）**：`[STATE]` 块注入——`Budget.snapshot()`（turns/tokens/cost/ratio，
  预算感知，快超限时让模型收尾）+ `RunContext.project_id`/status + 当前阶段/节点进度。
  无额外持久化（Budget 内存、AgentRuns 已有 status）。
- **L3 Skills（固定，独立预算）**：`[SKILLS]` 块——`DESIGN_GUIDE`（视觉铁律）+ 单文件 HTML 约束。
  固定简短，**不做** kima 式按需召回（需 embedding，超范围）。
- **L5 History（唯一弹性层，压缩只打这里）**：迭代链路收集最近 N 轮（`agent.chat` 已取 limit=6）→
  更早 LLM 摘要成 `[HISTORY SUMMARY]`；`current_html` 截断（前 3000 字符 + `…[TRIMMED]`）。
  **不做** kima 五级渐进压缩（对话形态简单），两级够用。
- **不做**：L2 记忆/RAG（无需求+额度）、L4 宪法重放（无宪法）、按需 skill 召回（超范围）。

### 实施点（长任务验证收尾后进行）

1. 新增 `app/backend/services/forge/context.py`：`system()` / `state(snapshot, progress)` /
   `skills_block()` / `format_history(messages, keep=6)` / `trim_html(html, 3000)`。
2. `gateway.py` `chat()` 加 `assemble(messages, *, layers=(L0,L1,L3,L5))` 统一拼层。
3. `forge_dag.py` 各 SYSTEM 常量只写角色职能 + 指向 L0/L3，去掉重复内联 DESIGN_GUIDE。
4. `forge_react.py` `PLAN_CHECKS_SYSTEM` 同理。
5. `agent.chat`（迭代）history 走 `format_history`、`current_html` 走 `trim_html`。
6. `.env.example` 加 `HISTORY_KEEP_ROUNDS=6`、`HTML_TRIM_CHARS=3000`。

### 验证（实施后）

- `docker compose logs` 看请求体：L0/L1/L3 恒定，L5 随轮次增长但被压缩。
- 迭代 5 轮以上，history 注入保持常数大小（摘要生效）。
- 预算感知：接近 budget 上限时 L1 让模型提前收尾。
- 回归短/长任务：verify 分数、done 落库不受影响。
- **额度纪律**：长任务与多轮迭代 token 消耗显著低于"全量 history"方案。

---

## 附：关键坑备忘（已写进 Claude 记忆）

- DAG 失败恢复原则：节点失败只废弃失败节点+下游，replan 合入后从已完成部分继续，绝不从头重来。
- verify/plan/classify 模型必须用硅基流动可用的（`Qwen3-Coder-480B` 已 403 禁用）。
- gateway 超时 ≥600s 才能扛住 DeepSeek 长生成。
- ECS 部署同步项目 `atoms2` 的 `.env` 需改 verify 模型。

*计划文件：`.claude/plans/twelve-layer-context-design.md`（六层上下文详细版）、本项目文件。*