# Molecules（Atoms Forge）

> 一句话，生成一个能打开、能点、能存数据的网页应用。

输入一句自然语言描述，智能体自动生成**单文件 HTML 应用**（内联 CSS/JS + localStorage），在页面里 **iframe 实时预览**，结果**持久化**到数据库（刷新/重开浏览器还在）。生成后可继续**对话迭代修改**，或用**赛马模式**让多个模型并行生成、挑一个最喜欢的，最后**导出 HTML 文件**或**生成分享链接**。

这是对标 Atoms「一句话 → 应用」核心体验的面试 Demo，不是复刻 Atoms 的多智能体框架，也不做一个现成的业务应用——重点在于「AI 帮我生成应用」这个闭环。

## 核心闭环

```
用户输入一句话 → 需求澄清(可选) → 智能体生成单文件 HTML → iframe 实时预览 → 持久化
                                                                        ↑
                                                            对话迭代 / 赛马择优
```

## 功能

- **一句话生成**：输入需求，自动产出完整可运行的单文件 HTML 应用（真实交互，数据用 localStorage 持久化）。
- **视觉设计库**：内置通用设计铁律 + 6 个视觉方向模板（极简瑞士/温暖编辑/粗野波普/数据面板/极客终端/粘土柔和），每个方向带具体配色 token、字体、布局骨架；再配排版/状态/表单/图表/交互信息架构规范。生成时按需求主题或「更多选项 → 视觉风格」自动选方向，摆脱「AI 模板感」。
- **需求澄清**：生成前先结构化理解，有歧义时一次性问清（带默认值、可跳过）。
- **赛马模式**：多个模型并行生成，SSE 实时展示进度，选一个落库——对标 Atoms 招牌能力。
- **对话式迭代**：生成后继续一句话修改（「加个负责人字段」），在原结果上重新生成。
- **持久化**：项目 + 聊天历史 + 赛马结果全部落 PostgreSQL，刷新/重开浏览器都在。
- **导出 / 分享**：一键导出 `.html` 文件（双击即可打开），或发布生成公开分享链接 `/view/{slug}`。
- **额度保护**：内置预生成示例兜底（无 API Key 也能演示）、熔断器、预算上限、快照复用（相同调用不重复扣费）、DeepSeek→SiliconFlow 自动 fallback。
- **稳定性**：SSE 进度流、崩溃后重跑、失败可重试、错误提示。
- **质量闸门**：生成后走「结构校验 + 确定性反 AI 味检查（靛蓝主色/紫蓝渐变/emoji 图标/编造指标等，P0 自动回喂重生成）+ 跨模型核验」三重闭环。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI · SQLAlchemy 2.0 (async) · Alembic · LangGraph · OpenAI 兼容 SDK（DeepSeek 官方 + SiliconFlow） |
| 前端 | React 18 · TypeScript · Vite · Tailwind CSS · shadcn/ui · React Query · react-router-dom · sonner |
| 数据库 | PostgreSQL（docker-compose 默认）；本地可切 SQLite |
| 部署 | Docker Compose（nginx 前端 :80 反代 `/api` → FastAPI，PostgreSQL 持久卷） |

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
# 1. 准备环境变量（填入真实 API Key）
cp .env.example .env

# 2. 一键启动（db + backend + frontend）
docker compose up -d --build

# 3. 打开 http://localhost
```

前端托管在 nginx（:80），`/api` 反代到后端容器，SSE 已关闭缓冲、超时放宽到 900s（LLM 生成单文件 HTML 可能需几分钟）。

### 方式二：本地开发

```bash
# 后端（需先准备 Postgres 或 SQLite + 环境变量）
cd app/backend
pip install -r requirements.txt
export DATABASE_URL=postgresql+asyncpg://atoms:change-me@localhost:5432/atoms   # 或用 SQLite 快速起：
# export DATABASE_URL=sqlite+aiosqlite:///./atoms.db
export DEEPSEEK_API_KEY=sk-xxx
export SILICONFLOW_API_KEY=sk-xxx
alembic upgrade head
uvicorn main:app --reload --port 8000

# 前端（另一个终端，vite :3000 会把 /api 代理到 :8000）
cd app/frontend
pnpm install
pnpm dev
```

## 环境变量

见 `.env.example`。关键项：

- `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` — 主生成模型（DeepSeek 官方，更快更稳）
- `SILICONFLOW_API_KEY` / `SILICONFLOW_BASE_URL` — 核验模型（跨家换脑，赛马打分/阶段核验）
- `LLM_MAIN_MODEL` / `LLM_VERIFY_MODEL` / `LLM_INTENT_MODEL` — 模型路由
- `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` — 数据库（docker-compose 自动拼 `DATABASE_URL`）
- `DATABASE_URL` — 本地手动运行时必填

> 没有 API Key 也能演示：内置「读书笔记 / 健身打卡 / 招聘跟进」三个预生成示例，命中即返回缓存 HTML，不消耗额度。

## 项目结构

```
app/
├── backend/
│   ├── main.py                 # FastAPI 入口（0.0.0.0、崩溃恢复）
│   ├── routers/forge.py        # HTTP API（项目 / 运行 / SSE / 赛马 / 发布分享）
│   ├── services/forge/         # 核心生成链路
│   │   ├── agent.py            #   意图路由 + 生成 + 落库（LangGraph）
│   │   ├── forge_react.py      #   ReAct 生成 + 三校核验 + 反 AI 味闸门
│   │   ├── verify.py           #   结构校验 + 跨模型核验 + 反 AI 味检查（slop_checks）
│   │   ├── codegen.py          #   单文件 HTML 生成（注入视觉规范）
│   │   ├── design_templates.py #   视觉设计库（铁律 + 布局骨架 + 6 视觉方向 + 图表规范）
│   │   ├── spec.py             #   需求理解 / 澄清
│   │   ├── extras.py           #   赛马模式
│   │   ├── gateway.py          #   LLM 网关（重试/超时/熔断/预算/快照）
│   │   ├── breaker.py          #   熔断器
│   │   ├── budget.py           #   预算
│   │   ├── seed.py             #   预生成示例（无 Key 兜底）
│   │   └── run_manager.py      #   异步运行 + SSE 事件流 + 崩溃恢复
│   ├── models/                 # ORM（projects / agent_runs / races / chat_messages / llm_calls …）
│   └── alembic/                # 数据库迁移
└── frontend/
    └── src/
        ├── pages/              # Home / Create / Project / View（分享页）
        ├── components/         # ChatPanel / AppRuntime / Shell
        └── lib/forge.ts        # API 客户端 + SSE 封装
```

## 部署

`docker compose up -d` 即可（后端监听 `0.0.0.0`，端口与数据库均通过环境变量配置，密钥不入库）。如需托管到 Render/Railway 等平台，把后端启动命令设为 `alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT`，数据库换成平台自带云数据库（免费实例临时文件系统会在重启后清空，务必用持久化数据库）。
