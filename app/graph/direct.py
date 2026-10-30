"""Direct mode: one LLM call from question to SQL, no clarification graph.

The front end uses this to compare "agent with clarification" against "plain
LLM on the same schema context".  Generation, safety and execution still go
through ``app.tools.sql_agent.core`` so both modes share the same verification
and PII rules; only the extra rounds (metric choice, clarify, repair) are
dropped: one call, no repair, no escalation.
"""

from __future__ import annotations

from typing import Any

from app.graph.subgraphs.sql_generation import (
    default_executor,
    default_llm,
    schema_allowed_tables,
    state_from_result,
)
from app.tools.sql_agent.core import generate_sql
from app.tools.sql_agent.prompt import load_examples, load_schema, schema_text


def _context(question: str, deps) -> str:
    knowledge = getattr(deps, "knowledge", None)
    if knowledge is not None:
        return knowledge.get_schema_context(question).prompt_text
    return schema_text(load_schema())


def _examples(question: str, deps) -> list[dict] | None:
    knowledge = getattr(deps, "knowledge", None)
    if knowledge is None:
        return load_examples()
    examples = knowledge.search_sql_examples(question)
    if examples is None:
        return None
    return [item.to_dict() if hasattr(item, "to_dict") else item for item in examples]


def run_direct(question: str, deps) -> dict[str, Any]:
    """Generate SQL for ``question`` with a single model call.

    Returns the same field names as the SQL generation subgraph so the API
    layer can reuse ``_to_response`` unchanged.
    """
    llm = getattr(deps, "sql_llm", None) or default_llm(deps)
    executor = getattr(deps, "sql_executor", None) or default_executor(deps)
    result = generate_sql(
        question,
        _context(question, deps),
        llm=llm,
        executor=executor,
        examples=_examples(question, deps),
        n=1,
        llm_evaluate=False,
        max_candidates=1,
        # No repair: direct mode is the "what the model says first" baseline.
        max_repair_rounds=0,
        allowed_tables=schema_allowed_tables(),
    )
    state = state_from_result(result, node="direct_sql")
    state["trace"][0]["mode"] = "direct"
    return state
