"""Frontend HTTP client tests (no network, no Streamlit)."""

from __future__ import annotations

import json

import httpx
import pytest

from frontend.api_client import BackendClient, BackendError


def make_client(handler) -> BackendClient:
    return BackendClient("http://test", transport=httpx.MockTransport(handler))


def clarification_payload() -> dict:
    return {
        "session_id": "s1",
        "query_id": "q1",
        "status": "needs_clarification",
        "sql": None,
        "clarification": {
            "question_id": "q_time_range",
            "code": "time_range",
            "question": "您说的『上个月』是指自然月还是最近30天？",
            "options": [
                {"id": "natural_month", "label": "自然月", "value": "自然月"},
                {"id": "last_30_days", "label": "最近30天", "value": "最近30天"},
            ],
            "default_option_id": "natural_month",
            "round": 1,
            "max_rounds": 2,
            "allow_free_text": True,
            "can_skip": True,
        },
        "assumptions": [],
        "clarification_history": [],
        "message": "需要补充信息后才能生成 SQL。",
        "error": None,
        "trace": [],
    }


def test_ask_parses_clarification():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/queries"
        assert json.loads(request.content) == {
            "session_id": "s1",
            "question": "上个月门诊人次是多少",
        }
        return httpx.Response(200, json=clarification_payload())

    with make_client(handler) as client:
        response = client.ask("s1", "上个月门诊人次是多少")

    assert response.status == "needs_clarification"
    assert response.is_waiting
    assert response.clarification.code == "time_range"
    assert [o.id for o in response.clarification.options] == [
        "natural_month",
        "last_30_days",
    ]
    assert response.clarification.option_label("last_30_days") == "最近30天"


def test_answer_sends_option_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/queries/q1/answers"
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "session_id": "s1",
            "query_id": "q1",
            "status": "ok",
            "sql": "SELECT 1",
            "sql_source": "deepseek",
            "clarification": None,
            "assumptions": [{
                "code": "visit_type_scope",
                "value": "仅门诊",
                "label": "仅门诊",
                "source": "default",
            }],
            "clarification_history": [],
            "message": "SQL 已生成，未执行。",
            "error": None,
            "trace": [],
        })

    with make_client(handler) as client:
        response = client.answer("q1", question_id="q_time_range", option_id="natural_month")

    assert captured == {
        "answer": {"question_id": "q_time_range", "option_id": "natural_month"}
    }
    assert response.status == "ok"
    assert response.sql == "SELECT 1"
    assert response.assumptions[0].source == "default"


def test_answer_sends_free_text():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        payload = clarification_payload()
        payload["clarification"]["round"] = 2
        return httpx.Response(200, json=payload)

    with make_client(handler) as client:
        client.answer("q1", text="最近 30 天")

    assert captured["answer"] == {"text": "最近 30 天"}


def test_http_error_is_mapped_to_backend_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "query is not waiting for an answer"})

    with make_client(handler) as client, pytest.raises(BackendError) as excinfo:
        client.answer("q1", option_id="natural_month")

    assert excinfo.value.status_code == 409
    assert "not waiting" in excinfo.value.detail


def test_connection_error_is_mapped_to_backend_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with make_client(handler) as client:
        assert client.health() is False
        with pytest.raises(BackendError) as excinfo:
            client.ask("s1", "问题")
    assert excinfo.value.status_code == 0


def test_health():
    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    with make_client(ok) as client:
        assert client.health() is True


def test_list_metrics():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/metrics"
        assert request.url.params["q"] == "门诊"
        assert request.url.params["top_k"] == "50"
        return httpx.Response(200, json={
            "items": [{
                "id": "outpatient_visits",
                "name": "门诊人次",
                "category": "门急诊情况",
                "aliases": ["门诊量"],
                "definition": "统计周期内的门诊就诊人次",
                "unit": "人次",
            }],
            "total": 1,
        })

    with make_client(handler) as client:
        metrics = client.list_metrics("门诊")

    assert metrics[0].id == "outpatient_visits"
    assert metrics[0].aliases == ["门诊量"]


def test_query_response_tolerates_missing_fields():
    from frontend.models import QueryResponse

    response = QueryResponse.from_dict({"status": "ok", "sql": "SELECT 1"})
    assert response.session_id == ""
    assert response.clarification is None
    assert response.is_waiting is False
