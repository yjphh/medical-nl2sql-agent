from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    session_id: str
    question: str = Field(min_length=1, max_length=2000)


class QueryResponse(BaseModel):
    session_id: str
    status: str  # ok | needs_clarification | error | not_implemented
    answer: str | None = None
    sql: str | None = None
    clarification: str | None = None
    error: str | None = None


class ClarificationAnswer(BaseModel):
    session_id: str
    answer: str
