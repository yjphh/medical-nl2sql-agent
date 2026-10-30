# 组员 C 开发文档：SQL 生成器

C 负责把「澄清后的完整问题 + schema 上下文」变成一条通过静态安全校验、并在本地 SQLite 镜像上执行成功的
SQL。核心是 `app/tools/sql_agent/`（纯 Python，不 import `langgraph` / `app/graph` / `app/api`），
由 B 的 `app/graph/subgraphs/sql_generation.py` 包成图节点。

**本轮只生成 SQLite 方言 SQL，不连接真实生产库，不输出查询结果行。**

## 1. 目录与依赖方向

```text
app/tools/sql_agent/
├── build_db.py     # data/schema.json → data/synthetic/medical.db（合成数据）
├── prompt.py       # SQLite 系统提示词、评估/生成/修复消息、schema 渲染兜底
├── safety.py       # sqlglot 静态检查：单语句 SELECT、表白名单、PII / SELECT *
├── executor.py     # 只读执行：mode=ro + query_only + 超时 + 行数上限
├── llm.py          # OpenAI-compatible 客户端（生成 N 条候选 / 修复 / 难度评估）
├── selector.py     # 结果哈希、多数投票、静态打分、升级判定
└── core.py         # evaluate → generate → execute → repair → select

app/graph/subgraphs/sql_generation.py   # 子图包装 + state_from_result
app/graph/direct.py                     # 直连模式复用同一 core
```

依赖方向：`app/graph → app/tools`，反向禁止，由 `tests/unit/tools/test_import_boundaries.py` 守护。
`core.generate_sql` 是纯函数：`llm` 与 `executor` 都由调用方注入，测试传 fake，离线可跑。

## 2. 构建 SQLite 镜像

```powershell
.venv\Scripts\python.exe -m app.tools.sql_agent.build_db
```

- 输入 `data/schema.json`（13 表）→ 输出 `data/synthetic/medical.db`；
- 类型映射到 `INTEGER / NUMERIC / TEXT`，日期时间用 `TEXT` 存储、字符串比较；
- 固定 `seed=42`：60 名患者、240 条就诊，就诊时间分布在 `REFERENCE_DATE` 前 12 个月；
- 关联键从共享 ID 池生成，保证 join 有结果；PII 列写明显假值；
- 科室只有 4 个（内科 75 / 儿科 57 / 外科 56 / 妇产科 52）；
- 生成后自动执行 `data/sql_examples_sqlite.json` 的全部 15 条示例，任何一条失败则返回非 0。

`data/synthetic/` 被 `.gitignore` 忽略，新环境必须重跑。

## 3. 核心流程 `core.generate_sql`

```python
generate_sql(question, schema_context, *, llm, executor, examples=None,
             schema_links=None, n=None, llm_evaluate=False,
             max_candidates=3, max_repair_rounds=2, allowed_tables=None) -> GenerationResult
```

1. **evaluate**：`n = n or (LLM 评估 if SQL_LLM_EVALUATE else estimate_n(question))`，
   夹在 `[1, max_candidates]`。启发式：含「排名/占比/比例/同比/环比/平均/各/每/趋势/top」→ 2；
   含「和/与/对比/分别」或问题长度 >12 → 2；否则 1。
2. **generate**：一次调用让模型返回 N 条结构化候选（`{"candidates":[{sql, assumptions, confidence}]}`）。
   `_prepare` 做归一化去重（空白折叠、小写、去尾分号）+ `safety.check_sql` 静态检查。
3. **execute**：只跑安全候选，记录 `row_count / truncated / error / result_hash`。
4. **repair**：对「安全但执行失败」的候选回传错误修正，`max_repair_rounds` 是共享预算，
   单候选最多修 1 次（预算按候选顺序消耗）；修复结果同样要去重、静态检查、执行。
   修复 prompt 只带 SQL 与错误文本，**不回传结果行**。
5. **select**：见 §4。

`GenerationResult` 字段：`sql / verified / candidates / reason / result_empty / error / stats /
trace`。`stats` 只有 `llm_calls / executions / repairs / elapsed_ms`。

失败时 `sql=None`，`error` 为 `empty_sql`、`sql_no_valid_candidate` 或 LLM 错误原文；
子图把后者统一映射成 `sql_generation_failed`。

## 4. 选择与置信度 `selector.py`

`select(candidates, allow_escalation)`：

| 情形 | 结果 |
|---|---|
| 没有安全且执行成功的候选 | `chosen=None`（子图返回 error） |
| 只有 1 条有效候选 | 直接选用，`verified=True` |
| 多条且某个结果哈希占多数 | 返回该组内静态分最高者，`verified=True` |
| 不一致且允许升级（初始候选 ≥2） | `needs_escalation=True`，core 再生成 1 条后重选（只升一次） |
| 仍无共识 | 静态分选优，`verified=False` |

- **结果哈希**：行值转字符串 → 排序 → 连同 `truncated` 一起 sha256 取前 16 位。忽略列别名与行序。
- **静态分**：安全 > 执行成功 > JOIN 少 > SQL 短 > 模型自评置信度。
- **空结果**：空 + 空算一致（`result_empty=True`）；空 vs 非空算不一致，触发升级。
- 升级只发生一次，`allow_escalation=False` 的第二轮不会再升级。

子图的置信度映射（`state_from_result`）：

```
result_empty          → 0.3
verified              → 0.9
否则（无共识/静态分）  → 0.5
```

空结果不降级为 error，但 `sql_result_empty=True` 会一路传到 API 与前端告警。

## 5. 静态安全 `safety.check_sql`

```python
check_sql(sql, *, allowed_tables=None, pii_columns=None) -> SafetyResult
```

- sqlglot 按 SQLite 方言解析；解析失败 / 多语句 / 非 `SELECT`、`UNION` → `safe=False`；
- 表名必须全部在 `allowed_tables` 内，CTE 名不算表；
- **PII**：`schema.json` 中 `is_pii` 的列名不得出现在最外层 SELECT 的投影里，
  也不得出现在 `GROUP BY / ORDER BY / HAVING`；沿 AST 父链判断列是否在 `COUNT(...)` 内，
  在 `COUNT(DISTINCT pii)` 里允许；
- `SELECT *` 与 `t.*` 一律拒绝（`select_star_pii_risk`）；
- `UNION` 的每个分支都会被检查。

拒绝理由（`reason`）：`empty_sql`、`parse_error`、`multiple_statements`、`only_select_allowed`、
`unknown_tables`、`pii_in_select`、`select_star_pii_risk`。

已知限制：PII 检查只看最外层输出；PII 若在 CTE/子查询里被选中、外层只做 `COUNT(*)`，不会被标记
（最终输出仍只是计数）。接真实库前建议补一轮 AST 追踪。

## 6. 只读执行 `executor.py`

- `file:{path}?mode=ro` + `PRAGMA query_only=ON`，库不存在时返回 `database_not_found`；
- `set_progress_handler` 每 10k 条 VM 指令检查一次 deadline（默认 5s，`SQL_EXEC_TIMEOUT`）；
- `fetchmany(max_rows + 1)`，超出则 `truncated=True`（默认 50 行，`SQL_MAX_ROWS`）；
- 错误作为返回值（`ExecutionResult.error`），不抛异常；
- 结果行只留在进程内用于哈希，不写 state / trace / API / 日志。

## 7. Prompt 与 LLM 客户端

`prompt.py`：

- `SQLITE_SYSTEM_PROMPT`：只生成一条 SELECT/WITH、SQLite 语法（禁 `NVL/TO_CHAR/ROWNUM/DATE_FORMAT`）、
  时间列字符串比较、`modify_flag IN ('1','2')`、显式 JOIN、PII 不得输出、口径不确定写进 assumptions；
- `build_generate_messages`：schema 上下文 + 模式链接（仅提示）+ few-shot 示例 + 问题 + 输出 JSON 契约；
- `build_repair_messages`：schema + 示例 + 问题 + 失败 SQL + 错误信息；
- `build_evaluate_messages`：一次难度评估；
- `schema_text(load_schema())` 是**兜底**：父图没给 `schema_context.prompt_text` 时才用，
  按列重要性给每张表分配字符预算（主键 → join 键 → 枚举 → 日期/金额 → PII → 其他）。

`llm.py::OpenAICompatLLM`：

- 懒加载 OpenAI 客户端，缺 key 抛 `LLMError("missing_api_key: …")`；
- 生成 temperature 0.7，修复/评估 0.0；
- 解析时会剥 ```json 代码块、截取最外层 `{...}`；非法 JSON 抛 `LLMError`；
- 只负责"发 prompt + 解析 JSON"，投票/预算等决策都在 `core`。

## 8. 与父图的接口

子图与父图**共用 `GraphState`**，只写以下字段（父图没有的 key 会被 LangGraph 静默丢弃）：

| 字段 | 值 |
|---|---|
| `sql` | 最终 SQL；失败为 `None` |
| `sql_source` | 固定 `"sqlite"` |
| `sql_assumptions` | 选中候选的假设 + 一致性结论 + 空结果提示（中文短句） |
| `sql_confidence` | 0.9 / 0.5 / 0.3 |
| `sql_result_empty` | 是否命中 0 行 |
| `status` | `"ok"` / `"error"` |
| `error` | `{"code": "sql_generation_failed", "message": …}` |
| `trace` | 候选数、verified、result_empty、reason、result_hash、统计（**不含结果行**） |

输入：`rewritten_question`（优先）或 `original_question`；`schema_context.prompt_text`（优先）；
`schema_links` 仅作提示；示例来自知识层 `search_sql_examples`。

`build_sql_subgraph(deps, *, llm=None, executor=None, examples=None, allowed_tables=None)`：
不传时用 `deps.sql_llm` / `deps.sql_executor`（测试注入），再回退到按 `settings` 构造真实客户端；
`allowed_tables` 来自 `schema.json` 的表名。

## 9. 配置

| 配置 | 默认 | 说明 |
|---|---|---|
| `SQLITE_DB_PATH` | data/synthetic/medical.db | 镜像库路径 |
| `MAX_REPAIR_ROUNDS` | 2 | 修复总预算（共享） |
| `SQL_LLM_EVALUATE` | false | 是否用 LLM 评估候选数 |
| `SQL_EXEC_TIMEOUT` | 5 | 单条 SQL 执行超时（秒） |
| `SQL_MAX_ROWS` | 50 | 每次执行取多少行（用于哈希） |
| `SQL_MAX_CANDIDATES` | 3 | 候选上限 |

LLM 复用 `LLM_API_KEY / LLM_MODEL / LLM_BASE_URL`。

## 10. 成本控制

1. 默认启发式评估，不额外调 LLM；
2. 两条候选一致即停，不升级第三条；
3. 只有"安全但执行失败"的候选才进修复，空结果不修；
4. 去重后再执行；非法 SQL 静态拒绝，不浪费执行；
5. 结果哈希只采样前 50 行 + 行数 + 截断标记；
6. 统计 `llm_calls / executions / repairs / elapsed_ms` 写进 trace，便于对比。

## 11. 方言限制

- 只生成单条 `SELECT` / `WITH ... SELECT`；
- 可用：`IFNULL/COALESCE`、`strftime`、`||`、`LIMIT`、`CASE WHEN`；
- 禁止：`NVL`、`TO_CHAR`、`ROWNUM`、`DATE_FORMAT`、反引号、多参 `CONCAT`；
- 返回的 SQL 是 SQLite 方言，接 Oracle/达梦等真实库时需另行方言转换。

## 12. 测试

```powershell
.venv\Scripts\python.exe -m pytest tests\unit\tools -q
```

| 文件 | 覆盖 |
|---|---|
| `test_safety.py` | 15 条示例全 safe；写操作/多语句/未知表拒绝；PII 仅在 COUNT 内允许；`t.*`、UNION 分支 |
| `test_build_db.py` | 13 表有数据、示例全部可执行、join 键无孤儿、执行器只读 |
| `test_selector.py` | 哈希忽略行序/别名、多数投票、升级、空结果、无共识静态分 |
| `test_core.py` | 单候选、修复、非法 SQL 预过滤不执行、升级第 3 条、无共识、空结果、预算耗尽、去重 |
| `test_subgraph.py` | 子图接入父图后字段不丢、结果行不外泄 |
| `test_import_boundaries.py` | `app/tools` 不 import `langgraph` / `app/graph` |

全部使用 `FakeSQLAgentLLM` / `FakeExecutor`，不联网、不需要 API key。
