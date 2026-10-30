# API 文档

Base URL: `http://127.0.0.1:8000`

启动：

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

交互式文档：`http://127.0.0.1:8000/docs`

> 本轮只生成 SQL，**不执行**、不连接数据库。响应里不含查询结果。

## Health

```
GET /health
```

```json
{ "status": "ok", "version": "0.1.0" }
```

## Sessions

### 创建会话

```
POST /sessions
```

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

## Queries

### 提交自然语言查询

```
POST /queries
```

```json
{
  "session_id": "uuid",
  "question": "上个月心内科住院患者平均住院天数是多少？"
}
```

响应（需要澄清）：

```json
{
  "session_id": "uuid",
  "query_id": "uuid",
  "status": "needs_clarification",
  "sql": null,
  "sql_source": null,
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
  "sql_source": "deepseek",
  "clarification": null,
  "assumptions": [
    { "code": "time_range", "label": "自然月", "value": "自然月", "source": "default" }
  ],
  "clarification_history": [
    { "round": 1, "code": "time_range", "question": "…", "answer": {"option_id": "natural_month"}, "resolved": true, "source": "user" }
  ],
  "message": "SQL 已生成，未执行。其中 1 个口径按默认值处理。",
  "error": null,
  "trace": []
}
```

响应（失败）：

```json
{
  "status": "error",
  "sql": null,
  "error": { "code": "sql_generation_failed", "message": "llm_unavailable" },
  "message": "llm_unavailable"
}
```

`status` 取值：`ok` / `needs_clarification` / `error`。**`needs_clarification` 时 `query_id` 必须保留**，下一轮用它 resume。

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

- `skip` 表示"跳过，用默认值"，后端按 `default_option_id` 继续，并在 `assumptions` 里标记 `source=default`。
- 返回结构与 `POST /queries` 完全一致：要么是下一轮的 `clarification`，要么是最终的 `sql`。
- 该 query 已结束时返回 `409`，未知 `query_id` 返回 `404`。

### 查询状态

```
GET /queries/{query_id}
```

```json
{ "session_id": "uuid", "query_id": "uuid", "question": "…", "status": "ok", "sql": "SELECT …" }
```

## 状态机

```
POST /queries
   ├─ 无 blocking 歧义 ───────────────────────▶ ok(sql)
   └─ 有 blocking 歧义 ─▶ needs_clarification
                              │ POST /answers
                              ├─ 还有问题 ─▶ needs_clarification(round+1)
                              └─ 问完/到上限 ─▶ ok(sql)，未消解项进 assumptions
```

轮数上限 = `min(MAX_CLARIFICATION_TURNS, 初始 blocking 问题数)`；每个问题只问一次。

## 配置

后端 `.env`：

```text
LLM_API_KEY=...
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com
MAX_CLARIFICATION_TURNS=3
REFERENCE_DATE=2025-12-31
AMBIGUITY_LLM_SUPPLEMENT=true
CLARIFY_LLM_POLISH=true
```
