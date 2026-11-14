# 架构决策记录：DAG 分阶段生成为何退役

> 本文替代早期《FORGE_DAG_PLAN.md》（那份计划曾把「DAG 分阶段生成」当主干，现已退役）。
> 保留这份记录是为了**写清「为什么这么选、砍了什么」**——这是评分维度里「工程思维」的直接得分点。

## 一、结论

生成链路最终收敛为**一条 ReAct 单次生成 + 三校核验**，不复用 DAG 分阶段拼接：

```
一句话需求 → 规范化(可选) → codegen 一次生成完整单文件 HTML
                              ↓
              ① 程序侧骨架校验（side_effect_checks，零额度）
                              ↓
              ② 换一个模型逐项给布尔（verify_html，跨家换脑，只回有序布尔数组）
                              ↓
              ③ 程序对位重算分 → 核验项未全过（score < 1.0）则针对缺失项定向重生成（≤2 次）
```

对应代码：`agent.py`（异步 run 入口 + 落库）、`forge_react.py`（LangGraph 图 + 三重校验 + checkpoint）、
`verify.py`（骨架校验 + 跨模型核验）、`codegen.py`（单文件 HTML 生成）。

## 二、为什么砍掉 DAG（实测问题）

早期按「复杂度分类 → DAG 分阶段拼片段」实现，实测产出质量差：

1. **空 HTML / 半成品**：模型在分阶段时经常返回带 markdown 围栏、片段拼接指令注释
   （`<!-- 片段 A：接在… -->`）的半成品，页面「白底黑字露出代码」。
2. **无限打补丁**：阶段核验不过就补 patch，补丁再不过再补，耗 token 且容易越补越散。
3. **慢且贵**：一次需求拆成多阶段、每阶段多次串行 LLM 调用，墙钟时间与额度都成倍放大，
   与「$70 额度纪律」直接冲突。

结论：**宁要稳定闭环，不要花哨架构**。一次生成 + 校验闭环，反而比拆阶段更稳、更快、更省。

## 三、保留了什么（DAG 里的有价值部分）

- **三重校验闭环**：程序侧骨架校验 + 跨模型核验 + 程序算分，这套思路从 DAG 抽出来，
  原样用在 ReAct 单次生成上（`verify.py`）。
- **失败不整体重来**：短任务核验不过，只针对缺失项定向重生成（`REACT_MAX_RETRY`），
  不把整个应用推翻重来。
- **SSE 实时可见**：`stage/verify/done/error` 事件流照常，前端实时渲染进度。

## 四、复杂度路由同样退役

`router.py` 的 `classify()`（模型判长短）也一并删除——因为长短两条路最终都走同一套
ReAct 生成，不再需要路由。新建 / 迭代的差异只剩「有无现有 HTML」：
- 无 `project_id` → 全新生成；
- 有 `project_id` → 在现有 HTML 上迭代。

## 五、断点续跑的真实形态

DAG 的「阶段级 checkpoint + 续跑」随 DAG 一起退役，但「断点续跑」能力没有丢——它以 LangGraph
StateGraph 的 PostgreSQL checkpoint 形态保留下来（`forge_react.py` 把 ReAct 循环编译成图，每个节点边界
= 一个 checkpoint 点）：

- 后端崩溃后，`run_manager.resume_interrupted_runs()` 把卡在 `running` 的 run 重新拉起；
- 单应用 run：`resume_forge` 用同 `run_key`（= `thread_id`）重新 `ainvoke`，从最近 checkpoint 续跑、
  跳过已完成节点；单次 `codegen.generate`（最贵的一步）中断仍会重跑该步（节点级恢复）；
- 赛马（`race-*`）：`resume_race` 重拉 `_race_worker`，每路（`thread_id = race-{id}:{model}`）从各自
  checkpoint 恢复，已完成路立即返回、未完成路从断点续跑。

## 六、相关文档

- 权威验收：《Atoms笔试避坑指南(1).docx》
- 最终计划与取舍总览：`复刻计划.md`
- 提交说明（含「为什么砍 DAG」）：`提交说明.md`
- 项目说明：`README.md`
