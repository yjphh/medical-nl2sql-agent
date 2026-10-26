# API 文档

Base URL: `http://localhost:8000`

## Health

```
GET /health
```

响应：

```json
{ "status": "ok", "version": "0.1.0" }
```

## Sessions

### 创建会话

```
POST /sessions
```

请求体：

```json
{ "user_id": "optional-user-id" }
```

响应：

```json
{ "session_id": "uuid", "user_id": "optional-user-id" }
```

### 获取会话

```
GET /sessions/{session_id}
```

## Queries

### 提交自然语言查询

```
POST /queries
```

请求体：

```json
{
  "session_id": "uuid",
  "question": "上个月心内科住院患者平均住院天数是多少？"
}
```

响应：

```json
{
  "session_id": "uuid",
  "status": "ok | needs_clarification | error | not_implemented",
  "answer": "…",
  "sql": "SELECT …",
  "clarification": "您指的是自然月还是最近30天？",
  "error": null
}
```
