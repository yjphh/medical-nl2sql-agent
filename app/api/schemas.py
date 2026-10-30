from typing import Any, Literal

from pydantic import BaseModel, Field


class ClarificationOption(BaseModel):
    id: str
    label: str
    value: str


class Clarification(BaseModel):
    """One blocking question, ready to be rendered by the front end."""

    question_id: str
    code: str
    question: str
    options: list[ClarificationOption] = Field(default_factory=list)
    default_option_id: str | None = None
    round: int = 1
    max_rounds: int = 1
    allow_free_text: bool = True
    can_skip: bool = True


class Assumption(BaseModel):
    code: str
    value: str = ""
    label: str = ""
    source: str = "default"
    reason: str | None = None


class QueryRequest(BaseModel):
    session_id: str
    question: str = Field(min_length=1, max_length=2000)


class QueryResponse(BaseModel):
    session_id: str
    query_id: str
    status: Literal["ok", "needs_clarification", "error"]
    sql: str | None = None
    sql_source: str | None = None
    clarification: Clarification | None = None
    assumptions: list[Assumption] = Field(default_factory=list)
    clarification_history: list[dict[str, Any]] = Field(default_factory=list)
    message: str | None = None
    error: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = Field(default_factory=list)


class ClarificationAnswer(BaseModel):
    question_id: str | None = None
    option_id: str | None = None
    text: str | None = None


class AnswerRequest(BaseModel):
    session_id: str | None = None
    answer: ClarificationAnswer


class SessionCreate(BaseModel):
    user_id: str | None = None


class SessionOut(BaseModel):
    session_id: str
    user_id: str | None = None


class QueryStatusOut(BaseModel):
    session_id: str
    query_id: str
    question: str
    status: str
    sql: str | None = None
