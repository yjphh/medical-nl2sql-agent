from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from langgraph.types import Command

from app.api.runtime import get_deps, get_graph
from app.api.schemas import (
    AnswerRequest,
    Assumption,
    Clarification,
    QueryRequest,
    QueryResponse,
)
from app.graph.direct import run_direct
from app.memory.session_repository import repository
from app.tools.sql_agent.llm import LLMError

router = APIRouter(prefix="/queries", tags=["queries"])
logger = logging.getLogger(__name__)


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
    return _clarification_from_payload(interrupts[0].value or {})


def _clarification_from_payload(payload: dict) -> Clarification:
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


def _snapshot(query_id: str):
    """Checkpointer state for a query, or ``None`` when it cannot be read."""
    try:
        return get_graph().get_state(_config(query_id))
    except Exception:  # noqa: BLE001 - recovery must not break the endpoint
        return None


def _recovered_response(record) -> QueryResponse:
    """Rebuild a full response from what the process still remembers.

    Used when the client re-reads a query it may have missed (timeout, reloaded
    page): the checkpointer still holds the graph state, including the pending
    interrupt and the assumptions behind a finished SQL.
    """
    snapshot = _snapshot(record.query_id)
    values = dict(getattr(snapshot, "values", None) or {})
    interrupts = tuple(getattr(snapshot, "interrupts", None) or ())

    if not values:
        return QueryResponse(
            session_id=record.session_id,
            query_id=record.query_id,
            status=record.status,
            sql=record.sql,
            message="已取回后端记录的查询状态（详细口径不可用）。",
        )
    if interrupts:
        values["__interrupt__"] = list(interrupts)
    return _to_response(values, record.session_id, record.query_id)


def _assumptions(result: dict) -> list[Assumption]:
    items: list[Assumption] = [
        Assumption(**item) for item in (result.get("assumptions") or [])
    ]
    for text in result.get("sql_assumptions") or []:
        items.append(Assumption(code="sql", label=str(text), value=str(text), source="llm"))
    return items


def _friendly_error(exc: Exception) -> tuple[str, str]:
    """Map an exception to a stable code plus a Chinese sentence for the user.

    The raw text goes into ``error["detail"]`` so the UI can fold it away
    instead of showing ``TypeError: 'NoneType' object ...`` to a clinician.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "timed out" in text:
        return "llm_timeout", "模型调用超时，请稍后重试。"
    if "connect" in name or "connection" in text:
        return "llm_unavailable", "无法连接模型服务，请检查网络或后端配置。"
    if isinstance(exc, LLMError):
        if "missing_api_key" in text:
            return "llm_unavailable", "后端未配置模型密钥，请检查 .env 中的 LLM_API_KEY。"
        return "bad_llm_response", "模型返回内容无法解析，请重试或换一种问法。"
    if isinstance(exc, (ValueError, KeyError)):
        return "bad_llm_response", "模型返回内容无法解析，请重试或换一种问法。"
    return "graph_error", "生成过程出错，请稍后重试。"


def _error_response(
    session_id: str,
    query_id: str,
    exc: Exception,
) -> QueryResponse:
    code, message = _friendly_error(exc)
    return QueryResponse(
        session_id=session_id,
        query_id=query_id,
        status="error",
        message=message,
        error={
            "code": code,
            "message": message,
            "detail": f"{type(exc).__name__}: {exc}",
        },
    )


def _to_response(result: dict[str, Any], session_id: str, query_id: str) -> QueryResponse:
    status = _status(result)
    assumptions = _assumptions(result) if status == "ok" else []
    sql = result.get("sql") if status == "ok" else None

    if status == "ok":
        message = "SQL 已生成，真实库未执行（已在本地 SQLite 镜像校验可执行性）。"
        if result.get("sql_result_empty"):
            message += "注意：镜像上查询结果为空，请核对时间范围、过滤条件或数据是否缺失。"
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
        sql_confidence=result.get("sql_confidence") if status == "ok" else None,
        sql_result_empty=bool(result.get("sql_result_empty")) if status == "ok" else False,
        schema_links=result.get("schema_links") or [],
        clarification=_clarification(result),
        assumptions=assumptions,
        clarification_history=result.get("clarification_history") or [],
        message=message,
        error=result.get("error") if status == "error" else None,
        trace=result.get("trace") or [],
    )


# ``def`` (not ``async def``) on purpose: the graph call is blocking, so
# FastAPI must run it in the threadpool or one slow query freezes /health and
# every other request in the event loop.
@router.post("", response_model=QueryResponse)
def run_query(body: QueryRequest) -> QueryResponse:
    # A retried request (same client-generated id) must not re-run the graph:
    # the first attempt may already have advanced the clarification thread.
    if body.request_id is not None:
        existing = repository.get_query(str(body.request_id))
        if existing is not None:
            return _recovered_response(existing)

    query_id = str(body.request_id or uuid.uuid4())
    started = time.monotonic()
    logger.info("query %s start: %s", query_id, body.question[:80])
    repository.create_query(query_id, body.session_id, body.question)
    repository.append_turn(body.session_id, "user", body.question)

    initial = {
        "session_id": body.session_id,
        "query_id": query_id,
        "original_question": body.question,
    }
    try:
        if body.mode == "direct":
            # Baseline: one model call, no ambiguity detection, no clarifying
            # questions.  The SQL is still verified on the SQLite mirror.
            result = run_direct(body.question, get_deps())
        else:
            result = get_graph().invoke(initial, config=_config(query_id))
    except Exception as exc:
        logger.exception("query %s failed after %.1fs", query_id, time.monotonic() - started)
        repository.update_query(query_id, "error")
        return _error_response(body.session_id, query_id, exc)

    response = _to_response(result, body.session_id, query_id)
    logger.info(
        "query %s -> %s in %.1fs",
        query_id,
        response.status,
        time.monotonic() - started,
    )
    repository.update_query(query_id, response.status, response.sql)
    repository.append_turn(body.session_id, "assistant", response.message or "")
    return response


@router.post("/{query_id}/answers", response_model=QueryResponse)
def answer_query(query_id: str, body: AnswerRequest) -> QueryResponse:
    record = repository.get_query(query_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown query_id")
    if record.status != "needs_clarification":
        raise HTTPException(
            status_code=409,
            detail=f"query is not waiting for an answer (status={record.status})",
        )

    payload = body.answer.model_dump(exclude_none=True)
    started = time.monotonic()
    logger.info("query %s answer: %s", query_id, payload)
    try:
        result = get_graph().invoke(Command(resume=payload), config=_config(query_id))
    except Exception as exc:
        logger.exception("query %s answer failed", query_id)
        repository.update_query(query_id, "error")
        return _error_response(record.session_id, query_id, exc)

    response = _to_response(result, record.session_id, query_id)
    logger.info(
        "query %s -> %s in %.1fs (round %s)",
        query_id,
        response.status,
        time.monotonic() - started,
        len(response.clarification_history),
    )
    repository.update_query(query_id, response.status, response.sql)
    repository.append_turn(record.session_id, "user", str(payload))
    repository.append_turn(record.session_id, "assistant", response.message or "")
    return response


@router.get("/{query_id}", response_model=QueryResponse)
def get_query(query_id: str) -> QueryResponse:
    record = repository.get_query(query_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown query_id")
    return _recovered_response(record)
