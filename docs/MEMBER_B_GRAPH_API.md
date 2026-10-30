# 组员 B 开发文档：编排层与 HTTP 层

B 负责把「用户问题」变成「可交给 SQL 生成器的完整问题」，并在需要时暂停下来向用户澄清；
对外通过 FastAPI 暴露会话、查询、澄清回答与断线取回。SQL 生成本身由 C 的子图完成
（见 [MEMBER_C_SQL_AGENT.md](MEMBER_C_SQL_AGENT.md)），知识检索由 A 提供
（见 [MEMBER_A_KNOWLEDGE.md](MEMBER_A_KNOWLEDGE.md)）。

依赖方向：`app/api → app/graph → app/knowledge / app/tools`。B 不直接读写 SQLite 镜像，
也不做静态 SQL 校验。

## 1. 目录

```text
app/
├── main.py                       # FastAPI 入口、/health、启动检查与 TTL 清理
├── config.py                     # pydantic-settings，读取 .env
├── api/
│   ├── runtime.py                # 图与依赖的惰性单例（测试可替换）
│   ├── routes_sessions.py        # POST/GET /sessions
│   ├── routes_queries.py         # POST /queries、answers、GET /queries/{id}
│   ├── routes_knowledge.py       # GET /metrics
│   └── schemas.py                # 请求/响应模型
├── graph/
│   ├── builder.py                # 组装并编译状态图（挂 SQLite checkpointer）
│   ├── state.py                  # GraphState
│   ├── routing.py                # 两个条件路由
│   ├── deps.py                   # GraphDeps：knowledge / llm / settings / sql_llm / sql_executor
│   ├── llm.py                    # DeepSeekLLMClient（图内 LLM）
│   ├── direct.py                 # 直连模式：一次调用出 SQL
│   ├── nodes/
│   │   ├── analyze.py            # 术语/指标/schema 检索 + 明确时间消解
│   │   ├── ambiguity.py          # 歧义策略与轮次预算
│   │   └── finalize.py           # 问题重写 + 最终歧义检查
│   └── subgraphs/
│       ├── clarification.py      # plan → ask(interrupt) → apply
│       └── sql_generation.py     # 包装 C 的 core（另有 state_from_result）
├── prompts/                      # 图内提示词：base / analyze / clarify / ambiguity / sql
└── memory/
    ├── checkpointer.py           # SqliteSaver 单例 + drop_thread
    └── session_repository.py     # sessions / turns / queries + TTL 清理
```

## 2. `GraphState`

`app/graph/state.py` 是父子图共用的唯一 state schema（`TypedDict, total=False`）：

| 分组 | 字段 |
|---|---|
| 标识 | `session_id`、`query_id` |
| 输入 | `original_question`、`rewritten_question` |
| 知识/分析 | `terms`、`metrics`、`selected_metric_id`、`intent`、`schema_context`、`schema_links`、`ambiguities`、`resolved_codes` |
| 澄清 | `pending_codes`、`asked_codes`、`clarification_round`、`max_rounds`、`pending_question`、`last_answer`、`resolved_slots`、`assumptions`、`clarification_history` |
| 最终检查 | `final_ambiguities` |
| SQL 输出 | `sql`、`sql_source`、`sql_assumptions`、`sql_confidence`、`sql_result_empty` |
| 输出 | `status`、`error`、`trace` |

`trace` 用 `Annotated[list, operator.add]` 累积；没有 reducer 时每个节点的 trace 会覆盖前一个，
调试面板只能看到最后一个节点。

## 3. 图组装与路由

```python
# app/graph/builder.py
START → analyze → ambiguity_policy
ambiguity_policy ──pending_codes 非空──▶ clarification ──▶ finalize_question
                 └─否则───────────────▶ finalize_question
finalize_question → final_ambiguity_check
final_ambiguity_check ──有新歧义──▶ clarification
                       └─否则────▶ generate_sql → END
```

路由函数只有两个（`routing.py`）：`route_after_ambiguity` 看 `pending_codes`，
`route_after_final_check` 看 `final_ambiguities`。

编译时挂 `get_checkpointer()`，`thread_id` 由 API 传 `query_id`。

## 4. 节点

### 4.1 `analyze`

1. `resolve_terms` / `search_metrics` / `get_schema_context` / `detect_ambiguities` 各一次。
2. 指标选择：候选唯一直接用；多于一个时调一次 `deps.llm.choose_metric`（失败则回退第一个）。
3. 自行消解两类歧义：
   - 选中了指标 → `metric_scope` 已解决（注意：多候选时也只记"已解决"，见 TEST_QUESTIONS 的 K6）；
   - 问题中出现 `20xx年M` / `20xx-M` / `20xx/M` → `time_range` 已解决。
4. 写 `terms`、`metrics`、`selected_metric_id`、`schema_context`、`ambiguities`、`resolved_codes`、
   `intent`、`trace`。

### 4.2 `ambiguity_policy`

- 只在 `settings.ambiguity_llm_supplement=true` 时调用 LLM 补充歧义；补充项固定 `blocking=False`，
  既不提问也不进 SQL prompt，所以默认关闭。
- pending = `blocking` 且未 `asked` 且未 `resolved` 的项，按 `(priority, code)` 排序。
- `max_rounds = min(max(0, MAX_CLARIFICATION_TURNS), HARD_TURN_LIMIT=5)`。
- 有 pending 时在这里写 `status="needs_clarification"`——子图在 `interrupt` 暂停期间父图拿不到
  子图写入，API 必须靠父图 state 回应。

### 4.3 澄清子图 `clarification.py`

```
START → plan → ask → apply ─┬─ 预算未用完且还有 pending ─▶ plan
                            └─ 否则 ─▶ END
```

- **plan**：取剩余 pending 的第一项，生成 `pending_question`：
  `{id: "q_<code>", code, question, options, default_option_id, allow_free_text, can_skip, round, max_rounds}`。
  `max_rounds` 显示为 `min(预算, 当前轮 + 剩余题数 - 1)`，卡片上的「1/2」是实际会问的题数。
  选项 id 由 `OPTION_IDS` 固定映射（如 `time_range` → `natural_month / last_30_days`），
  `metric_scope` 直接用指标 id。`POLISH_CODES` 且 `CLARIFY_LLM_POLISH=true` 时，
  用 LLM 润色问法与选项文案（只改 label，不改 id/value，选项数量必须一致，否则整次润色丢弃）。
- **ask**：`interrupt(state["pending_question"])` 挂起；用户回答后返回 `last_answer`。
- **apply**：
  - `option_id` 命中选项 → 采用；否则有自由文本 → 采用文本；否则用默认项并标 `source="default"`；
  - 写 `resolved_slots[code]`、`resolved_codes`、`asked_codes`、`clarification_round+1`、
    `assumptions`（仅默认）、`clarification_history`；
  - `metric_scope` 的答案同时更新 `selected_metric_id`。
- **路由**：`clarification_round >= max_rounds` 或没有 pending → `done`。
- pending 每轮重算而不是沿用初始 `pending_codes`：一个回答可能同时消解多个 code
  （例如答了 `visit_type_scope` 就连带 `metric_scope`）。

### 4.4 `finalize_question`

把已答槽位写进重写问题，让模型看到明确口径：

- `time_range`：按 `REFERENCE_DATE` 渲染成具体区间（自然月 → 上月 1 日到本月 1 日，不含结束日；
  最近 30 天 → 参考日往前 30 天到参考日）；
- 指标名、就诊类型、科室名（从 `terms` 里取 `visit.dept_name` 的匹配词）、医保范围各有专门句子；
- 其余槽位（`time_field_scope`、`patient_scope`、`aggregation`、`fee_type`）统一走 `SLOT_LABELS`，
  以免答了却被静默丢弃。

重写后**重新计算** `schema_context`，并由它展开 `schema_links`：

```json
[{ "table": "visit", "columns": ["visit_id", "activity_type_code"], "relationship": "" }]
```

### 4.5 `final_ambiguity_check`

- 只处理**新出现**的 blocking code（不在 `ambiguities`、`asked_codes`、`resolved_codes` 中）。
- 有新增且 `clarification_round < max_rounds` → 合并进 `ambiguities`，回澄清子图。
- 否则把新项的默认值写进 `assumptions`（`reason="max_rounds"`），并把
  「未澄清项按默认口径：…」追加到重写问题。**只放 assumptions 模型看不到**，会重新猜一个口径。

### 4.6 `generate_sql`

`subgraphs/sql_generation.py` 把 C 的 `generate_sql` 包成单节点子图：

- schema 文本优先用父图 `schema_context.prompt_text`，缺失时才让知识层/`schema.json` 兜底；
- 示例优先用 `deps.knowledge.search_sql_examples`；
- `llm` / `executor` 在**编译期**解析（`deps.sql_llm/sql_executor` → 真实客户端），
  测试注入 fake，编译后的图可复用；
- `state_from_result` 把 `GenerationResult` 映射为 `sql / sql_source / sql_assumptions /
  sql_confidence / sql_result_empty / status / error / trace`，置信度规则只在此处定义。
  直连模式复用同一函数。

## 5. 直连模式 `direct.py`

`mode="direct"` 时 API 不调用图，直接 `run_direct(question, deps)`：

- 一次 `generate_candidates(n=1)`，`max_candidates=1`、`max_repair_rounds=0`、不升级；
- 仍经过 `safety.check_sql` 与 SQLite 执行验证，返回结构与引导模式一致；
- `trace[0].node="direct_sql"` 且带 `"mode":"direct"`；
- 不写 checkpointer，`GET /queries/{id}` 只能回 `queries` 表的降级信息（见 API.md）。

## 6. HTTP 层

### 6.1 运行时单例

`app/api/runtime.py` 惰性构建 `GraphDeps`（加载知识、构造 DeepSeek 客户端）与编译后的图；
`set_deps/set_graph/reset` 供测试替换。首个请求会慢几秒，属正常。

### 6.2 路由

| 方法 | 路径 | 要点 |
|---|---|---|
| GET | `/health` | 真探测镜像库（`SELECT COUNT(1) FROM visit`）与知识加载；失败给 `fix` 命令 |
| POST | `/sessions` | 生成 uuid，写 `sessions` 表 |
| GET | `/sessions/{id}` | 不存在 404 |
| GET | `/metrics` | 在 id/name/category/definition/aliases 上包含匹配，`top_k` 1–200 |
| POST | `/queries` | `request_id` 幂等；`mode` 分支；同步执行图 |
| POST | `/queries/{id}/answers` | 非 `needs_clarification` → 409；未知 id → 404 |
| GET | `/queries/{id}` | 由 checkpointer 重建完整响应 |

所有执行图的端点都是 `def` 而不是 `async def`：图调用是阻塞的，FastAPI 会把它放进线程池，
否则一次慢查询会卡住 `/health` 和事件循环。

### 6.3 幂等与恢复

- `request_id` 已存在 → 不重跑图，直接 `_recovered_response` 返回已有记录。
- `_recovered_response`：从 checkpointer 取 `values` 与 `interrupts`；有 interrupt 时把它塞回
  `__interrupt__` 再走 `_to_response`，所以恢复出来的响应和原响应同构。
- checkpointer 不可读时降级为 `queries` 表里的 `status + sql`。

### 6.4 错误映射 `_friendly_error`

| 异常特征 | `error.code` | 用户文案 |
|---|---|---|
| 类名/文本含 timeout | `llm_timeout` | 模型调用超时，请稍后重试。 |
| 类名/文本含 connect/connection | `llm_unavailable` | 无法连接模型服务，请检查网络或后端配置。 |
| `LLMError` 且 `missing_api_key` | `llm_unavailable` | 后端未配置模型密钥，请检查 .env 中的 LLM_API_KEY。 |
| 其他 `LLMError` / `ValueError` / `KeyError` | `bad_llm_response` | 模型返回内容无法解析，请重试或换一种问法。 |
| 其他 | `graph_error` | 生成过程出错，请稍后重试。 |

原始异常文本放 `error.detail`，前端折叠在「技术详情」里，不直接展示给用户。

## 7. 持久化

### 7.1 `app/memory/checkpointer.py`

- `SqliteSaver`（`data/checkpoints.db`），`check_same_thread=False` + WAL + 30s timeout，
  因为 FastAPI 从线程池调用；
- 未安装 `langgraph-checkpoint-sqlite` 时回退 `MemorySaver` 并打印警告（重启丢失澄清进度）；
- `drop_thread(query_id)` 删除过期查询的 checkpoint。

### 7.2 `app/memory/session_repository.py`

- 三张表：`sessions`、`turns`（每个会话最多回读 50 条）、`queries`；
- `create_query` 同时创建/更新会话；`append_turn` 记用户问题与助手结论；
- `prune()` 删除 TTL 之外的 sessions/turns/queries，并返回过期 query id 供 `drop_thread`；
- 启动时 `main.prune_expired()` 执行一次。

## 8. 配置（B 相关）

| 配置 | 默认 | 作用 |
|---|---|---|
| `MAX_CLARIFICATION_TURNS` | 3 | 澄清轮次预算，硬上限 5 |
| `REFERENCE_DATE` | 2025-12-31 | 「上个月/最近30天」的参照日 |
| `AMBIGUITY_LLM_SUPPLEMENT` | false | 是否让 LLM 补充非阻塞歧义（只花钱，不提问） |
| `CLARIFY_LLM_POLISH` | true | 是否润色澄清问法 |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | 30 / 1 | 图内 LLM 单次超时与重试 |
| `SESSION_DB_PATH` / `CHECKPOINT_DB_PATH` | data/*.db | 持久化位置 |
| `SESSION_TTL_HOURS` | 24 | 会话与查询保留时长 |

## 9. 测试

```powershell
.venv\Scripts\python.exe -m pytest tests\unit\graph tests\unit\memory tests\integration -q
```

- `tests/unit/graph/test_graph_flow.py`：0/1/2 轮澄清、跳过用默认值、trace 累积、直连分支；
- `tests/unit/memory`：checkpointer 落盘与线程删除、会话仓库 TTL；
- `tests/integration`：FastAPI 路由（`request_id` 幂等、409/404、`mode=direct`、`schema_links`）；
- 所有 LLM 都用 `tests/fakes.py::FakeLLMClient`，SQL 生成用 `FakeSQLAgentLLM`/`FakeExecutor`，
  不联网、不需要 API key。
