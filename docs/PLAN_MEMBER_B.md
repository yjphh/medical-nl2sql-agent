# 组员 B 开发计划：澄清工作流 + FastAPI

> 目标：把用户问题变成 SQL。B 负责意图识别、schema linking、歧义检测、多轮澄清、问题重写、SQL 生成（当前用 DeepSeek 直接生成）和 HTTP 接口。
>
> 技术栈：LangChain（模型）+ LangGraph（状态图、interrupt、checkpointer）+ FastAPI。
>
> 已确认：澄清**一次只问一个问题**；`thread_id = query_id`；MVP **直接做澄清子图**；LLM 客户端用 `langchain_deepseek.ChatDeepSeek`。
>
> **当前实施版本（C 未就绪）**：图只到 `generate_sql`，不接 `CAdapter`、`validate_sql`、`sql_repair`、`answer_merger`；`generate_sql` 只做一次 DeepSeek 调用；`clarify_polish` 放在 `clarify_plan` 内部；轮数按 pending 数量动态确定。C 就绪后再把校验/修复加回来。

## 0. 实现进度（2026-10-30）

| 模块 | 文件 | 状态 |
|---|---|---|
| 状态 | `app/graph/state.py` | ✅ `GraphState` 全字段 |
| 依赖注入 | `app/graph/deps.py` | ✅ `GraphDeps` + `build_deps()` |
| LLM 客户端 | `app/graph/llm.py` | ✅ `DeepSeekLLMClient`（`ChatDeepSeek(base_url=...)`，懒加载，无 key 也能 import） |
| 意图/schema linking | `app/graph/nodes/analyze.py` | ✅ 规则优先，多候选才调 LLM |
| 歧义策略 | `app/graph/nodes/ambiguity.py` | ✅ 规则定 blocking，LLM 只补 `blocking=false` 的语义歧义；`max_rounds = min(3, len(pending))` |
| 澄清子图 | `app/graph/subgraphs/clarification.py` | ✅ `plan(含润色) → ask(interrupt) → apply → route_more`，每轮重新计算 pending |
| 问题重写 | `app/graph/nodes/finalize.py` | ✅ 确定性模板；所有已答 slot 都写进重写问题；预算用尽时把默认值写进 `assumptions` |
| SQL 生成 | `app/graph/nodes/sql_generation.py` | ✅ 一次 DeepSeek 调用，失败返回 `status=error` |
| 图装配 | `app/graph/builder.py` | ✅ `compile(checkpointer=MemorySaver)` |
| API | `app/api/*`、`app/main.py` | ✅ `POST /sessions`、`POST /queries`、`POST /queries/{id}/answers`、`GET /queries/{id}` |
| 测试 | `tests/fakes.py`、`tests/unit/graph/`、`tests/integration/test_query_flow.py` | ✅ `pytest tests -q` → 18 passed（不联网） |

真实 DeepSeek（`deepseek-flash`）冒烟已通过：「2025年1月急诊人次」直出 SQL；「上个月心内科门诊患者的次均费用」问 3 轮（time_range → time_field_scope → department_entity）后生成 SQL。

**已知限制**：`interrupt` 发生在子图内时，子图本轮写入只在子图结束后合并到父状态；因此 `needs_clarification` 由 `ambiguity_policy` 节点预先写入，澄清问题内容从 `__interrupt__` 的 payload 读取。

**实际 API 契约**

- `POST /sessions` `{user_id?}` → `{session_id}`
- `POST /queries` `{session_id, question}` → `QueryResponse`
- `POST /queries/{query_id}/answers` `{answer: {question_id?, option_id?, text?}}` → `QueryResponse`（`Command(resume=answer)`）
- `GET /queries/{query_id}` → `{session_id, query_id, question, status, sql}`
- `QueryResponse`：`{session_id, query_id, status, sql, sql_source, clarification, assumptions, clarification_history, message, error, trace}`
- `clarification`：`{question_id, code, question, options:[{id,label,value}], default_option_id, round, max_rounds, allow_free_text, can_skip}`
- 响应里出现 `__interrupt__` 即 `status = needs_clarification`；`assumptions[].source=default` 表示该口径按默认值处理。

## 1. 分工边界

- **A**：`data/schema.json`、`metrics.json`、`glossary.json`、`ambiguities.json`、`sql_examples_sqlite.json`；`app/knowledge/` 的 `KnowledgeProvider`。
- **B**：`app/graph/`、`app/api/`、`app/prompts/`、`app/memory/`。
- **C**：`app/tools/sql_agent/agent.py::generate_sql(question)`，纯 Python 多候选 + SQLite 自修正；未就绪时 B 用 LLM 兜底。
- B 不实现知识库、不实现 SQL 自修正；只编排和封装。

## 2. 目录结构

```text
app/graph/
├── __init__.py
├── state.py                    # GraphState
├── builder.py                  # 组装主图 + 澄清子图 + checkpointer
├── routing.py                  # 条件路由
├── deps.py                     # GraphDeps：knowledge / ChatDeepSeek / settings / prompts
├── nodes/
│   ├── __init__.py
│   ├── analyze.py              # 规则优先；冲突/低置信才调 DeepSeek
│   ├── ambiguity.py            # 规则 + LLM 语义歧义补充
│   ├── finalize.py             # 重写问题 + 二次 schema linking + 最终检查
│   ├── sql_generation.py       # DeepSeek 直接生成 SQL
│   └── error.py                # 终止节点
└── subgraphs/
    ├── __init__.py
    └── clarification.py        # plan -> ask(interrupt) -> apply -> route

app/api/
├── __init__.py
├── schemas.py                  # 请求/响应 Pydantic
├── routes_sessions.py          # POST /sessions
└── routes_queries.py           # POST /queries, POST /queries/{id}/answers

app/prompts/
├── __init__.py
├── base.py                     # 共享 system prompt
├── analyze.py                  # 冲突消解 prompt
├── ambiguity.py                # 语义歧义补充 prompt
├── clarify.py                  # 澄清文案润色（clarify_plan 内部）
└── sql.py                      # DeepSeek SQL 生成 prompt

app/memory/
├── __init__.py
├── checkpointer.py             # MemorySaver 单例
└── session_repository.py       # session -> query 列表

app/config.py                   # DeepSeek + reference_date 等
app/main.py                     # 创建 graph，挂 router
```

## 3. 节点表：每个节点具体做什么

| 顺序 | 节点 | 具体做什么 | 输入 | 输出 |
|---|---|---|---|---|
| 1 | `analyze_question` | 先用 A 的知识库拿 schema、指标、术语；再用**一次**结构化 LLM 调用，产出：意图、指标、实体、时间范围、候选表/字段、候选歧义。返回前用 schema 白名单过滤 LLM 编造的表/列。**不决定 blocking、默认值、优先级。** | `original_question`、A 的 schema/metrics/terms | `intent`、`schema_links`、`candidate_ambiguities` |
| 2 | `ambiguity_policy` | 纯规则。合并 LLM 候选歧义 + A 的 `ambiguities.json`；去掉 `asked_codes` 里已经问过的；给每条标 `blocking / priority / default / source`。没有 blocking → 去 finalize；有 → 进澄清子图。 | `candidate_ambiguities`、A 的规则、`asked_codes`、`round` | `ambiguities`、`pending_codes` |
| 3 | `clarify_plan` | 从 blocking 且没问过的歧义里，选**优先级最高的一个**；用 A 的模板生成问题、选项、默认值，必要时 LLM 只润色措辞；生成一个 `pending_question`。 | `ambiguities`、`asked_codes`、`round` | `pending_question` |
| 4 | `clarify_ask` | **只调用 `interrupt(pending_question)`**，把问题交给 HTTP 层并暂停；用户回答后 resume，返回 `last_answer`。**不调 LLM、不写历史、不做业务判断。** | `pending_question` | `last_answer` |
| 5 | `clarify_apply` | 解析用户回答：选项 ID、自由文本或 skip。无效回答用默认值或让用户重答一次。更新：`resolved_slots`、`assumptions`、`asked_codes`、`clarification_round`、`clarification_history`。 | `pending_question`、`last_answer` | 更新后的澄清状态 |
| 6 | `route_more` | 子图内路由：还有未问的 blocking 且轮次未满 → 回 `clarify_plan` 问下一个；否则退出子图，回主图。 | 更新后的状态 | 下一个节点 |
| 7 | `finalize_question` | **确定性模板，不调 LLM**。把原问题 + 已确认 slot + 默认假设拼成一条完整、自包含的问题；相对时间在这里解析成明确日期区间；再用 A 的知识库重算最终表/字段/指标。 | `original_question`、`resolved_slots`、`assumptions` | `rewritten_question`、`final_schema_links` |
| 8 | `final_ambiguity_check` | 纯规则：对重写后的问题再跑一次 A 的歧义检测，只保留**新出现的 blocking code**（不在 `asked_codes` 里）。有新的且轮次未满 → 回澄清子图；否则去生成 SQL。 | `rewritten_question`、`asked_codes`、`round` | `final_ambiguities` |
| 9 | `generate_sql` | **本轮只用 DeepSeek**：最终问题 + A 的 `ctx.prompt_text`（相关表/join/枚举/PII）+ top metrics + examples → 一条 SQLite SQL；输出 `sql`、`assumptions`、`confidence`。C 就绪后只替换这个节点的内部实现，图不变。 | `rewritten_question`、A 的上下文 | `sql`、`assumptions`、`confidence` |
| 10 | `validate_sql` | **C 就绪后再加**：纯规则校验只读、单条、表/列、PII。 | `sql` | `validation` |
| 11 | `sql_repair` | **C 就绪后再加**：最多修一次。 | `sql`、`validation` | 修复后的 `sql` |
| 12 | `answer_merger` | **当前不需要节点**；API 直接拼响应（SQL 已生成、未执行、assumptions）。 | `sql`、`assumptions` | API response |
| 13 | `error` / `rejected` | 终止：无法生成 SQL、超时、无法链接任何表/指标、越界问题。 | `error` | `status=error/rejected` |

> 当前实施版本：第 1–9 步 + `error`；第 10–12 步是 C 就绪后的扩展。

## 4. 图结构

```text
START
  → analyze_question
  → ambiguity_policy
  → route_after_ambiguity
       ├─ 无 blocking → finalize_question
       └─ 有 blocking → clarification_subgraph
                            clarify_plan
                              → clarify_ask（interrupt，只暂停）
                              → clarify_apply
                              → route_more
                                   ├─ 还有问题且未超轮次 → clarify_plan
                                   └─ 完成 → END（回主图）
  → finalize_question
  → final_ambiguity_check
       ├─ 有新 blocking 且未超轮次 → clarification_subgraph
       └─ 完成 → generate_sql
  → generate_sql（DeepSeek 直接生成）
  → END
```

关键点：

- 当前版本到 `generate_sql` 结束，没有 `validate_sql` / `sql_repair` / `answer_merger` 节点；
- API 在 `generate_sql` 后直接拼响应（SQL、assumptions）；
- `clarify_ask` 是唯一的 `interrupt` 节点；里面不能有 LLM 和副作用；
- 澄清子图跨多次 HTTP 请求循环；每次 HTTP 只问一个问题；
- `finalize_question` 不再调 LLM 做第二次歧义分析；
- 最终检查只看新 `code`，`asked_codes` 里的不再问。

## 5. GraphState

节点返回**局部更新**，不要 `return {**state, ...}`。列表字段用 `Annotated[list, operator.add]`。

```python
from operator import add
from typing import Annotated, Any, Literal, TypedDict


class GraphState(TypedDict, total=False):
    # 身份
    session_id: str
    query_id: str
    thread_id: str

    # 输入
    original_question: str
    conversation_context: list[dict]

    # 分析 / 知识
    knowledge_context: dict
    intent: dict
    schema_links: dict
    candidate_ambiguities: list[dict]

    # 澄清
    ambiguities: list[dict]
    pending_codes: list[str]
    asked_codes: list[str]
    clarification_round: int
    pending_question: dict | None
    last_answer: dict | None
    answers: Annotated[list[dict], add]
    resolved_slots: dict
    assumptions: list[dict]
    clarification_history: Annotated[list[dict], add]

    # 最终问题
    rewritten_question: str | None
    final_schema_links: dict
    final_ambiguities: list[dict]

    # SQL
    sql: str | None
    sql_candidates: list[str]
    sql_source: Literal["deepseek"] | None
    sql_verified: bool
    sql_meta: dict
    repair_count: int

    # 校验 / 输出
    validation: dict
    sql_result: dict | None
    answer: str | None
    status: Literal["ok", "needs_clarification", "error", "rejected"]
    error: dict | None
    trace: Annotated[list[dict], add]
```

## 6. 核心代码示例

### 6.1 澄清子图

```python
# app/graph/subgraphs/clarification.py
from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.graph.state import GraphState


def build_clarification_subgraph(deps):
    graph = StateGraph(GraphState)
    graph.add_node("plan", partial(plan, deps=deps))
    graph.add_node("ask", ask)
    graph.add_node("apply", apply)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "ask")
    graph.add_edge("ask", "apply")
    graph.add_conditional_edges(
        "apply", partial(route_more, deps=deps), {"plan": "plan", "done": END}
    )
    return graph.compile()


def plan(state: GraphState, deps) -> dict:
    asked = set(state.get("asked_codes", []))
    items = [
        i for i in state.get("ambiguities", [])
        if i.get("blocking") and i.get("code") not in asked
    ]
    if not items:
        return {"pending_question": None}

    item = sorted(items, key=lambda x: x.get("priority", 99))[0]
    return {
        "pending_question": {
            "id": f"q_{item['code']}",
            "code": item["code"],
            "question": item["question"],
            "options": item.get("options", []),
            "default_option_id": item.get("default"),
            "allow_free_text": item.get("allow_free_text", True),
            "can_skip": True,
            "round": state.get("clarification_round", 0) + 1,
            "max_rounds": deps.settings.max_clarification_turns,
            "priority": item.get("priority", 99),
        }
    }


def ask(state: GraphState) -> dict:
    """只做 interrupt：不调 LLM、不写 history。"""
    answer = interrupt(state["pending_question"])
    return {"last_answer": answer}


def apply(state: GraphState) -> dict:
    q = state["pending_question"]
    answer = state.get("last_answer") or {}
    option_id = answer.get("option_id")
    if option_id == "skip" or not option_id:
        value = q.get("default_option_id")
        source = "default"
    else:
        value = option_id
        source = "user"

    round_no = state.get("clarification_round", 0) + 1
    return {
        "resolved_slots": {**state.get("resolved_slots", {}), q["code"]: value},
        "assumptions": [
            *state.get("assumptions", []),
            {"code": q["code"], "value": value, "source": source},
        ],
        "asked_codes": [*state.get("asked_codes", []), q["code"]],
        "clarification_round": round_no,
        "answers": [answer],
        "clarification_history": [{
            "round": round_no,
            "code": q["code"],
            "question": q["question"],
            "answer": answer,
            "resolved": True,
            "source": source,
        }],
        "pending_question": None,
    }


def route_more(state: GraphState, deps) -> str:
    if state.get("clarification_round", 0) >= deps.settings.max_clarification_turns:
        return "done"
    asked = set(state.get("asked_codes", []))
    has_more = any(
        i.get("blocking") and i.get("code") not in asked
        for i in state.get("ambiguities", [])
    )
    return "plan" if has_more else "done"
```

### 6.2 主图

```python
# app/graph/builder.py
from functools import partial

from langgraph.graph import END, START, StateGraph

from app.graph import nodes, routing
from app.graph.state import GraphState
from app.graph.subgraphs.clarification import build_clarification_subgraph
from app.memory.checkpointer import get_checkpointer


def build_graph(deps):
    g = StateGraph(GraphState)
    g.add_node("analyze", partial(nodes.analyze.run, deps=deps))
    g.add_node("ambiguity_policy", partial(nodes.ambiguity.run, deps=deps))
    g.add_node("clarification", build_clarification_subgraph(deps))
    g.add_node("finalize_question", partial(nodes.finalize.run, deps=deps))
    g.add_node("final_ambiguity_check", partial(nodes.finalize.check, deps=deps))
    g.add_node("generate_sql", partial(nodes.sql_generation.run, deps=deps))
    g.add_node("error", nodes.error.run)

    g.add_edge(START, "analyze")
    g.add_edge("analyze", "ambiguity_policy")
    g.add_conditional_edges(
        "ambiguity_policy",
        routing.route_after_ambiguity,
        {"clarification": "clarification", "finalize": "finalize_question"},
    )
    g.add_edge("clarification", "finalize_question")
    g.add_edge("finalize_question", "final_ambiguity_check")
    g.add_conditional_edges(
        "final_ambiguity_check",
        routing.route_after_final_check,
        {
            "clarification": "clarification",
            "generate_sql": "generate_sql",
            "error": "error",
        },
    )
    g.add_edge("generate_sql", END)
    g.add_edge("error", END)
    return g.compile(checkpointer=get_checkpointer())
```

### 6.3 `analyze_question`

```python
# app/graph/nodes/analyze.py
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage

from app.graph.state import GraphState


class AmbiguityCandidate(BaseModel):
    code: str
    reason: str
    blocking: bool = True
    priority: int = 50


class AnalysisOutput(BaseModel):
    intent: str
    metrics: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    time_range: str | None = None
    tables: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    ambiguities: list[AmbiguityCandidate] = Field(default_factory=list)


def run(state: GraphState, deps) -> dict:
    question = state["original_question"]
    schema = deps.knowledge.get_schema_context(question)
    metrics = deps.knowledge.search_metrics(question, top_k=5)
    terms = deps.knowledge.resolve_terms(question)

    llm = deps.llm.with_structured_output(AnalysisOutput)
    result = llm.invoke([
        SystemMessage(content=deps.prompts.analyze_system),
        HumanMessage(content=(
            f"问题：{question}\n\nSchema：\n{schema.prompt_text}\n\n"
            f"指标：{[m.name for m in metrics]}\n术语：{[t.term for t in terms]}"
        )),
    ])

    allowed_tables = set(schema.tables)
    allowed_columns = set(schema.columns)
    tables = [t for t in result.tables if t in allowed_tables]
    columns = [c for c in result.columns if c in allowed_columns]

    return {
        "intent": {
            "type": result.intent,
            "metrics": result.metrics,
            "entities": result.entities,
            "time_range": result.time_range,
        },
        "schema_links": {"tables": tables, "columns": columns, "metrics": result.metrics},
        "candidate_ambiguities": [a.model_dump() for a in result.ambiguities],
        "knowledge_context": {"schema_text": schema.prompt_text},
        "trace": [{"node": "analyze_question", "intent": result.intent}],
    }
```

### 6.4 `ambiguity_policy`

```python
# app/graph/nodes/ambiguity.py
from app.graph.state import GraphState


def run(state: GraphState, deps) -> dict:
    question = state["original_question"]
    terms = deps.knowledge.resolve_terms(question)
    rule_report = deps.knowledge.detect_ambiguities(question, terms)

    merged = {i.code: i.model_dump() for i in rule_report.items}
    for item in state.get("candidate_ambiguities", []):
        merged.setdefault(item["code"], item)

    items = list(merged.values())
    asked = set(state.get("asked_codes", []))
    pending = [
        i for i in items
        if i.get("blocking") and i.get("code") not in asked
    ]
    return {
        "ambiguities": items,
        "pending_codes": [i["code"] for i in pending],
        "trace": [{"node": "ambiguity_policy", "blocking": [i["code"] for i in pending]}],
    }
```

### 6.5 `finalize_question` + 最终检查

```python
# app/graph/nodes/finalize.py
from app.graph.state import GraphState


def run(state: GraphState, deps) -> dict:
    rewritten = render_question(
        original=state["original_question"],
        slots=state.get("resolved_slots", {}),
        assumptions=state.get("assumptions", []),
        reference_date=deps.settings.reference_date,
    )
    terms = deps.knowledge.resolve_terms(rewritten)
    metrics = deps.knowledge.search_metrics(rewritten, top_k=5)
    schema = deps.knowledge.get_schema_context(rewritten)

    return {
        "rewritten_question": rewritten,
        "final_schema_links": {
            "tables": schema.tables,
            "columns": schema.columns,
            "metrics": [m.id for m in metrics],
            "joins": schema.join_paths,
        },
        "trace": [{"node": "finalize_question", "rewritten": rewritten}],
    }


def check(state: GraphState, deps) -> dict:
    report = deps.knowledge.detect_ambiguities(state["rewritten_question"], [])
    asked = set(state.get("asked_codes", []))
    new_items = [
        i.model_dump() for i in report.items
        if i.blocking and i.code not in asked
    ]
    return {"final_ambiguities": new_items}


def render_question(original, slots, assumptions, reference_date):
    """确定性模板：把 slot 和默认假设拼进原问题；相对时间解析成明确日期。"""
    ...
```

### 6.6 C adapter + LLM 兜底（C 就绪后的扩展）

> 当前版本**跳过本节**：`generate_sql` 直接调 DeepSeek。C 就绪后再把 `CAdapter/AutoBackend` 加回来。

```python
# app/graph/sql_backend.py
from typing import Literal, Protocol
from pydantic import BaseModel


class SqlGenerationResult(BaseModel):
    sql: str | None
    verified: bool
    result_empty: bool = False
    candidates: list[str] = []
    selected_reason: str = ""
    source: Literal["c", "llm"] = "llm"
    meta: dict = {}


class SqlBackend(Protocol):
    def generate(self, question: str) -> SqlGenerationResult: ...


class CAdapter:
    def generate(self, question: str) -> SqlGenerationResult:
        from app.tools.sql_agent.agent import generate_sql
        data = generate_sql(question)
        return SqlGenerationResult(
            sql=data.get("sql"),
            verified=data.get("verified", False),
            result_empty=data.get("result_empty", False),
            candidates=data.get("candidates", []),
            selected_reason=data.get("selected_reason", ""),
            source="c",
            meta=data,
        )


class LLMFallback:
    def __init__(self, llm, knowledge, validator):
        self.llm = llm
        self.knowledge = knowledge
        self.validator = validator

    def generate(self, question: str) -> SqlGenerationResult:
        # 用 A 的 schema + sql_examples_sqlite.json 拼 prompt，调一次 LLM
        # 解析 SQL，过 validator；verified=False
        ...


class AutoBackend:
    def __init__(self, c_backend: CAdapter, llm_backend: LLMFallback):
        self.c = c_backend
        self.llm = llm_backend

    def generate(self, question: str) -> SqlGenerationResult:
        try:
            return self.c.generate(question)
        except Exception:
            return self.llm.generate(question)
```

`generate_sql` 节点：

```python
def run(state, deps) -> dict:
    result = deps.sql_backend.generate(state["rewritten_question"])
    return {
        "sql": result.sql,
        "sql_candidates": result.candidates,
        "sql_source": result.source,
        "sql_verified": result.verified,
        "sql_meta": result.meta,
        "trace": [{"node": "generate_sql", "source": result.source}],
    }
```

### 6.7 校验与响应

```python
# app/graph/nodes/validation.py
def run(state, deps) -> dict:
    result = deps.validator.validate(state["sql"] or "")
    return {"validation": result.model_dump()}


# app/graph/nodes/answer_merger.py
def run(state) -> dict:
    assumptions = state.get("assumptions", [])
    message = "SQL 已生成，未执行。"
    if assumptions:
        message += " 基于默认假设：" + "；".join(
            f"{a['code']}={a['value']}({a['source']})" for a in assumptions
        )
    return {"answer": message, "status": "ok"}
```

> 现有 `SQLValidator` 只允许 `SELECT` 开头，会拒绝 `WITH ... SELECT`；开发时先修，否则 CTE 会被误杀。

### 6.8 `deps.py` / `routing.py` / `error.py` / `main.py`

```python
# app/graph/deps.py
from dataclasses import dataclass
from typing import Any

from langchain_deepseek import ChatDeepSeek

from app.knowledge.provider import FileKnowledgeProvider


@dataclass
class GraphDeps:
    knowledge: FileKnowledgeProvider
    llm: ChatDeepSeek
    settings: Any           # app.config.Settings
    prompts: Any            # PromptBundle


def build_deps() -> GraphDeps:
    from app.config import settings
    from app.knowledge.provider import FileKnowledgeProvider

    llm = ChatDeepSeek(
        model=settings.llm_model,          # deepseek-chat
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,    # https://api.deepseek.com
        temperature=0.2,
    )
    return GraphDeps(
        knowledge=FileKnowledgeProvider("data"),
        llm=llm,
        settings=settings,
        prompts=...,
    )
```

> `base_url` 是当前安装的 `langchain-deepseek` 的参数名（已用 `inspect.signature` 确认）；如果版本不同，装好后用
> `python -c "import inspect; from langchain_deepseek import ChatDeepSeek; print(inspect.signature(ChatDeepSeek))"`
> 确认一次。

```python
# app/graph/routing.py
def route_after_ambiguity(state) -> str:
    return "clarification" if state.get("pending_codes") else "finalize"


def route_after_final_check(state) -> str:
    if state.get("final_ambiguities") and state.get("clarification_round", 0) < 3:
        return "clarification"
    if not state.get("final_schema_links", {}).get("tables"):
        return "error"
    return "generate_sql"


def route_after_validation(state) -> str:
    validation = state.get("validation") or {}
    if validation.get("valid"):
        return "answer"
    if state.get("repair_count", 0) < 1:
        return "repair"
    if validation.get("needs_clarification") and state.get("clarification_round", 0) < 3:
        return "clarification"
    return "error"
```

```python
# app/graph/nodes/error.py
def run(state) -> dict:
    return {
        "status": "error",
        "error": state.get("error") or {"code": "unknown"},
    }
```

```python
# app/main.py
from fastapi import FastAPI

from app.api import routes_queries, routes_sessions
from app.graph.builder import build_graph
from app.graph.deps import build_deps

app = FastAPI(title="Medical NL2SQL Agent", version="0.1.0")
app.state.graph = build_graph(build_deps())
app.include_router(routes_sessions.router)
app.include_router(routes_queries.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}
```

## 7. FastAPI 接口

### 7.1 端点

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/sessions` | 创建 session，返回 UUID |
| POST | `/queries` | 开始一个问题，返回 `query_id`、`needs_clarification` 或 SQL |
| POST | `/queries/{query_id}/answers` | 提交一个澄清回答并 resume |
| GET | `/queries/{query_id}` | 查询状态（可选） |

### 7.2 请求/响应

```python
# app/api/schemas.py
from typing import Literal
from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    session_id: str
    question: str = Field(min_length=1, max_length=2000)


class ClarificationAnswer(BaseModel):
    question_id: str
    option_id: str | None = None
    text: str | None = None


class QueryResponse(BaseModel):
    session_id: str
    query_id: str
    status: Literal["ok", "needs_clarification", "error", "rejected"]
    question: str
    rewritten_question: str | None = None
    clarification: dict | None = None
    sql: str | None = None
    sql_source: Literal["deepseek"] | None = None
    verified: bool = False
    answer: str | None = None
    assumptions: list[dict] = []
    error: dict | None = None
    trace: dict | None = None
```

### 7.3 路由

```python
# app/api/routes_queries.py
import asyncio
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from langgraph.types import Command

from app.api.schemas import ClarificationAnswer, QueryRequest, QueryResponse

router = APIRouter(prefix="/queries", tags=["queries"])


@router.post("", response_model=QueryResponse)
async def start_query(body: QueryRequest, request: Request) -> QueryResponse:
    graph = request.app.state.graph
    query_id = uuid4().hex
    initial = {
        "session_id": body.session_id,
        "query_id": query_id,
        "thread_id": query_id,
        "original_question": body.question,
        "clarification_round": 0,
        "asked_codes": [],
        "resolved_slots": {},
        "assumptions": [],
        "repair_count": 0,
    }
    config = {"configurable": {"thread_id": query_id}}
    result = await asyncio.to_thread(graph.invoke, initial, config)
    return to_response(result)


@router.post("/{query_id}/answers", response_model=QueryResponse)
async def answer_clarification(
    query_id: str, body: ClarificationAnswer, request: Request
) -> QueryResponse:
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": query_id}}
    snapshot = graph.get_state(config)
    if not snapshot.next:
        raise HTTPException(status_code=409, detail="not waiting for clarification")

    result = await asyncio.to_thread(
        graph.invoke, Command(resume=body.model_dump()), config
    )
    return to_response(result)


def to_response(result: dict) -> QueryResponse:
    interrupts = result.get("__interrupt__")
    if interrupts:
        return QueryResponse(
            session_id=result["session_id"],
            query_id=result["query_id"],
            status="needs_clarification",
            question=result["original_question"],
            clarification=interrupts[0].value,
            assumptions=result.get("assumptions", []),
            trace=build_trace(result),
        )
    return QueryResponse(
        session_id=result["session_id"],
        query_id=result["query_id"],
        status=result.get("status", "error"),
        question=result["original_question"],
        rewritten_question=result.get("rewritten_question"),
        sql=result.get("sql"),
        sql_source=result.get("sql_source"),
        verified=result.get("sql_verified", False),
        answer=result.get("answer"),
        assumptions=result.get("assumptions", []),
        error=result.get("error"),
        trace=build_trace(result),
    )


def build_trace(result: dict) -> dict:
    return {"nodes": result.get("trace", [])}
```

### 7.4 checkpointer

```python
# app/memory/checkpointer.py
from langgraph.checkpoint.memory import MemorySaver

_CHECKPOINTER = MemorySaver()


def get_checkpointer() -> MemorySaver:
    return _CHECKPOINTER
```

- `thread_id = query_id`；同一 session 的多个问题互不污染。
- MVP 用 `MemorySaver`，只支持单 worker；演示重启恢复再换 `SqliteSaver`。
- `graph.get_state(config).next` 为空表示不在等待澄清，resume 返回 409。

## 8. 完整执行链路

### 8.1 无歧义

```http
POST /queries
{"session_id": "s-001", "question": "统计2025年1月急诊人次"}
```

```json
{
  "query_id": "q-001",
  "status": "ok",
  "question": "统计2025年1月急诊人次",
  "rewritten_question": "统计2025年1月1日至2025年1月31日急诊（activity_type_code='2'）人次",
  "sql": "SELECT COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time) AS cnt FROM visit WHERE activity_type_code = '2' AND treatment_occurrence_time >= '2025-01-01' AND treatment_occurrence_time < '2025-02-01'",
  "sql_source": "deepseek",
  "verified": true,
  "assumptions": []
}
```

### 8.2 有歧义：一次问一个

第一轮：

```http
POST /queries
{"session_id": "s-001", "question": "上个月门诊人次是多少"}
```

```json
{
  "query_id": "q-002",
  "status": "needs_clarification",
  "clarification": {
    "round": 1,
    "max_rounds": 3,
    "question": {
      "id": "q_time_range",
      "code": "time_range",
      "question": "您说的『上个月』是指自然月还是最近30天？",
      "options": [
        {"id": "natural_month", "label": "自然月"},
        {"id": "last_30_days", "label": "最近30天"}
      ],
      "default_option_id": "natural_month",
      "allow_free_text": true,
      "can_skip": true
    }
  }
}
```

第二轮：

```http
POST /queries/q-002/answers
{"question_id": "q_time_range", "option_id": "natural_month"}
```

```json
{
  "query_id": "q-002",
  "status": "needs_clarification",
  "clarification": {
    "round": 2,
    "max_rounds": 3,
    "question": {
      "id": "q_visit_scope",
      "code": "visit_scope",
      "question": "门诊人次是只统计门诊，还是包含急诊？",
      "options": [
        {"id": "outpatient_only", "label": "仅门诊"},
        {"id": "outpatient_emergency", "label": "门诊+急诊"}
      ],
      "default_option_id": "outpatient_only",
      "allow_free_text": true,
      "can_skip": true
    }
  },
  "assumptions": [
    {"code": "time_range", "value": "natural_month", "source": "user"}
  ]
}
```

第三轮：

```http
POST /queries/q-002/answers
{"question_id": "q_visit_scope", "option_id": "outpatient_only"}
```

```json
{
  "query_id": "q-002",
  "status": "ok",
  "rewritten_question": "统计2025年9月1日至2025年9月30日（自然月）门诊（仅门诊，不含急诊）人次",
  "sql": "SELECT COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time) AS cnt FROM visit WHERE activity_type_code = '1' AND treatment_occurrence_time >= '2025-09-01' AND treatment_occurrence_time < '2025-10-01'",
  "sql_source": "deepseek",
  "verified": true,
  "assumptions": [
    {"code": "time_range", "value": "natural_month", "source": "user"},
    {"code": "visit_scope", "value": "outpatient_only", "source": "user"}
  ]
}
```

### 8.3 用户拒绝回答

```http
POST /queries/q-003/answers
{"question_id": "q_time_range", "option_id": "skip"}
```

```json
{
  "query_id": "q-003",
  "status": "ok",
  "rewritten_question": "统计2025年9月1日至2025年9月30日门诊人次（默认自然月）",
  "sql": "SELECT COUNT(DISTINCT ...) ...",
  "assumptions": [
    {"code": "time_range", "value": "natural_month", "source": "default"}
  ]
}
```

## 9. 与 A / C 的集成

### 9.1 A 的 `KnowledgeProvider`

```python
knowledge.get_schema_context(question, intent=None)
knowledge.search_metrics(question, top_k=5)
knowledge.resolve_terms(question)
knowledge.detect_ambiguities(question, terms)
knowledge.search_sql_examples(question, top_k=3)
```

使用位置：

- `analyze_question`：schema + metrics + terms；
- `ambiguity_policy`：`detect_ambiguities`；
- `finalize_question`：terms + metrics + schema；
- `LLMFallback`：schema + `sql_examples_sqlite.json`。

注意：A 的 knowledge 层已完成并通过 `python -m app.knowledge.validate --data-dir data`；B 直接使用 `FileKnowledgeProvider`，不需要 adapter，也不会再遇到 `ColumnInfo/type` 的 `TypeError`。

### 9.2 C 的 `generate_sql`

**当前 C 未就绪**：B 的 `generate_sql` 节点只调用 DeepSeek：

```python
from langchain_core.messages import HumanMessage, SystemMessage

from app.prompts.base import SQL_SYSTEM
from app.prompts.sql import build_sql_messages


def generate_sql(state, deps):
    context = deps.knowledge.get_schema_context(state["rewritten_question"])
    metrics = deps.knowledge.search_metrics(state["rewritten_question"])
    examples = deps.knowledge.search_sql_examples(
        state["rewritten_question"], metrics=metrics
    )
    messages = [
        SystemMessage(content=SQL_SYSTEM),
        HumanMessage(content=build_sql_messages(
            question=state["rewritten_question"],
            schema_context=context.prompt_text,
            metrics=metrics,
            examples=examples,
        )),
    ]
    result = deps.llm.invoke(messages)
    return {"sql": result.sql, "assumptions": result.assumptions}
```

C 就绪后，只替换这个节点的内部实现为 `app.tools.sql_agent.agent.generate_sql`，图不变。

## 10. 配置

```python
# app/config.py
llm_api_key: str = ""
llm_model: str = "deepseek-chat"
llm_base_url: str = "https://api.deepseek.com"

max_clarification_turns: int = 3
max_sql_repairs: int = 1
reference_date: str = "2025-12-31"   # demo 相对时间基准
```

`.env.example`：

```text
LLM_API_KEY=your-deepseek-api-key
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com

MAX_CLARIFICATION_TURNS=3
MAX_SQL_REPAIRS=1
REFERENCE_DATE=2025-12-31
```

`reference_date` 很重要：合成数据是 2025 年，如果按当前日期解析“上个月”，可能查不到数据。

## 11. 开发顺序

| 天 | 任务 | 交付 |
|---|---|---|
| D1 | 冻结 `GraphState`、API Pydantic；`MemorySaver` + interrupt/subgraph spike | 能跑通“暂停 → resume” |
| D2 | 接入 A 的 `FileKnowledgeProvider`；`analyze` prompt + fake DeepSeek | `analyze` 能输出 intent/schema_links |
| D3 | `ambiguity_policy` + 澄清子图 `plan/ask/apply` | 一次一个问题、轮数按 pending 动态确定 |
| D4 | `finalize_question` + `final_ambiguity_check` + API 开始/恢复 | 端到端澄清对话 |
| D5 | `generate_sql`（DeepSeek + A 的上下文） | 能生成 SQLite SQL |
| D6 | API 响应、错误处理、trace | 可返回 SQL/澄清/错误 |
| D7 | 3 条演示对话 + 集成测试 + trace/错误处理 | 可演示 |
| D8 | 文档、缓冲、联调 | 交接 |

先做纵向切片：一个问题从 START 走到 END，再加澄清。

## 12. 验收标准

- [ ] `build_graph(deps)` 能编译，挂 `MemorySaver`。
- [ ] 澄清子图能 interrupt/resume；resume 不重复调 LLM。
- [ ] 一次 HTTP 只问一个问题；最多 3 轮；同一 code 不重复问。
- [ ] 无歧义问题一次返回 `ok` + SQL。
- [ ] 有歧义问题首次返回 `needs_clarification`，有 options/default。
- [ ] 用户 `skip`/拒绝 → 默认值继续，`assumptions` 明确返回。
- [ ] 同一 session 多个 query 的澄清状态互不串线。
- [ ] `sql_source="deepseek"`；DeepSeek 超时/返回格式错误时返回结构化错误，不 500。
- [ ] 生成的 SQL 通过只读校验；`WITH ... SELECT` 不被误杀。
- [ ] `DELETE/UPDATE/DROP`、多语句、PII 输出被拒绝。
- [ ] 非法 `question_id/option_id` 返回 422；不在等待澄清时 resume 返回 409。
- [ ] 响应含 `query_id/status/assumptions/sql_source/verified/trace`。
- [ ] `pytest tests/unit/graph tests/integration -q` 全绿；LLM 用 fake。
- [ ] `graph/` 不 import 具体 knowledge/tools 实现，通过 `GraphDeps` 注入。

## 13. 风险与取舍

| 风险 | 处理 |
|---|---|
| `interrupt` 节点 resume 时重跑 | `clarify_ask` 只调用 `interrupt`，LLM 放在 `clarify_plan` |
| 子图 + checkpointer 不工作 | D1 先做 30 行 spike；不行就退回父图 plan/ask/apply 三节点 |
| `thread_id=session_id` 串状态 | 用 `query_id` |
| 同一歧义反复问 | `asked_codes` + 最大轮次 + 默认值 |
| 二次 LLM 分析改变原意/死循环 | `finalize_question` 确定性；最终检查只看新 code |
| A 的 loader/规则不完整 | `SimpleKnowledgeAdapter` 兜底；LLM 候选歧义一等公民 |
| C 接口未就绪/格式不符 | `AutoBackend` 降级 LLM；统一 `SqlGenerationResult` |
| 现有 `SQLValidator` 拒绝 WITH | 先修成允许 `WITH ... SELECT` |
| 合成数据日期与“上个月”不匹配 | `reference_date=2025-12-31` |
| MemorySaver 多 worker 丢状态 | MVP 单 worker；需要重启恢复再换 SqliteSaver |
| LLM 结构化输出失败 | Pydantic 校验 + 一次重试 + 降级 |

## 14. 给 AI 的直接开发顺序

1. 修 `app/graph/state.py`，加入本文件字段。
2. 实现 `app/memory/checkpointer.py` 的 `MemorySaver`。
3. 写最小 spike：`START -> ask(interrupt) -> END`，用 `graph.invoke` / `Command(resume=...)` 验证暂停/恢复。
4. 实现 `app/graph/deps.py`：`GraphDeps(knowledge, llm, sql_backend, validator, settings, prompts)`。
5. 实现澄清子图 `plan/ask/apply/route_more`。
6. 实现 `analyze.py`、`ambiguity.py`、`finalize.py`。
7. 实现 `sql_backend.py` + `sql_generation.py`。
8. 实现 `sql_generation.py`（DeepSeek）和 API 响应/错误/trace；C 就绪后再加 validation/repair。
9. 实现 `routing.py`、`builder.py`。
10. 实现 `api/schemas.py`、`routes_sessions.py`、`routes_queries.py`、`main.py`。
11. 写 fake LLM / fake knowledge / stub C，补单测和集成测试。
12. 跑 3 条演示对话；更新 `docs/API.md`、`docs/ARCHITECTURE.md`。

### 实现约束

- 不把 LLM 调用放进 `clarify_ask`。
- 不用 `session_id` 当 `thread_id`。
- 不让 LLM 决定 blocking/default/priority/轮次。
- 不把全量 schema、gold SQL、结果集放进 checkpoint。
- 不返回未校验的 SQL。
- 不把 PII、prompt 原文、API key 写进日志或响应。
