# 组员 C 开发计划：SQL 生成子图（LangGraph 子图 + 多候选执行自修正）

> 目标：B 给出澄清后的完整问题 + 模式链接结果，C 用 **LangGraph 子图**实现 SQL 生成：
> 问题评估（决定候选数）→ 多候选生成 → SQLite 执行/修正 → 结果投票/校验 → 返回最终 SQL。
>
> 本轮只生成 **SQLite SQL**，在本地 SQLite 镜像上执行验证；不接官方 ReFoRCE、不连真实库。
>
> 相比旧版（纯 Python ReFoRCE-lite）的三个改动：
> 1. 生成模块改成 **LangGraph 子图**（与澄清子图同构）；
> 2. 澄清后不只返回有效问题，**同时返回模式链接结果**（涉及的表/列/关系）；
> 3. 子图核心流程：多候选 → 执行 → 错误回传修正 → 结果投票/校验（升级第 3 条）。
>
> **实现约束（2026-10-30 评估后补充）**：子图与父图 state schema 不一致时，子图输出中父图没有的 key 会被 LangGraph **静默丢弃**（已实测）。因此本计划要求子图**共用父图 `GraphState`**，或经 wrapper 显式映射——详见 §3.1、§6.1。这是能否直接接入 B 的前提。

## 1. 分工边界

- **A**：`data/schema.json`（13 表、13 关系）、`data/sql_examples_sqlite.json`（15 条 SQLite 示例）、（待交付）`data/gold_sql_sqlite.json`。
- **B**：多轮澄清 → 输出 `rewritten_question`（完整问题）+ `schema_links`（模式链接结果）；调用 C 的子图；API 响应里带 `schema_links`。
- **C**：接收 `question + schema_links`，生成 SQLite SQL；SQLite 镜像执行、自修正、多候选投票；返回最终 SQL + 验证状态。
- C 不负责追问、不做评测、不连真实生产库、不 import `app/graph`。

## 2. 目录结构

```text
app/tools/sql_agent/            # C 的纯 Python 核心（不 import langgraph / app.graph）
├── __init__.py
├── build_db.py                 # schema.json -> SQLite 镜像 + 合成数据
├── llm.py                      # OpenAI-compatible 封装（n 候选）
├── prompt.py                   # schema 文本、评估/生成/修复 prompt
├── safety.py                   # 只读、单语句、PII 静态检查（sqlglot）
├── executor.py                 # SQLite 只读执行、超时、行数限制
├── selector.py                 # 结果哈希、投票、静态打分
├── core.py                     # 核心编排：评估/生成/执行/修正/投票（纯函数）
└── cli.py                      # 命令行演示

app/graph/subgraphs/
├── clarification.py            # B 的澄清子图（已有）
└── sql_generation.py           # C 写：SQL 生成子图（包装 core.py）

data/
├── schema.json                 # A 提供
├── sql_examples_sqlite.json    # A 提供（15 条）
├── gold_sql_sqlite.json        # A 待交付
└── synthetic/medical.db        # C 生成（build_db.py）

docs/
├── PLAN_MEMBER_C.md            # 本文件
└── SQLITE_AGENT.md             # 使用/配置/限制说明
```

依赖方向：`app/graph → app/tools`（子图包装 core），`app/tools` 不反向依赖。

旧的 `app/tools/sql_generator/`（`base.py` + `reforce_adapter.py`）**保留不动、不再被引用**，仅作历史记录；新代码一律走 `app/tools/sql_agent/`，避免有人接错入口。

## 3. 子图主流程

```text
输入：question（澄清后）+ schema_links（模式链接）
  ↓
[1] evaluate 问题评估节点
    规则/LLM 判定难度 → 初始候选数 N（1 或 2）
  ↓
[2] generate SQL 生成节点
    LLM 生成 N 条候选；提取 SQL、归一化去重、静态安全检查
  ↓
[3] execute 执行节点
    N 条候选在 SQLite 镜像执行；
    语法/执行失败的候选：错误回传 → 回到 [2] 重新生成（共享修复预算）
    输出：N 条候选 + 各自执行结果（结果只留在子图内）
  ↓
[4] select 校验/投票节点
    - 只有 1 条有效候选 → 直接返回（verified=true）
    - 2 条候选结果一致 → 任选一条返回
    - 2 条候选结果不一致 → 回 [2] 升级生成第 3 条；
      新结果与原 2 条哪条一致 → 返回那条
    - 第 3 条与前 2 条都不一致 → 静态分选优，verified=false，说明原因
    - 候选全部非法/失败 → 返回 error
  ↓
输出：sql + verified + candidates + 统计（llm_calls/executions/repairs/elapsed_ms）
```

### 3.1 子图状态：必须与父图兼容（关键约束）

**LangGraph 实测结论**：编译后的子图如果与父图 **state schema 不一致**，直接 `add_node` 会让子图输出中父图没有的 key **被静默丢弃**——不报错、不告警。实测中返回结果只剩 `{'question': 'q'}`，`final_sql` 直接消失。现有澄清子图能正常工作是**因为它和父图共用同一个 `GraphState`**。

因此**不允许**把 `SQLGenerationState` 直接当作子图 state 接进主图。两种可行做法：

| 方案 | 做法 | 评价 |
|---|---|---|
| **A（推荐）** | 子图直接使用父图 `GraphState`，节点只读写 `sql / sql_source / sql_assumptions / status / error / trace`；候选、投票、修复计数等中间量作为 `core.py` 内部的局部数据传递，不放进 state | 与澄清子图同构，零映射代码，最稳 |
| B | 保留 `SQLGenerationState` 作为子图私有 state，在 `builder.py` 写 wrapper 节点：从 `GraphState` 取入参，出参显式映射回父图字段 | 多一层胶水，但子图可独立测试 |

无论选 A 还是 B，**父图侧必须收到这 6 个字段**：`sql`、`sql_source`、`status`、`error`、`sql_assumptions`、`trace`。缺任何一个，API/前端都会表现为「SQL 消失」或「状态不对」，而且不抛异常。

下面的 `SQLGenerationState` 作为 `core.py` 的内部数据结构保留（方案 A 下不进入 LangGraph state；方案 B 下作为子图私有 state），字段本身不变：

```python
class SQLGenerationState(TypedDict, total=False):
    question: str                      # 澄清后的完整问题
    schema_links: list[dict]           # 模式链接：[{table, columns, relationship, ...}]
    n_initial: int                     # 评估结果：1 或 2
    candidates: list[dict]             # [{sql, safe, exec_ok, rows, truncated, error, has_pii, repaired}]
    repair_budget: int                 # 剩余修复次数（共享，默认 2）
    escalated: bool                    # 是否已升级生成第 3 条
    final_sql: str | None
    verified: bool
    result_empty: bool
    reason: str                        # 选择理由（人类可读）
    stats: dict                        # llm_calls/executions/repairs
    error: str | None
    trace: list[dict]
```

### 3.2 子图节点与路由

```text
START → evaluate → generate → execute → select → END
                    ↑           │
                    └───────────┴── 修复/升级回环
execute --有失败且预算--> generate   # 错误回传修正
select   --不一致且可升级--> generate # 第 3 条升级
```

路由函数放在子图内（`app/graph/subgraphs/sql_generation.py`），节点实现调用 `app/tools/sql_agent/core.py` 的纯函数。

子图**不含 `interrupt`**，不需要 checkpointer，也不产生子图级持久化状态；每次 `generate_sql` 节点被调用时从头执行一次。

## 4. 节点设计

### 4.1 evaluate —— 问题评估（候选数 1 或 2）

- **默认：关键词启发式（0 LLM 调用）**。

  ```python
  COMPLEX_WORDS = ["排名", "占比", "比例", "同比", "环比", "平均", "各", "每", "趋势", "top"]
  JOIN_WORDS = ["和", "与", "对比", "分别"]

  def estimate_n(question: str) -> int:
      if any(w in question for w in COMPLEX_WORDS):
          return 2
      if any(w in question for w in JOIN_WORDS) or len(question) > 12:
          return 2
      return 1
  ```

- **可选 LLM 评估**（`SQL_LLM_EVALUATE=true`）：一次结构化调用，返回 `{"complexity": "simple|complex", "n": 1|2}`；解析失败回退启发式。**默认关闭**，避免每个问题多花一次调用。

### 4.2 generate —— 生成 N 条候选

- prompt：问题 + schema 文本 + SQLite 示例（top-k 相关）+ 只读/PII/SQLite 方言规则。
- **schema 文本来源优先级**（避免与 B 的上下文分叉）：
  1. 父图传入的 `schema_context.prompt_text`（B 已算好，≤8k，含指标/示例/PII 标注）—— **首选**；
  2. 父图没传时，才由 `prompt.py` 读 `data/schema.json` 自行拼；
  3. `schema_links` 只用于提示和选表过滤，**不替代**全量 schema。
- **LLM 客户端**：默认由 `settings` 新建（`llm.py`，OpenAI-compatible，支持 n 候选）；`build_sql_subgraph(deps, llm=...)` 支持注入，测试传 fake，**测试中禁止联网**。
- 输出：`n` 条 SQL（一条请求里并列输出，或 `n` 次调用，temperature 0.7）。
- 提取 SQL 后：归一化去重（相同 SQL 只留一条）、静态安全检查（`safety.py`，sqlglot 解析 + 只读/PII 检查）。
- 返回候选列表 + 去重/安全过滤信息。

### 4.3 execute —— 执行 + 修正

- 每个候选在 `data/synthetic/medical.db` 上只读执行（`file:...?mode=ro` + `PRAGMA query_only`）。
- 超时 5s（`set_progress_handler`），最多取 50 行，记录 `truncated`。
- 失败候选：把 **错误信息 + 原 SQL + schema 摘要** 回传 `generate` 重新生成（temperature 0）。
  - 单候选最多修 1 次；总修复预算 `MAX_REPAIR_ROUNDS=2`（共享）。
  - 空结果**不当作错误**（可能确实无数据），继续参与投票。
- 只回传错误给 LLM，**不把查询结果行回传**（防 PII 泄露）。

### 4.4 select —— 投票/校验/升级

按优先级：

1. 无有效候选（全部非法/执行失败且修复耗尽）→ `error`。
2. 有效候选只有 1 条 → 返回它（`verified=true`）。
3. 有效候选 ≥ 2 条：
   - 结果哈希两两一致 → 任选一条（`verified=true`，`votes=2`）。
   - 不一致 → **升级**：生成第 3 条（只允许一次，`escalated` 标记）。
     - 第 3 条与其中一条一致 → 返回该条（`votes=2`）。
     - 第 3 条与前 2 条都不一致 → **静态分选优**（安全 + 执行通过 + 无 PII + 少 join + SQL 短），`verified=false`，`reason` 说明无共识。
4. 空结果规则：空+空算一致（`result_empty=true`）；空 vs 非空算不一致（触发升级）。

结果哈希：排序后取行值字符串 + 行数 + 是否截断（忽略列别名差异，避免"cnt" vs "count"误判不一致）。

## 5. 成本控制（沿用并加强）

1. 启发式评估，默认不调 LLM。
2. 共识提前停止：前两条一致直接返回，不生成第 3 条。
3. 不一致才升级，且**只升一次**（上限 3 条）。
4. 只修失败候选；空结果不修。
5. 去重后再执行。
6. 静态预过滤（非 SELECT/多语句/PII 直接拒绝，不浪费执行）。
7. 结果哈希只采样前 50 行 + 行数 + 截断标记。
8. 不用 LLM judge。
9. 返回 `llm_calls / executions / repairs / elapsed_ms`。

## 6. 与 B 的接口（含模式链接）

### 6.1 子图 ↔ 父图字段映射（必须实现）

| 父图 `GraphState` 字段 | 子图产出 | 说明 |
|---|---|---|
| `sql` | `final_sql` | 无有效候选时为 `None` |
| `sql_source` | 固定 `"sqlite"` | 接口/前端用于区分来源 |
| `sql_assumptions` | 由 `verified` / `result_empty` / `reason` 生成的中文说明 | 例如「2 条候选结果一致」「无共识，已按静态分选优」 |
| `sql_confidence` | `verified ? 0.9 : 0.5` | 可选字段，无则写 `None` |
| `status` | `"ok"` / `"error"` | **必须返回**，`routes_queries._status()` 依赖它 |
| `error` | `{"code": ..., "message": ...}` | `code` 用 `sql_generation_failed` / `sql_no_valid_candidate` |
| `trace` | 追加 `{node: "generate_sql", ok, candidates, executions, repairs, elapsed_ms}` | **只放行数/哈希/耗时，不放结果行** |

`candidates`、`verified`、`reason`、`stats` 等中间量如需暴露，**写进 `trace`**，不新增顶层字段，避免 `QueryResponse` 一路改到前端。

### 6.2 B 的 `finalize_question` 输出 `schema_links`

```python
schema_links = [
    {"table": "visit", "columns": ["visit_id", "activity_type_code"], "relationship": "visit→patient"},
    {"table": "medical_expense_rec", "columns": ["visit_id"], "relationship": "visit→medical_expense_rec"},
]
```

来源：`finalize` 节点已经算出的 `schema_context`（其 `to_dict()` 已含 `tables / columns / join_paths`），拼装即可，**不需要重新调知识层**。

### 6.3 C 的子图输入

`question + schema_links + schema_context.prompt_text`（可选）。

**schema 文本优先用 `prompt_text`**（见 §4.2），子图内部读 `data/schema.json` 只作为兜底；`schema_links` 用于提示与过滤，不替代全量 schema。

### 6.4 B 侧改造点（共 5 处）

| 文件 | 改动 |
|---|---|
| `app/graph/subgraphs/sql_generation.py` | 新建：子图本体 + `build_sql_subgraph(deps, llm=None)` |
| `app/graph/builder.py` | `generate_sql` 节点换成子图（方案 A 直接 `add_node`；方案 B 加 wrapper 节点做映射） |
| `app/graph/state.py` | 加 `schema_links: list[dict]` |
| `app/graph/nodes/finalize.py` | 输出 `schema_links` |
| `app/api/schemas.py` + `app/api/routes_queries.py` | `QueryResponse` 加 `schema_links`，`_to_response()` 映射 |

前端 `frontend/models.py::QueryResponse` 本轮**不加**字段：`schema_links` 先只出现在接口响应与 `trace` 里。注意 `result.py` 的 trace 是折叠的原始 JSON，**验收请用 `curl POST /queries` 确认字段**，不要指望页面显示。

### 6.5 约束

- 子图不含 `interrupt`，不需要 checkpointer；
- 子图入口必须取 `rewritten_question or original_question`，不能直接用 `original_question`；
- `app/tools/sql_agent/` 不 import `app/graph` / `langgraph`（边界测试保留）。

## 7. 配置

`sqlite_db_path` 与 `max_repair_rounds` **已存在**于 `app/config.py` 和 `.env.example`，不需要新增。只需补 4 项：

```python
sql_llm_evaluate: bool = False   # 是否用 LLM 评估候选数（默认启发式）
sql_exec_timeout: int = 5
sql_max_rows: int = 50
sql_max_candidates: int = 3      # 上限：1/2/3
```

`.env.example` 对应增加：

```text
SQL_LLM_EVALUATE=false
SQL_EXEC_TIMEOUT=5
SQL_MAX_ROWS=50
SQL_MAX_CANDIDATES=3
```

LLM 复用 `LLM_API_KEY / LLM_MODEL / LLM_BASE_URL`（DeepSeek，OpenAI-compatible）。

## 8. 开发顺序（4 天）

| 天 | 任务 | 交付 |
|---|---|---|
| D1 | `build_db.py`（schema 建库 + 合成数据）+ `prompt.py` + `safety.py`；生成 `data/synthetic/medical.db` | 能建库、能打印 schema 文本、一条 SQL 能执行 |
| D2 | `llm.py` + `core.py` 的 generate/execute + 单候选链路 | `cli` 对 3 个问题返回 verified SQL |
| D3 | `selector.py` + `core.py` 投票/升级/兜底 + `app/graph/subgraphs/sql_generation.py` | 子图闭环，B 接入 `builder.py` |
| D4 | B 侧 `schema_links` + API 字段 + 联调 + `docs/SQLITE_AGENT.md` | 可演示、可交接 |

每日本门槛（不做完不进下一天）：

- **D1**：`build_db.py` 建出 `medical.db`，并把 `data/sql_examples_sqlite.json` 的 **15 条示例全部在 `medical.db` 上重跑通过**。这比「13 张表都有数据」更能暴露合成数据 join 键不匹配的问题。
- **D2**：`tests/unit/tools/` 用 fake LLM + fake 执行器跑通「单候选成功」和「单候选执行失败 → 修复成功」两条路径，不联网。
- **D3**：子图测试覆盖投票一致 / 不一致升级第 3 条 / 无共识静态分兜底 / 全部失败四条路径；接入 `builder.py` 后 `pytest tests -q` 全绿。
- **D4**：`curl POST /queries` 能同时看到 `schema_links` 和非空 `sql`。

### 8.1 测试与 fake（新增，D2 门槛）

现有 `tests/fakes.py::FakeLLMClient` 只实现单条 `generate_sql`，**不支持 n 候选与修复重试**，C 不能直接复用。需要新增：

- `tests/fakes.py` 追加 `FakeSQLAgentLLM`：按队列返回 `list[str]`，可配置抛错；
- `FakeExecutor`：按 SQL 映射预设 `(columns, rows)` 或 `error`，覆盖空结果、截断、超时；
- `tests/unit/tools/test_selector.py`：一致 / 不一致 / 空+空 / 空 vs 非空 / 无共识静态分；
- `tests/unit/tools/test_core.py`：修复预算耗尽、升级只一次、候选去重、非法 SQL 预过滤；
- `tests/unit/tools/test_subgraph.py`：子图作为节点接入父图后 `sql / status / error` 不丢（**专门回归本次实测发现的 schema 丢 key 问题**）；
- 全部不联网、不读 `.env`。

## 9. 验收标准

- [ ] `python -m app.tools.sql_agent.build_db` 建出 `data/synthetic/medical.db`，13 张表都有数据，且 `data/sql_examples_sqlite.json` 的 15 条示例全部在该库执行通过。
- [ ] `python -m app.tools.sql_agent.cli "2025年1月门诊人次是多少"` 返回非空 SQL，`verified=true`。
- [ ] 简单问题初始候选 1 条，直接返回（不生成第 2 条）。
- [ ] 复杂问题初始候选 2 条；两条一致时 `llm_calls/executions` 不继续增长。
- [ ] 两条不一致时升级第 3 条；第 3 条与某条一致则返回该条；都不一致则静态分兜底且 `verified=false`。
- [ ] 执行失败的候选触发修正；总修复 ≤ 2；仍失败返回结构化错误，不崩溃。
- [ ] 空结果：空+空算一致（`result_empty=true`）；空 vs 非空算不一致（升级）。
- [ ] `safety.py` 拒绝 `DELETE/UPDATE/DROP`、多语句、非 SELECT；PII 列被标记。
- [ ] 执行器只读、5s 超时、最多 50 行；不连真实库。
- [ ] 子图可被 B 直接接入：`build_sql_subgraph(deps)` 作为 `generate_sql` 节点，接入后父图能收到 `sql / sql_source / sql_assumptions / status / error / trace`，不掉字段（`tests/unit/tools/test_subgraph.py` 回归）。
- [ ] 澄清后的响应含 `schema_links`（表/列/关系），用 `curl POST /queries` 验证（前端本轮不展示）。
- [ ] `app/tools` 不 import `langgraph` / `app/graph`（边界测试通过）。
- [ ] 查询结果行不进 API 响应（trace 只有行数/哈希/耗时）。
- [ ] `docs/SQLITE_AGENT.md` 写清建库、配置、子图、投票/兜底、SQLite 方言限制。

> 本轮验收**不依赖** `data/gold_sql_sqlite.json`（尚未交付）：只验「可执行 + 候选一致 + 字段完整」。gold 结果对比等 A 交付后再补，届时单开一节扩展验收。

## 10. 不做什么

- 不接官方 ReFoRCE、Spider 2.0、Snowflake/BigQuery/GPU。
- `app/tools` 不 import langgraph/langchain（子图包装放在 `app/graph/subgraphs/`）。
- 不做评测、不做 gold SQL 结果对比（A 交付 `gold_sql_sqlite.json` 后再做）。
- 不连真实 Oracle/PostgreSQL；SQLite 只用于本地执行验证。
- 不做复杂 schema linking（schema_links 由 B/A 提供，C 只用于过滤提示）。
- 不做 LLM judge；选择只用结果哈希 + 静态分。
- 不输出查询结果行（PII/泄露风险）。

## 11. 风险与对策

| 风险 | 处理 |
|---|---|
| 第 3 条仍无共识 | 静态分兜底 + `verified=false` + 明确 reason，不无限升级 |
| 空结果被误判 | 空+空算一致但标 `result_empty`；空 vs 非空触发升级 |
| 修复循环不终止 | 单候选最多修 1 次 + 总预算 2；耗尽即返回错误 |
| LLM 评估多花调用 | 默认启发式，`SQL_LLM_EVALUATE=true` 才走 LLM |
| 合成数据 join 为空 | `_id` 列固定范围生成；空结果不当作错误 |
| 结果哈希碰撞 | 采样前 50 行 + 行数 + 截断标记；验收人工抽查 |
| SQLite 与 Oracle 方言差异 | 本轮只做 SQLite；返回给用户的 SQL 是 SQLite 方言，接真实库时需方言切换（文档写明） |
| 查询结果泄露 | 结果只留在子图内；trace 只记行数/哈希/耗时，不回传结果行 |
| gold SQL 未交付 | 验收先以「能执行 + 候选一致」为准；gold 对比后置 |
| 子图与父图 schema 不一致，SQL 被静默丢弃 | 方案 A 共用 `GraphState`，或 wrapper 显式映射；`test_subgraph.py` 回归 |
| 合成数据 join 键不匹配，示例/查询全空 | D1 用 15 条示例在 `medical.db` 上重跑；`_id` 按固定范围生成 |
| 与 B 的 schema 文本分叉 | 优先用 `schema_context.prompt_text`，读 `schema.json` 仅作兜底 |

## 12. 与旧计划的差异（记录）

| 项 | 旧计划 | 新计划 |
|---|---|---|
| 生成方式 | 纯 Python `agent.py` 循环 | LangGraph 子图（`app/graph/subgraphs/sql_generation.py` 包装 `core.py`） |
| 子图 state | —（无子图） | 与父图共用 `GraphState`（或 wrapper 映射），避免输出被静默丢弃 |
| 测试 | 无 | `tests/unit/tools/` + `FakeSQLAgentLLM` / `FakeExecutor`，不联网 |
| schema 文本 | 独立读取 | 优先复用 B 的 `schema_context.prompt_text` |
| 问题评估 | 关键词启发式 | 启发式默认；可选 LLM 评估 |
| 澄清输出 | 只有 `rewritten_question` | `rewritten_question` + `schema_links` |
| 升级规则 | 第 3 条 | 第 3 条 + 无共识静态分兜底 |
| 空结果 | 空+空算一致 | 同上，但显式标 `result_empty` |
| 结果外泄 | 未明确 | 结果只留在子图内，不进 API |
| 候选上限 | 3（复杂先 2） | 同，升级只一次 |
