# 项目架构

医疗数据自然语言查询与 SQL 生成系统：把中文医疗业务问题（如「上个月门诊人次是多少」）转换成一条通过
静态安全校验的 SQL。

**本轮边界（贯穿全部代码与文档）：**

- 只生成 SQL，**不连接真实生产库、不返回查询结果**；
- SQL 只在本地 SQLite 镜像（`data/synthetic/medical.db`，合成数据）上执行，用于自修正与验证可执行性；
- 生成的 SQL 是 SQLite 方言，接 Oracle/达梦等真实库前需另做方言转换；
- 不做金标准结果对比评测（`data/gold_sql.json` 只做引用校验）。

## 1. 技术栈选型

| 层 | 选型 | 为什么 |
|---|---|---|
| HTTP 层 | FastAPI + Pydantic v2 | 同步路由交给线程池执行，图调用是阻塞的；自动生成 `/docs` |
| 编排层 | LangGraph `StateGraph` | 需要多轮澄清、条件路由、可暂停（`interrupt`）与断点恢复，链式调用表达不了 |
| 持久化 | SQLite（标准库 `sqlite3` + WAL） | 会话索引、对话轮次、图 checkpoint 都落盘；单机部署不需要额外服务，重启可恢复 |
| LLM | DeepSeek（LangChain `ChatDeepSeek`；SQL 生成走 OpenAI-compatible SDK） | 中文医疗问题理解与 SQL 生成；两套客户端分工见下 |
| SQL 校验 | `sqlglot`（SQLite 方言） | 静态解析出 AST，做单语句、表白名单、PII 列检查；前端也用它格式化 SQL |
| 知识层 | 6 个 JSON 文件 + 纯 Python 检索 | 口径、术语、歧义规则是可维护的业务资产，不进 prompt 硬编码 |
| 前端 | Streamlit + httpx | 单文件脚本式 UI，快速做对话/澄清/调试视图；前端只调后端 HTTP |
| 测试 | pytest + Streamlit `AppTest` + `httpx.MockTransport` | 全部离线：图与 SQL 生成用 fake LLM/executor |

两套 LLM 客户端有意分开：

- `app/graph/llm.py::DeepSeekLLMClient`（LangChain `ChatDeepSeek`）负责**图内**的
  指标选择、澄清问法润色、可选歧义补充；
- `app/tools/sql_agent/llm.py::OpenAICompatLLM`（OpenAI SDK）负责**SQL 生成/修复/难度评估**，
  输出结构化 JSON 候选。分开始于两者提示词与重试策略不同，也便于测试分别注入 fake。

## 2. 模块划分

| 模块 | 职责 | 文档 |
|---|---|---|
| `app/api` | FastAPI 路由、请求/响应 schema、幂等重试、断线取回、错误映射 | [MEMBER_B_GRAPH_API.md](MEMBER_B_GRAPH_API.md) |
| `app/graph` | LangGraph 状态、节点、路由、澄清子图、SQL 生成子图、直连模式 | [MEMBER_B_GRAPH_API.md](MEMBER_B_GRAPH_API.md) |
| `app/knowledge` | schema / 指标 / 术语 / 歧义规则 / SQL 示例的加载与检索 | [MEMBER_A_KNOWLEDGE.md](MEMBER_A_KNOWLEDGE.md) |
| `app/tools/sql_agent` | SQLite 自修正 SQL 生成（评估→生成→执行→修复→投票）与静态安全校验 | [MEMBER_C_SQL_AGENT.md](MEMBER_C_SQL_AGENT.md) |
| `app/memory` | SQLite checkpointer、会话/查询/轮次仓库、TTL 清理 | [MEMBER_B_GRAPH_API.md](MEMBER_B_GRAPH_API.md) |
| `app/prompts` | 图内 LLM 的提示词（分析、澄清、歧义、SQL 兜底） | [MEMBER_B_GRAPH_API.md](MEMBER_B_GRAPH_API.md) |
| `frontend` | Streamlit 三栏界面：模式、询问记录、对话与澄清卡片 | [FRONTEND.md](FRONTEND.md) |

依赖方向：`frontend → HTTP → app/api → app/graph → app/knowledge / app/tools`。
`app/tools` 不 import `app/graph` / `app/api` / `langgraph`（由
`tests/unit/tools/test_import_boundaries.py` 守护）。

## 3. 执行链路（图工作流）

### 3.1 引导模式（`mode="guided"`，默认）

```
START
  │
  ▼
analyze                 指标 / 术语 / schema 检索；多个指标候选时调一次 LLM 选指标
  │
  ▼
ambiguity_policy        规则命中 + 轮次预算（min(MAX_CLARIFICATION_TURNS, 5)）
  │
  ├── 有 blocking 歧义 ──▶ clarification 子图
  │                          plan ─▶ ask(interrupt) ─▶ apply ─┬─▶ plan（还有问题）
  │                                                           └─▶ END
  ▼ 无歧义（或澄清完毕）
finalize_question       合并已答槽位 + 默认口径，重写问题，重算 schema_context、schema_links
  │
  ▼
final_ambiguity_check   只处理「重写后新出现」的 blocking 歧义；有且预算未用完则回澄清子图
  │
  ▼
generate_sql            子图，内部走 app/tools/sql_agent/core.generate_sql
  │
  ▼
 END
```

关键机制：

- **暂停与恢复**：`ask` 节点调用 `interrupt(pending_question)` 挂起整个图；API 把
  `pending_question` 转成响应里的 `clarification`。用户回答后以 `Command(resume=answer)` 恢复。
  每个查询一个线程：`thread_id = query_id`。
- **轮次是预算而不是题量**：`max_rounds = min(settings.max_clarification_turns, 5)`。问几题由实际
  pending 决定，预算只封顶；`clarification_round >= max_rounds` 时不再提问。
- **默认口径必须进 prompt**：跳过、超预算或最终检查新出现的歧义，都会写入
  `assumptions`（`source="default"`），并在重写问题后追加「未澄清项按默认口径：…」——只写在
  `assumptions` 里模型看不到，会重新猜一个不同口径。
- **子图共用 `GraphState`**：LangGraph 中子图若与父图 state schema 不一致，父图没有的 key 会被
  静默丢弃。澄清子图与 SQL 子图都直接使用父图 `GraphState`，只写父图字段。

### 3.2 自由对话（`mode="direct"`）

对照实验用：**一次 LLM 调用**直接从问题生成 SQL，不检测歧义、不提问、不修复、不升级；仍走同一套
静态安全校验与 SQLite 执行验证，返回同样的响应结构，`trace[0].node = "direct_sql"`。
实现见 `app/graph/direct.py` 与 `routes_queries.run_query` 的分支。

### 3.3 单次 SQL 生成内部链路

```
evaluate   关键词启发式决定初始候选数 n（1 或 2）；SQL_LLM_EVALUATE=true 时可改用 LLM
   │
generate   LLM 一次返回 n 条候选 → 归一化去重 → sqlglot 静态安全检查
   │
execute    在只读 SQLite 镜像上执行（行数/哈希/是否截断）；结果行不离开进程
   │
repair     执行失败的候选回传错误修正（单候选最多 1 次，总预算 MAX_REPAIR_ROUNDS）
   │
select     只有 1 条有效候选 → 直接返回（verified=true）
           多条结果哈希多数一致 → 返回多数组内静态分最高者
           不一致且初始候选 ≥2 → 升级生成第 3 条（只升一次）
           仍无共识 → 静态分选优，verified=false
           全部非法/失败 → error: sql_generation_failed
```

置信度只有三档：`0.9`（verified 且结果非空）、`0.5`（无共识，静态分兜底）、`0.3`（结果为空）。
空结果不算错误，但也不可信。

## 4. 状态与持久化

- `app/graph/state.py::GraphState`：输入（`original_question`）、知识检索（`terms/metrics/schema_context`）、
  澄清（`pending_codes/asked_codes/resolved_slots/assumptions/clarification_history`）、
  输出（`sql/sql_confidence/sql_assumptions/status/error`）与 `trace`（`operator.add` 累积）。
- `data/checkpoints.db`：LangGraph `SqliteSaver`，每个 query 一个线程，重启可恢复待澄清问题。
- `data/sessions.db`：`sessions / turns / queries` 三张表；`queries` 表支撑 `request_id` 幂等与
  `GET /queries/{id}`；超过 `SESSION_TTL_HOURS`（默认 24h）的记录在启动时清理，checkpoint 线程一并删除。
- 两个文件都是运行时产物，已被 `.gitignore` 忽略。

## 5. 安全与隐私

- **只读**：只允许单条 `SELECT` / `WITH ... SELECT`；写操作、多语句、`PRAGMA` 直接拒绝且不执行。
- **表白名单**：SQL 中所有表名必须来自 `data/schema.json`（CTE 名除外）。
- **PII 不上屏**：`schema.json` 里 `is_pii` 的列不得出现在 SELECT 输出，也不得出现在
  `GROUP BY / ORDER BY / HAVING`；唯一例外是 `COUNT(DISTINCT pii)`。`SELECT *` 与 `t.*` 一律拒绝。
- **执行隔离**：`file:...?mode=ro` + `PRAGMA query_only=ON`，`set_progress_handler` 超时中断，
  `fetchmany(max_rows + 1)` 限制行数；结果行只用于哈希，不写入 state / trace / API / 日志。
- **密钥**：`LLM_API_KEY` 只放后端 `.env`；前端不持有 key，也不 import 任何 LLM SDK。

## 6. 关键决策记录

| # | 决策 | 原因与后果 |
|---|---|---|
| ADR-001 | 用 LangGraph `StateGraph` 编排 | 多轮澄清、条件路由、可暂停与断点恢复是硬需求；代价是图与节点需保持无副作用、依赖注入以便测试 |
| ADR-002 | 歧义检测独立于 SQL 生成 | 先生成再报错会白花一次 LLM 调用，体验也差；澄清失败或跳过时以默认口径兜底并回显 |
| ADR-003 | SQL 生成在本地 SQLite 镜像上执行自修正 | 执行反馈比"看起来对"的静态检查可靠；镜像不入库，新环境必须跑 `build_db` |
| ADR-004 | 安全校验放在执行之前（sqlglot AST） | 非法 SQL 不浪费执行时间；PII 规则不依赖模型自觉 |
| ADR-005 | 会话与 checkpoint 用 SQLite 而不是内存 | 原先 MemorySaver 重启即丢，客户端拿着 `query_id` 只能 404；单 worker 足够，水平扩展需换共享存储 |
| ADR-006 | 子图与父图共用 `GraphState` | LangGraph 会静默丢弃子图中父图没有的字段；共用 state 零映射、最稳 |
| ADR-007 | 直连模式复用 core 的校验与执行 | 对照实验只应去掉"澄清/修复"这些额外轮次，安全与验证口径必须一致 |

## 7. 已知限制与后续方向

- 单 worker：本地 SQLite 支撑不了多进程并发写；要扩容需换 Postgres checkpointer 与会话库。
- SQLite 方言：接真实库前要确认方言（Excel 的 gold SQL 是 Oracle 家族写法）并只给只读账号。
- 知识层的枚举值（科室、诊断、检查项目）还没有真实数据字典，`glossary.json` 的
  `needs_value_dictionary` 列出待补项。
- 金标准评测（gold SQL 结果集对比）未做，见 [TEST_QUESTIONS.md](TEST_QUESTIONS.md) 的已知问题清单。
