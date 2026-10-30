"""SQL generation subgraph: wraps ``app.tools.sql_agent.core`` for LangGraph.

The subgraph shares the parent ``GraphState`` (same schema, same keys) so the
fields written here are never silently dropped by LangGraph.  Candidate rows,
vote counts and repair counters stay inside ``core.generate_sql``; only the
six parent-facing fields (``sql`` / ``sql_source`` / ``sql_assumptions`` /
``status`` / ``error`` / ``trace``) plus ``sql_confidence`` are written back.

The LLM and executor are injected at build time so tests never touch the
network or the real database.
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from app.config import settings as default_settings
from app.graph.state import GraphState
from app.tools.sql_agent.core import GenerationResult, generate_sql
from app.tools.sql_agent.executor import sqlite_executor
from app.tools.sql_agent.llm import OpenAICompatLLM
from app.tools.sql_agent.prompt import load_examples, load_schema, schema_text


def _settings(deps):
    return getattr(deps, "settings", None) or default_settings


def default_llm(deps):
    app_settings = _settings(deps)
    return OpenAICompatLLM(
        model=app_settings.llm_model,
        api_key=app_settings.llm_api_key,
        base_url=app_settings.llm_base_url,
    )


def default_executor(deps):
    app_settings = _settings(deps)
    return sqlite_executor(
        app_settings.sqlite_db_path,
        max_rows=app_settings.sql_max_rows,
        timeout_s=app_settings.sql_exec_timeout,
    )


def schema_allowed_tables() -> list[str] | None:
    try:
        return [table["name"] for table in load_schema()["tables"]]
    except (OSError, ValueError, KeyError):
        return None


def _schema_context(state: GraphState, deps) -> str:
    """Prefer B's ``schema_context.prompt_text``; only rebuild as a fallback."""
    prompt_text = (state.get("schema_context") or {}).get("prompt_text")
    if prompt_text:
        return prompt_text
    question = _question(state)
    knowledge = getattr(deps, "knowledge", None)
    if knowledge is not None:
        return knowledge.get_schema_context(question).prompt_text
    return schema_text(load_schema())


def _examples(state: GraphState, deps) -> list[dict] | None:
    knowledge = getattr(deps, "knowledge", None)
    if knowledge is None:
        return load_examples()
    examples = knowledge.search_sql_examples(_question(state))
    if examples is None:
        return None
    return [item.to_dict() if hasattr(item, "to_dict") else item for item in examples]


def _question(state: GraphState) -> str:
    # The clarified question must win: the original may still say "上个月".
    return state.get("rewritten_question") or state["original_question"]


def _assumptions(result: GenerationResult) -> list[str]:
    notes: list[str] = []
    chosen = next(
        (item for item in result.candidates if item.sql == result.sql and item.safe),
        None,
    )
    if chosen is not None:
        notes.extend(str(text) for text in chosen.assumptions)
    if result.verified:
        notes.append(result.reason)
    else:
        notes.append(f"候选结果不一致，已按静态分选优：{result.reason}")
    if result.result_empty:
        notes.append(
            "SQL 可执行，但在镜像上命中 0 行：可能是时间范围、科室/类型过滤或统计数据缺失，"
            "请核对后再使用"
        )
    return notes


def state_from_result(result: GenerationResult, *, node: str = "generate_sql") -> dict:
    """Map a ``GenerationResult`` onto response-compatible state fields.

    Shared by the graph subgraph and the direct (no-clarification) mode so the
    confidence / empty-result rules only live in one place.
    """
    stats = {
        "llm_calls": result.stats.get("llm_calls", 0),
        "executions": result.stats.get("executions", 0),
        "repairs": result.stats.get("repairs", 0),
        "elapsed_ms": result.stats.get("elapsed_ms", 0),
    }
    if not result.sql:
        return {
            "sql": None,
            "sql_source": "sqlite",
            "status": "error",
            "error": {
                "code": "sql_generation_failed",
                "message": result.error or "empty_sql",
            },
            "trace": [{"node": node, "ok": False, **stats}],
        }

    counts = {
        "candidates": len(result.candidates),
        "verified": result.verified,
        "result_empty": result.result_empty,
        "reason": result.reason,
        "result_hash": next(
            (item.result_hash for item in result.candidates if item.sql == result.sql),
            "",
        ),
    }
    # Candidate consensus alone must not imply correctness: a stable empty
    # result is still an unverified answer.
    confidence = 0.3 if result.result_empty else (0.9 if result.verified else 0.5)
    return {
        "sql": result.sql,
        "sql_source": "sqlite",
        "sql_assumptions": _assumptions(result),
        "sql_confidence": confidence,
        "sql_result_empty": result.result_empty,
        "status": "ok",
        "error": None,
        "trace": [{"node": node, "ok": True, **counts, **stats}],
    }


def run(state: GraphState, deps, *, llm, executor, examples=None, allowed_tables=None) -> dict:
    app_settings = _settings(deps)
    question = _question(state)
    result = generate_sql(
        question,
        _schema_context(state, deps),
        llm=llm,
        executor=executor,
        examples=_examples(state, deps) if examples is None else examples,
        schema_links=state.get("schema_links"),
        llm_evaluate=getattr(app_settings, "sql_llm_evaluate", False),
        max_candidates=getattr(app_settings, "sql_max_candidates", 3),
        max_repair_rounds=getattr(app_settings, "max_repair_rounds", 2),
        allowed_tables=allowed_tables,
    )
    return state_from_result(result)


def build_sql_subgraph(
    deps,
    *,
    llm=None,
    executor=None,
    examples: list[dict] | None = None,
    allowed_tables: list[str] | None = None,
):
    """Compile the SQL generation subgraph.

    ``llm`` / ``executor`` are resolved here (not per request) so the compiled
    graph can be reused; tests pass fakes to keep everything offline.
    """
    llm = llm if llm is not None else getattr(deps, "sql_llm", None) or default_llm(deps)
    executor = (
        executor
        if executor is not None
        else getattr(deps, "sql_executor", None) or default_executor(deps)
    )
    if allowed_tables is None:
        allowed_tables = schema_allowed_tables()

    graph = StateGraph(GraphState)
    graph.add_node(
        "generate",
        partial(
            run,
            deps=deps,
            llm=llm,
            executor=executor,
            examples=examples,
            allowed_tables=allowed_tables,
        ),
    )
    graph.add_edge(START, "generate")
    graph.add_edge("generate", END)
    return graph.compile()
