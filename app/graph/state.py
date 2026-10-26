from typing import Any, Literal, TypedDict


class GraphState(TypedDict, total=False):
    session_id: str
    question: str
    clarification_history: list[dict[str, str]]
    intent: dict[str, Any]
    ambiguity: dict[str, Any]
    schema_context: dict[str, Any]
    sql: str | None
    sql_result: Any
    validation: dict[str, Any]
    answer: str | None
    status: Literal["ok", "needs_clarification", "error"]
    error: str | None
