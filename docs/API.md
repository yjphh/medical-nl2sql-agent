# HTTP 接口文档

Base URL：`http://127.0.0.1:8000`

启动：

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

交互式文档：<http://127.0.0.1:8000/docs>

> 本轮只生成 SQL，**不连接真实数据库**。SQL 只在本地 SQLite 镜像上执行，用于自修正和验证可执行性；
> 响应里**不返回查询结果**。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 探测镜像库与知识文件 |
| POST | `/sessions` | 创建会话 |
| GET | `/sessions/{session_id}` | 查询会话 |
| GET | `/metrics` | 指标库检索（前端侧边栏用） |
| POST | `/queries` | 提交自然语言查询 |
| POST | `/queries/{query_id}/answers` | 回答澄清问题 |
| GET | `/queries/{query_id}` | 查询状态 / 断线取回 |

---

## Health

```
GET /health
```

会真的探测 SQLite 镜像与知识文件，而不只是"进程活着"：

```json
{
  "status": "ok",
  "version": "0.1.0",
  "checks": {
    "database": { "ok": true, "path": "data/synthetic/medical.db", "visits": 240 },
    "knowledge": { "ok": true, "tables": 13, "metrics": 35 }
  }
}
```

缺镜像库或知识文件时 `status` 为 `"degraded"`，并附 `message`（含修复命令）：

```json
{
  "status": "degraded",
  "message": "后端未就绪：database 不可用。 修复：.venv\\Scripts\\python.exe -m app.tools.sql_agent.build_db"
}
```

知识文件异常时 `checks.knowledge.detail` 是 `KnowledgeDataError` 原文（缺文件 / 版本不一致 /
引用不存在的列或指标），`message` 里的修复提示指向对应文档。

---

## Sessions

### 创建会话

```
POST /sessions
```

请求体可省略：

```json
{ "user_id": "optional-user-id" }
```

```json
{ "session_id": "uuid", "user_id": "optional-user-id" }
```

### 获取会话

```
GET /sessions/{session_id}
```

会话不存在返回 `404`。

会话、查询索引与澄清进度都存在 SQLite（`data/sessions.db` / `data/checkpoints.db`），
**后端重启后仍可用**；超过 `SESSION_TTL_HOURS`（默认 24h）未更新的记录在启动时清理。

---

## Metrics（只读，供前端指标库）

```
GET /metrics?q=门诊&top_k=50
```

- `q`：可选，在 `id / name / category / definition / aliases` 里做不区分大小写的包含匹配。
- `top_k`：1–200，默认 50。

```json
{
  "items": [
    {
      "id": "outpatient_visits",
      "name": "门诊人次",
      "category": "门急诊情况",
      "aliases": ["门诊量", "门诊人数"],
      "definition": "统计周期内门诊就诊人次",
      "unit": "人次"
    }
  ],
  "total": 35
}
```

---

## Queries

### 提交自然语言查询

```
POST /queries
```

```json
{
  "session_id": "uuid",
  "question": "上个月心内科住院患者平均住院天数是多少？",
  "mode": "guided",
  "request_id": "6f1b8c1e-…"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `session_id` | 是 | `POST /sessions` 返回的 id |
| `question` | 是 | 1–2000 字符 |
| `mode` | 否 | `guided`（默认，走澄清图）/ `direct`（一次模型调用直接出 SQL） |
| `request_id` | 否 | 客户端生成的 UUID，用于幂等重试（见下） |

响应（需要澄清）：

```json
{
  "session_id": "uuid",
  "query_id": "uuid",
  "status": "needs_clarification",
  "sql": null,
  "sql_source": null,
  "sql_confidence": null,
  "sql_result_empty": false,
  "schema_links": [],
  "clarification": {
    "question_id": "q_time_range",
    "code": "time_range",
    "question": "您说的『上个月』是指自然月还是最近30天？",
    "options": [
      { "id": "natural_month", "label": "自然月", "value": "自然月" },
      { "id": "last_30_days", "label": "最近30天", "value": "最近30天" }
    ],
    "default_option_id": "natural_month",
    "round": 1,
    "max_rounds": 2,
    "allow_free_text": true,
    "can_skip": true
  },
  "assumptions": [],
  "clarification_history": [],
  "message": "需要补充信息后才能生成 SQL。",
  "error": null,
  "trace": []
}
```

响应（生成成功）：

```json
{
  "session_id": "uuid",
  "query_id": "uuid",
  "status": "ok",
  "sql": "SELECT COUNT(*) AS cnt FROM visit WHERE activity_type_code = '2' …",
  "sql_source": "sqlite",
  "sql_confidence": 0.9,
  "sql_result_empty": false,
  "schema_links": [
    { "table": "visit", "columns": ["visit_id", "activity_type_code"], "relationship": "visit→patient" }
  ],
  "clarification": null,
  "assumptions": [
    { "code": "time_range", "label": "自然月", "value": "自然月", "source": "default" },
    { "code": "sql", "label": "2 条候选结果一致", "value": "2 条候选结果一致", "source": "llm" }
  ],
  "clarification_history": [
    { "round": 1, "code": "time_range", "question": "…", "answer": { "option_id": "natural_month" }, "resolved": true, "source": "user" }
  ],
  "message": "SQL 已生成，真实库未执行（已在本地 SQLite 镜像校验可执行性）。其中 1 个口径按默认值处理。",
  "error": null,
  "trace": []
}
```

响应（失败）：

```json
{
  "status": "error",
  "sql": null,
  "error": {
    "code": "llm_unavailable",
    "message": "无法连接模型服务，请检查网络或后端配置。",
    "detail": "APIConnectionError: Connection error."
  },
  "message": "无法连接模型服务，请检查网络或后端配置。"
}
```

`status` 取值：`ok` / `needs_clarification` / `error`。**`needs_clarification` 时 `query_id` 必须保留**，
下一轮用它 resume。

`error.code` 是稳定枚举：

| code | 含义 |
|---|---|
| `llm_timeout` | 模型调用超时 |
| `llm_unavailable` | 连接失败或后端未配置 `LLM_API_KEY` |
| `bad_llm_response` | 模型输出不是合法 JSON / 结构不符 |
| `sql_generation_failed` | SQL 生成器没有产出可执行候选（`detail` 为 `sql_no_valid_candidate` / `empty_sql` / LLM 错误原文） |
| `graph_error` | 其他图内异常 |

`message` 是给用户看的中文句子；`detail` 是原始异常文本，前端折叠在「技术详情」里，
**不要直接展示给临床用户**。

`sql_confidence` 只有三档：`0.9`（候选一致，已执行验证）、`0.5`（候选不一致，按静态分选优）、
`0.3`（SQL 可执行但镜像上 0 行）。`sql_result_empty=true` 时前端会标黄提示。

`assumptions` 有两类来源：`source="default"`（跳过/超轮次/最终检查兜底的默认口径）与
`source="llm"`（SQL 生成器附带的说明，包括候选一致性结论与空结果提示）。

### 回答澄清

```
POST /queries/{query_id}/answers
```

三选一（`option_id` 与 `text` 同时传时以 `option_id` 优先）：

```json
{ "answer": { "question_id": "q_time_range", "option_id": "natural_month" } }
```

```json
{ "answer": { "question_id": "q_time_range", "text": "最近 30 天" } }
```

```json
{ "answer": { "question_id": "q_time_range", "option_id": "skip" } }
```

- `skip` 表示"跳过，用默认值"，后端按 `default_option_id` 继续，并在 `assumptions` 里标记
  `source="default"`。
- 返回结构与 `POST /queries` 完全一致：要么是下一轮的 `clarification`，要么是最终的 `sql`。
- 该 query 已结束时返回 `409`，未知 `query_id` 返回 `404`。

### 查询状态 / 断线取回

```
GET /queries/{query_id}
```

返回结构同样是 `QueryResponse`，由 checkpointer 里仍保留的图状态重建：未完成的查询会带回
`clarification`，已完成的会带回 `sql` / `assumptions` / `clarification_history` / `trace`。
前端用它做两件事：

- 回答澄清失败（409 / 后端重启）后回同步，而不是丢掉已答轮次；
- 提示超时或刷新页面后取回结果（`?query=...`）。

> **直连模式（`mode="direct"`）不写 checkpointer**，因此 `GET /queries/{id}` 对该模式只能返回
> `queries` 表里记录的状态与 SQL（`message` 为"已取回后端记录的查询状态（详细口径不可用）"），
> 没有 `assumptions` / `trace`。这是有意的取舍：直连模式只用于对照实验。

会话、查询记录与 graph checkpoint 都存在 SQLite（`data/sessions.db` / `data/checkpoints.db`），
**进程重启后仍能取回**；只有超过 `SESSION_TTL_HOURS` 被清理的记录才返回 `404`。

### 幂等重试

`POST /queries` 支持可选的客户端 `request_id`（UUID）：

```json
{ "session_id": "uuid", "question": "2025年1月急诊人次", "request_id": "6f1b…" }
```

同一个 `request_id` 重复提交不会重跑图，直接返回已有查询的状态——用于超时后重试，
避免同一问题被追问两次或重复消耗 LLM。此时 `query_id` 与 `request_id` 相同。

---

## 状态机

```
POST /queries (guided)
   ├─ 无 blocking 歧义 ───────────────────────▶ ok(sql)
   └─ 有 blocking 歧义 ─▶ needs_clarification
                              │ POST /answers
                              ├─ 还有问题 ─▶ needs_clarification(round+1)
                              └─ 问完/到上限 ─▶ ok(sql)，未消解项进 assumptions

POST /queries (direct)
   └─ 一次模型调用 ─▶ ok(sql) | error
```

轮数预算是 `min(MAX_CLARIFICATION_TURNS, 5)`，与实际待问数**解耦**：问几个由 pending 决定，
预算只封顶。这样"问完第一轮后新出现的歧义"仍有额度可问；超预算或用户跳过的项以默认口径写进最终问题，
并作为 `assumptions` 返回。每个问题只问一次。

---

## 配置

后端 `.env`（全部有默认值，见 `.env.example`）：

```text
LLM_API_KEY=...
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com
LLM_TIMEOUT_SECONDS=30
LLM_MAX_RETRIES=1
MAX_CLARIFICATION_TURNS=3
REFERENCE_DATE=2025-12-31
AMBIGUITY_LLM_SUPPLEMENT=false   # 非阻塞补充项，默认关闭：不提问也不进 prompt
CLARIFY_LLM_POLISH=true
SESSION_DB_PATH=data/sessions.db
CHECKPOINT_DB_PATH=data/checkpoints.db
SESSION_TTL_HOURS=24
SQLITE_DB_PATH=data/synthetic/medical.db
MAX_REPAIR_ROUNDS=2
SQL_LLM_EVALUATE=false
SQL_EXEC_TIMEOUT=5
SQL_MAX_ROWS=50
SQL_MAX_CANDIDATES=3
```
