# Medical NL2SQL Agent

把中文医疗业务问题转换成 SQL：意图识别 → 歧义检测 → 多轮澄清 → SQL 生成。

生成器会先在本地 SQLite 镜像上执行、自修正并做多候选投票，最终返回一条通过静态安全校验的 SQL。
**本轮不连接真实生产库，也不返回查询结果**；页面上看到的是 SQL 与口径说明，不是业务数据。

## 功能

- **多轮澄清**：时间口径、就诊类型、统计字段、科室匹配等 8 类歧义规则；一次只问一个 blocking 项，
  跳过或超预算的项以默认口径写入最终问题，并在响应里回显（`assumptions`）。
- **可解释**：返回重写问题、口径说明、澄清历史、模式链接（`schema_links`）与逐节点 `trace`；
  置信度三档（0.9 已验证 / 0.5 无共识 / 0.3 空结果），不把"能执行"当成"答对了"。
- **安全**：只允许单条 `SELECT`/`WITH`；表白名单；PII 列不得输出（`COUNT(DISTINCT ...)` 除外）；
  `SELECT *` 拒绝；SQL 在只读镜像上执行，结果行不出进程。
- **可恢复**：`request_id` 幂等、`GET /queries/{id}` 断线取回、刷新页面恢复待澄清问题；
  会话与 checkpoint 落 SQLite，后端重启不丢。
- **对照实验**：「智能引导」走完整澄清图，「自由对话」一次模型调用直接出 SQL，
  两种模式各自保存对话，用于对比效果。

## 快速开始

```bash
pip install -e ".[dev]"
pip install -r frontend/requirements.txt
cp .env.example .env                     # 填写 LLM_API_KEY
python -m app.tools.sql_agent.build_db   # 生成 SQLite 镜像（必做）
uvicorn app.main:app --reload            # 后端 :8000，/health 应返回 ok
streamlit run frontend/app.py            # 前端 :8501
```

`data/synthetic/medical.db` 被 `.gitignore` 忽略，**新环境必须跑一次 `build_db`**，否则每条查询都会因
`database_not_found` 失败。`GET /health` 会探测镜像库与知识文件，缺失时直接给出修复命令。
当前只支持单 worker（会话与 checkpoint 存在本地 SQLite）。

详细步骤、常见问题与开发命令见 [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)。

## 工作流

```
用户问题
   │
   ▼
analyze ──▶ ambiguity_policy ──▶ clarification（plan → ask[interrupt] → apply）
   │                                   ▲
   ▼                                   │ 有新歧义且预算未用完
finalize_question ──▶ final_ambiguity_check
   │
   ▼
generate_sql（evaluate → generate → execute → repair → select）
   │
   ▼
 SQL + 口径说明 + trace（不执行真实库、不返回结果行）
```

架构、技术栈与设计决策见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 目录

```
medical-nl2sql-agent/
├── app/                      # 后端
│   ├── main.py               # FastAPI 入口、/health、启动清理
│   ├── config.py             # pydantic-settings（读取 .env）
│   ├── api/                  # 路由、请求/响应模型、运行时单例
│   ├── graph/                # LangGraph 状态图、节点、澄清/SQL 子图、直连模式
│   ├── knowledge/            # schema / 指标 / 术语 / 歧义 / 示例检索
│   ├── prompts/              # 图内 LLM 提示词
│   ├── tools/sql_agent/      # SQLite 自修正 SQL 生成器
│   └── memory/               # SQLite checkpointer、会话/查询仓库、TTL
├── frontend/                 # Streamlit 前端（只调后端 HTTP，不持有 API Key）
├── data/                     # schema / 指标 / gold / 术语 / 歧义 / 示例（JSON）
├── docs/                     # 文档（见下）
├── scripts/check_questions.py# 对运行中的后端跑 10 条冒烟用例
└── tests/                    # unit / integration / frontend，全部离线
```

## 文档

| 文档 | 内容 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 项目功能、技术栈选型、图工作流与执行链路、关键决策 |
| [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) | 环境、依赖、建库、配置、启动、开发命令、FAQ |
| [docs/API.md](docs/API.md) | HTTP 接口、请求/响应示例、错误码、状态机 |
| [docs/MEMBER_A_KNOWLEDGE.md](docs/MEMBER_A_KNOWLEDGE.md) | 知识层：数据文件、检索规则、上下文构建、扩展方式 |
| [docs/MEMBER_B_GRAPH_API.md](docs/MEMBER_B_GRAPH_API.md) | 编排层与 HTTP 层：状态、节点、澄清子图、持久化、错误映射 |
| [docs/MEMBER_C_SQL_AGENT.md](docs/MEMBER_C_SQL_AGENT.md) | SQL 生成器：镜像建库、多候选投票、安全校验、只读执行 |
| [docs/FRONTEND.md](docs/FRONTEND.md) | 前端：三栏布局、状态模型、提问时序、失败恢复、验收清单 |
| [docs/TEST_QUESTIONS.md](docs/TEST_QUESTIONS.md) | 测试问题集、期望结果、已知问题、一键回归脚本 |

## 测试

```powershell
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe -m ruff check app frontend tests
```

测试全部离线：LLM 用 `tests/fakes.py` 的替身，SQLite 镜像在需要的测试里现场构建，不需要 API key。

## 已知限制

- 只生成 SQLite 方言 SQL；接 Oracle/达梦等真实库需另行方言转换，且只应使用只读账号。
- 金标准结果评测尚未实现（`data/gold_sql.json` 只做引用校验）。
- 知识层的科室/诊断/检查项目等枚举缺少真实数据字典（`glossary.json` 的 `needs_value_dictionary`）。
- 单 worker：水平扩展需要把 checkpoint 与会话仓库换成共享存储。
