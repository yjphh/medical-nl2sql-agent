"""HTTP-level tests for the clarification loop, using a fake LLM."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import runtime
from app.config import settings
from app.graph.builder import build_graph
from app.graph.deps import GraphDeps
from app.knowledge.provider import FileKnowledgeProvider
from app.main import app
from tests.fakes import FakeExecutor, FakeLLMClient, FakeSQLAgentLLM

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def fake_graph():
    deps = GraphDeps(
        knowledge=FileKnowledgeProvider("data"),
        llm=FakeLLMClient(),
        settings=settings,
        sql_llm=FakeSQLAgentLLM(
            [],
            default_batch=[{"sql": "SELECT COUNT(*) AS visits FROM visit"}],
        ),
        sql_executor=FakeExecutor(),
    )
    runtime.set_deps(deps)
    runtime.set_graph(build_graph(deps))
    yield
    runtime.reset()


def new_session() -> str:
    response = client.post("/sessions", json={"user_id": "u-api"})
    assert response.status_code == 200
    return response.json()["session_id"]


def test_query_without_ambiguity_returns_sql():
    session_id = new_session()
    response = client.post(
        "/queries",
        json={"session_id": session_id, "question": "2025年1月急诊人次"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["sql"] == "SELECT COUNT(*) AS visits FROM visit"
    assert body["sql_source"] == "sqlite"
    assert body["clarification"] is None
    assert "未执行" in body["message"]
    # schema_links must survive finalize -> subgraph -> API response.
    assert body["schema_links"]
    link = body["schema_links"][0]
    assert {"table", "columns", "relationship"} <= set(link)

    status = client.get(f"/queries/{body['query_id']}")
    assert status.status_code == 200
    assert status.json()["status"] == "ok"


def test_direct_mode_skips_clarification():
    """``mode=direct`` is the baseline: one model call, no clarifying round."""
    session_id = new_session()
    body = client.post(
        "/queries",
        json={
            "session_id": session_id,
            "question": "上个月门诊人次是多少",
            "mode": "direct",
        },
    ).json()

    assert body["status"] == "ok"
    assert body["clarification"] is None
    assert body["sql"] == "SELECT COUNT(*) AS visits FROM visit"
    assert body["sql_source"] == "sqlite"
    assert body["sql_confidence"] == 0.9
    assert body["trace"][0]["node"] == "direct_sql"
    assert body["trace"][0]["mode"] == "direct"


def test_clarification_round_trip():
    session_id = new_session()
    response = client.post(
        "/queries",
        json={"session_id": session_id, "question": "上个月门诊人次是多少"},
    )
    body = response.json()
    assert body["status"] == "needs_clarification"
    assert body["sql"] is None
    clarification = body["clarification"]
    assert clarification["code"] == "time_range"
    assert clarification["round"] == 1
    assert clarification["max_rounds"] == 2
    assert [o["id"] for o in clarification["options"]] == ["natural_month", "last_30_days"]
    assert clarification["default_option_id"] == "natural_month"

    response = client.post(
        f"/queries/{body['query_id']}/answers",
        json={
            "session_id": session_id,
            "answer": {
                "question_id": clarification["question_id"],
                "option_id": "natural_month",
            },
        },
    )
    body = response.json()
    assert body["status"] == "needs_clarification"
    assert body["clarification"]["code"] == "visit_type_scope"
    assert body["clarification"]["round"] == 2

    response = client.post(
        f"/queries/{body['query_id']}/answers",
        json={
            "session_id": session_id,
            "answer": {
                "question_id": body["clarification"]["question_id"],
                "option_id": "outpatient_only",
            },
        },
    )
    body = response.json()
    assert body["status"] == "ok"
    assert body["sql"] == "SELECT COUNT(*) AS visits FROM visit"
    assert [h["code"] for h in body["clarification_history"]] == [
        "time_range",
        "visit_type_scope",
    ]


def test_free_text_answer_is_accepted():
    session_id = new_session()
    body = client.post(
        "/queries",
        json={"session_id": session_id, "question": "上个月门诊人次是多少"},
    ).json()
    response = client.post(
        f"/queries/{body['query_id']}/answers",
        json={"answer": {"text": "最近 30 天"}},
    )
    assert response.status_code == 200
    assert response.json()["status"] in {"needs_clarification", "ok"}


def test_answer_after_completion_returns_409():
    session_id = new_session()
    body = client.post(
        "/queries",
        json={"session_id": session_id, "question": "2025年1月急诊人次"},
    ).json()
    assert body["status"] == "ok"
    response = client.post(
        f"/queries/{body['query_id']}/answers",
        json={"answer": {"option_id": "natural_month"}},
    )
    assert response.status_code == 409


def test_get_query_returns_the_pending_clarification():
    """A reloaded or timed-out client must be able to resume, not restart."""
    session_id = new_session()
    body = client.post(
        "/queries",
        json={"session_id": session_id, "question": "上个月门诊人次是多少"},
    ).json()
    assert body["status"] == "needs_clarification"

    recovered = client.get(f"/queries/{body['query_id']}")
    assert recovered.status_code == 200
    payload = recovered.json()
    assert payload["status"] == "needs_clarification"
    assert payload["clarification"]["question_id"] == body["clarification"]["question_id"]
    assert payload["clarification"]["code"] == "time_range"


def test_client_request_id_makes_retry_idempotent():
    session_id = new_session()
    request_id = "6f1b1b9e-0000-4000-8000-000000000002"
    first = client.post(
        "/queries",
        json={
            "session_id": session_id,
            "question": "2025年1月急诊人次",
            "request_id": request_id,
        },
    ).json()
    second = client.post(
        "/queries",
        json={
            "session_id": session_id,
            "question": "2025年1月急诊人次",
            "request_id": request_id,
        },
    ).json()
    assert first["query_id"] == request_id
    assert second["query_id"] == request_id
    assert second["status"] == first["status"]
    assert second["sql"] == first["sql"]


def test_unknown_query_returns_404():
    response = client.post(
        "/queries/not-a-query/answers",
        json={"answer": {"option_id": "natural_month"}},
    )
    assert response.status_code == 404


def test_health_probes_database_and_knowledge():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok", body
    assert body["checks"]["database"]["ok"]
    assert body["checks"]["knowledge"]["ok"]


def test_graph_failure_is_reported_in_chinese_with_detail(monkeypatch):
    """The user sees a human sentence; the stack text is folded away."""

    class Boom:
        def invoke(self, *args, **kwargs):
            raise TimeoutError("Request timed out after 30s")

    monkeypatch.setattr("app.api.routes_queries.get_graph", lambda: Boom())
    session_id = new_session()
    body = client.post(
        "/queries",
        json={"session_id": session_id, "question": "2025年1月急诊人次"},
    ).json()

    assert body["status"] == "error"
    assert body["error"]["code"] == "llm_timeout"
    assert "超时" in body["error"]["message"]
    assert "TimeoutError" in body["error"]["detail"]
