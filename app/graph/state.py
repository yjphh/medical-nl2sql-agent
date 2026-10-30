import operator
from typing import Annotated, Any, Literal, TypedDict


class GraphState(TypedDict, total=False):
    # identity
    session_id: str
    query_id: str

    # input
    original_question: str
    rewritten_question: str | None

    # knowledge / analysis
    terms: list[dict[str, Any]]
    metrics: list[dict[str, Any]]
    selected_metric_id: str | None
    intent: dict[str, Any]
    schema_context: dict[str, Any]
    schema_links: list[dict[str, Any]]
    ambiguities: list[dict[str, Any]]
    resolved_codes: list[str]

    # clarification
    pending_codes: list[str]
    asked_codes: list[str]
    clarification_round: int
    max_rounds: int
    pending_question: dict[str, Any] | None
    last_answer: dict[str, Any] | None
    resolved_slots: dict[str, Any]
    assumptions: list[dict[str, Any]]
    clarification_history: list[dict[str, Any]]

    # final check
    final_ambiguities: list[dict[str, Any]]

    # SQL output
    sql: str | None
    sql_source: Literal["deepseek", "sqlite"] | None
    sql_assumptions: list[str]
    sql_confidence: float | None
    # The chosen SQL executed but returned no rows: still usable, not trusted.
    sql_result_empty: bool

    # output
    status: Literal["ok", "needs_clarification", "error"]
    error: dict[str, Any] | None
    # Accumulated: every node returns only its own entry.  Without the
    # reducer each node's trace overwrote the previous one, so the debug panel
    # only ever showed the last node.
    trace: Annotated[list[dict[str, Any]], operator.add]
