from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from langgraph.types import Command

from app.api.runtime import get_graph
from app.api.schemas import (
    AnswerRequest,
    Assumption,
    Clarification,
    QueryRequest,
    QueryResponse,
    QueryStatusOut,
)
from app.memory.session_repository import repository

router = APIRouter(prefix="/queries", tags=["queries"])


def _config(query_id: str) -> dict:
    # One graph thread per query: two questions in the same session must not
    # share clarification state.
    return {"configurable": {"thread_id": query_id}}


def _status(result: dict) -> str:
    if result.get("__interrupt__"):
        return "needs_clarification"
    return result.get("status") or "error"


def _clarification(result: dict) -> Clarification | None:
    interrupts = result.get("__interrupt__") or ()
    if not interrupts:
        return None
    payload = interrupts[0].value or {}
    return Clarification(
        question_id=payload.get("id", ""),
        code=payload.get("code", ""),
        question=payload.get("question", ""),
        options=payload.get("options", []),
        default_option_id=payload.get("default_option_id"),
        round=payload.get("round", 1),
        max_rounds=payload.get("max_rounds", 1),
        allow_free_text=payload.get("allow_free_text", True),
        can_skip=payload.get("can_skip", True),
    )


def _assumptions(result: dict) -> list[Assumption]:
    items: list[Assumption] = [
        Assumption(**item) for item in (result.get("assumptions") or [])
    ]
    for text in result.get("sql_assumptions") or []:
        items.append(Assumption(code="sql", label=str(text), value=str(text), source="llm"))
    return items


def _error_response(session_id: str, query_id: str, message: str) -> QueryResponse:
    return QueryResponse(
        session_id=session_id,
        query_id=query_id,
        status="error",
        message=message,
        error={"code": "graph_error", "message": message},
    )


def _to_response(result: dict[str, Any], session_id: str, query_id: str) -> QueryResponse:
    status = _status(result)
    assumptions = _assumptions(result) if status == "ok" else []
    sql = result.get("sql") if status == "ok" else None

    if status == "ok":
        message = "SQL 已生成，未执行。"
        defaults = [a for a in assumptions if a.source == "default"]
        if defaults:
            message += f"其中 {len(defaults)} 个口径按默认值处理。"
    elif status == "needs_clarification":
        message = "需要补充信息后才能生成 SQL。"
    else:
        message = (result.get("error") or {}).get("message") or "SQL 生成失败。"

    return QueryResponse(
        session_id=session_id,
        query_id=query_id,
        status=status,
        sql=sql,
        sql_source=result.get("sql_source") if status == "ok" else None,
        clarification=_clarification(result),
        assumptions=assumptions,
        clarification_history=result.get("clarification_history") or [],
        message=message,
        error=result.get("error") if status == "error" else None,
        trace=result.get("trace") or [],
    )


@router.post("", response_model=QueryResponse)
async def run_query(body: QueryRequest) -> QueryResponse:
    query_id = str(uuid.uuid4())
    repository.create_query(query_id, body.session_id, body.question)
    repository.append_turn(body.session_id, "user", body.question)

    initial = {
        "session_id": body.session_id,
        "query_id": query_id,
        "original_question": body.question,
    }
    try:
        result = get_graph().invoke(initial, config=_config(query_id))
    except Exception as exc:  # noqa: BLE001 - LLM/network failures must not become HTTP 500
        repository.update_query(query_id, "error")
        return _error_response(body.session_id, query_id, f"{type(exc).__name__}: {exc}")

    response = _to_response(result, body.session_id, query_id)
    repository.update_query(query_id, response.status, response.sql)
    repository.append_turn(body.session_id, "assistant", response.message or "")
    return response


@router.post("/{query_id}/answers", response_model=QueryResponse)
async def answer_query(query_id: str, body: AnswerRequest) -> QueryResponse:
    record = repository.get_query(query_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown query_id")
    if record.status != "needs_clarification":
        raise HTTPException(
            status_code=409,
            detail=f"query is not waiting for an answer (status={record.status})",
        )

    payload = body.answer.model_dump(exclude_none=True)
    try:
        result = get_graph().invoke(Command(resume=payload), config=_config(query_id))
    except Exception as exc:  # noqa: BLE001 - resume failures must not become HTTP 500
        repository.update_query(query_id, "error")
        return _error_response(record.session_id, query_id, f"{type(exc).__name__}: {exc}")

    response = _to_response(result, record.session_id, query_id)
    repository.update_query(query_id, response.status, response.sql)
    repository.append_turn(record.session_id, "user", str(payload))
    repository.append_turn(record.session_id, "assistant", response.message or "")
    return response


@router.get("/{query_id}", response_model=QueryStatusOut)
async def get_query(query_id: str) -> QueryStatusOut:
    record = repository.get_query(query_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown query_id")
    return QueryStatusOut(
        session_id=record.session_id,
        query_id=record.query_id,
        question=record.question,
        status=record.status,
        sql=record.sql,
    )
