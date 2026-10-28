# 架构

## 总览

Medical NL2SQL Agent 基于 LangGraph 构建有状态工作流，将自然语言问题转换为 SQL 并生成可解释答案。

```
用户问题
   │
   ▼
┌────────────────┐
│ intent_schema  │  意图识别 + schema/metric 上下文检索
└───────┬────────┘
        ▼
┌────────────────┐
│   ambiguity    │  歧义检测（时间/指标口径/实体）
└───────┬────────┘
        │ 有歧义
        ▼
┌────────────────┐     用户回答      ┌──────────────┐
│ clarification  │ ◄─────────────── │ 会话历史更新  │
└───────┬────────┘                  └──────────────┘
        │ 无歧义
        ▼
┌────────────────┐
│ sql_generation │  SQL 生成（Reforce/LLM adapter）
└───────┬────────┘
        ▼
┌────────────────┐
│  validation    │  只读校验、表/列校验、超时
└───────┬────────┘
        │ valid
        ▼
┌────────────────┐
│ answer_merger  │  结果归纳为自然语言答案
└────────────────┘
```

## 模块

| 模块 | 职责 |
|------|------|
| `app/api` | FastAPI 路由与请求/响应 schema |
| `app/graph` | LangGraph 状态、节点、路由 |
| `app/knowledge` | Schema/Metric 目录与加载 |
| `app/tools` | SQL 生成器适配器与校验器 |
| `app/memory` | Checkpointer 与会话仓库 |

## 设计原则

- **安全优先**：SQL 校验默认拒绝非 SELECT 语句，生产建议只读账号。
- **可澄清**：歧义与校验失败均可路由到 clarification 节点，支持多轮对话。
