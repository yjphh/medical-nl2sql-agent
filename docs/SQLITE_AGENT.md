# SQLite SQL 生成子图（组员 C）

> 状态：D1–D4 已完成。`app/tools/sql_agent/` 提供纯 Python 核心，`app/graph/subgraphs/sql_generation.py` 把它包成 LangGraph 子图，供 B 的 `generate_sql` 节点直接使用。
>
> 本轮只生成 **SQLite SQL**，并在本地 SQLite 镜像上执行验证；**不连接真实生产库**，查询结果行不会离开进程。

## 1. 目录与依赖方向

```text
app/tools/sql_agent/          # 纯 Python，不 import langgraph / app.graph
├── build_db.py               # schema.json -> data/synthetic/medical.db + 合成数据
├── prompt.py                 # schema 文本、评估/生成/修复 prompt
├── safety.py                 # sqlglot 静态检查：单语句 SELECT、表存在、PII
├── executor.py               # 只读执行（mode=ro + query_only + 超时 + 行数上限）
├── llm.py                    # OpenAI-compatible LLM（n 候选 / repair / evaluate）
├── selector.py               # 结果哈希、多数投票、静态打分
├── core.py                   # 评估 -> 生成 -> 执行 -> 修复 -> 投票
└── cli.py                    # 命令行演示

app/graph/subgraphs/sql_generation.py   # 子图包装层（可 import langgraph）
```

依赖方向：`app/graph → app/tools`。`app/tools` 不得反向 import `app/graph` / `app/api`，由 `tests/unit/tools/test_import_boundaries.py` 守护。

## 2. 构建 SQLite 镜像

```powershell
.venv\Scripts\python.exe -m app.tools.sql_agent.build_db
```

- 输入：`data/schema.json`（13 表）→ 输出 `data/synthetic/medical.db`；
- 类型映射：`INTEGER / NUMERIC / TEXT / BLOB`；日期时间以 `TEXT` 存储，字符串比较；
- 合成数据固定 `seed=42`，患者/就诊 ID 共享池，保证 join 有结果；
- PII 列写假值（如 `id_card` 为 `110101199001010000` 形式）；
- 生成后自动把 `data/sql_examples_sqlite.json` 的 15 条示例全部执行一遍，失败返回非 0。

`data/synthetic/` 已在 `.gitignore`，镜像不入库。

## 3. 命令行

```powershell
.venv\Scripts\python.exe -m app.tools.sql_agent.cli "2025年1月门诊人次是多少"
.venv\Scripts\python.exe -m app.tools.sql_agent.cli "上个月检查项目排名" --n 2 --show-candidates
```

输出：最终 SQL、`verified`、`result_empty`、选择理由、统计（`llm_calls / executions / repairs / elapsed_ms`）。`--show-candidates` 额外打印每个候选的安全/执行状态。

真实调用需要 `.env` 里的 `LLM_API_KEY / LLM_MODEL / LLM_BASE_URL`。

## 4. 配置

`app/config.py` 与 `.env.example`：

| 配置 | 默认 | 说明 |
|---|---|---|
| `SQLITE_DB_PATH` | `data/synthetic/medical.db` | 镜像库路径 |
| `MAX_REPAIR_ROUNDS` | `2` | 修复总预算（单候选最多修 1 次） |
| `SQL_LLM_EVALUATE` | `false` | `true` 时用 LLM 评估候选数；默认关键词启发式 |
| `SQL_EXEC_TIMEOUT` | `5` | 单条 SQL 执行超时（秒） |
| `SQL_MAX_ROWS` | `50` | 每次执行最多取多少行（用于哈希投票） |
| `SQL_MAX_CANDIDATES` | `3` | 候选上限（1/2/3） |

## 5. 子图流程

```text
START -> generate -> END   （子图内只有一个节点，内部走 core.generate_sql）
```

`core.generate_sql` 的步骤：

1. **evaluate**：关键词启发式决定初始候选数 1 或 2（可选 LLM）；
2. **generate**：一次结构化调用生成 N 条候选；归一化去重；
3. **safety**：sqlglot 解析，拒绝非 SELECT、多语句、未知表、PII 直接输出；
4. **execute**：只读执行，记录行数/哈希/是否截断；
5. **repair**：执行失败的候选回传错误修正，共享预算，单候选最多 1 次；
6. **select**：
   - 1 条有效候选 → 直接返回，`verified=true`；
   - 多条结果哈希多数一致 → 返回多数组内静态分最高者；
   - 不一致且初始候选 ≥2 → 升级生成第 3 条（只升一次）；
   - 第 3 条仍无共识 → 静态分选优，`verified=false`；
   - 全部非法/失败 → 返回 `sql_no_valid_candidate`。

空结果规则：空+空算一致（`result_empty=true`）；空 vs 非空算不一致，触发升级。

静态分：安全 > 执行成功 > join 少 > SQL 短 > LLM 自评置信度。

## 6. 与父图（B）的接口

子图与父图**共用 `GraphState`**（避免 LangGraph 静默丢弃子图输出 key），只写以下字段：

| 字段 | 值 |
|---|---|
| `sql` | 最终 SQL；失败为 `None` |
| `sql_source` | 固定 `"sqlite"` |
| `sql_assumptions` | 候选假设 + 选择理由 + 空结果提示 |
| `sql_confidence` | `verified ? 0.9 : 0.5` |
| `status` | `"ok"` / `"error"` |
| `error` | 失败时 `{"code": "sql_generation_failed", "message": ...}` |
| `trace` | 只含 `ok / candidates / verified / result_empty / reason / result_hash / 统计`，**不含结果行** |

输入：

- `rewritten_question`（优先）或 `original_question`；
- `schema_context.prompt_text`（优先；没有时才回退到 `data/schema.json`）；
- `schema_links`（仅提示，不替代全量 schema）。

`app/graph/nodes/finalize.py` 在算出 `schema_context` 后生成 `schema_links`：

```json
[{"table": "visit", "columns": ["visit_id", "activity_type_code"], "relationship": ""}]
```

`QueryResponse` 已加 `schema_links` 字段；`curl POST /queries` 可看到。前端本轮不展示。

### 注入与替换

```python
build_sql_subgraph(deps, llm=..., executor=..., examples=..., allowed_tables=...)
```

- 不传时用 `deps.sql_llm` / `deps.sql_executor`（测试注入），再回退到 `settings` 构造真实客户端；
- `GraphDeps` 新增了两个可选字段 `sql_llm` / `sql_executor`，默认 `None`，不影响现有构造。

旧的 `app/graph/nodes/sql_generation.py`（单次 DeepSeek 调用）保留但不再接入主图；旧 `app/tools/sql_generator/` 同样保留作历史记录。

## 7. 安全与 PII

- 只读连接：`file:{path}?mode=ro` + `PRAGMA query_only=ON`；
- 超时：`set_progress_handler`，默认 5s；
- 行数：`fetchmany(max_rows + 1)`，超出标记 `truncated`；
- 静态检查：`DELETE/UPDATE/DROP/PRAGMA/多语句` 直接拒绝、不执行；
- 未知表拒绝（`allowed_tables` 来自 `schema.json`）；
- `SELECT *` 视为 PII 风险；
- PII 列只允许出现在 `COUNT(DISTINCT ...)` 内（沿 AST 父链判断是否在 `Count` 下）；
- 结果行只留在 `core` 内用于哈希，**不写入 state / trace / API**。

已知限制：PII 检查只看最外层 SELECT 的投影。若 PII 列在 CTE/子查询中先被选中、再在外部被 `COUNT(*)`，不会被标记（最终输出仍只是计数）。接真实库前建议补一轮 AST 追踪。

## 8. SQLite 方言限制

- 只生成单条 `SELECT` / `WITH ... SELECT`；
- 可用：`IFNULL/COALESCE`、`strftime`、`||`、`LIMIT`、`CASE WHEN`；
- 禁止：`NVL`、`TO_CHAR`、`ROWNUM`、`DATE_FORMAT`、反引号、`CONCAT` 多参；
- 日期时间存为 `TEXT`，直接字符串比较；
- 返回的 SQL 是 SQLite 方言，接 Oracle/达梦等真实库时需另行方言转换。

## 9. 测试

```powershell
.venv\Scripts\python.exe -m pytest tests\unit\tools -q
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe -m ruff check app tests
```

覆盖：

- `test_safety.py`：15 条示例全 safe；写操作/多语句/PRAGMA/未知表拒绝；PII 仅 COUNT 内允许；
- `test_build_db.py`：13 表有数据、示例全执行、join 键无孤儿、执行器只读；
- `test_selector.py`：哈希忽略行序/别名、多数投票、升级、空结果、无共识静态分；
- `test_core.py`：单候选、修复、PII 预过滤不执行、升级第 3 条、无共识、空结果、预算耗尽、去重；
- `test_subgraph.py`：子图接入父图后 `sql / sql_source / sql_assumptions / status / error / trace` 不丢，结果行不外泄；
- `tests/unit/graph`、`tests/integration`：澄清流程走通，API 响应含 `schema_links`。

全部使用 fake LLM / fake executor，不联网、不需要 API key。
