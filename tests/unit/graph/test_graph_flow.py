"""End-to-end tests for the clarification graph (no network, no API key)."""

from __future__ import annotations

import uuid

import pytest
from langgraph.types import Command

from app.config import settings
from app.graph.builder import build_graph
from app.graph.deps import GraphDeps
from app.knowledge.provider import FileKnowledgeProvider
from tests.fakes import FakeKnowledgeProvider, FakeLLMClient

# A candidate question that triggers 5 rules; one of them (metric_scope) is
# resolved by metric selection, leaving 4 blocking items -> capped at 3 rounds.
FIVE_RULE_QUESTION = "上个月外科住院患者的手术例数"


@pytest.fixture(scope="module")
def knowledge():
    return FileKnowledgeProvider("data")


def make_graph(knowledge_provider, llm):
    deps = GraphDeps(knowledge=knowledge_provider, llm=llm, settings=settings)
    return build_graph(deps)


def config():
    return {"configurable": {"thread_id": f"pytest-{uuid.uuid4().hex}"}}


def first_question(result):
    interrupts = result.get("__interrupt__") or ()
    assert interrupts, "expected the graph to pause for clarification"
    return interrupts[0].value


def answer(result, option_id):
    question = result["__interrupt__"][0].value
    return {"question_id": question["id"], "option_id": option_id}


def test_no_ambiguity_generates_sql(knowledge):
    llm = FakeLLMClient(sql="SELECT COUNT(*) AS visits FROM visit")
    graph = make_graph(knowledge, llm)

    result = graph.invoke(
        {"session_id": "s1", "query_id": "q1", "original_question": "2025年1月急诊人次"},
        config=config(),
    )

    assert "__interrupt__" not in result
    assert result["status"] == "ok"
    assert result["sql"] == "SELECT COUNT(*) AS visits FROM visit"
    assert result.get("clarification_history", []) == []
    assert result.get("clarification_round", 0) == 0
    assert result["selected_metric_id"] == "emergency_visits"
    # Only the SQL node should have called the LLM: metric was unambiguous.
    assert llm.calls.count("generate_sql") == 1
    assert "choose_metric" not in llm.calls


def test_two_round_clarification_then_sql(knowledge):
    llm = FakeLLMClient(sql="SELECT COUNT(*) FROM visit")
    graph = make_graph(knowledge, llm)
    cfg = config()

    result = graph.invoke({"original_question": "上个月门诊人次是多少"}, config=cfg)
    assert result["status"] == "needs_clarification"
    assert result["max_rounds"] == 2
    question = first_question(result)
    assert question["code"] == "time_range"
    assert question["round"] == 1
    assert [o["id"] for o in question["options"]] == ["natural_month", "last_30_days"]
    assert question["default_option_id"] == "natural_month"
    # No SQL yet.
    assert "generate_sql" not in llm.calls

    result = graph.invoke(Command(resume=answer(result, "natural_month")), config=cfg)
    question = first_question(result)
    assert question["code"] == "visit_type_scope"
    assert question["round"] == 2
    assert [o["id"] for o in question["options"]] == ["outpatient_only", "outpatient_emergency"]

    result = graph.invoke(Command(resume=answer(result, "outpatient_only")), config=cfg)
    assert "__interrupt__" not in result
    assert result["status"] == "ok"
    assert result["sql"] == "SELECT COUNT(*) FROM visit"
    assert result["clarification_round"] == 2
    assert [h["code"] for h in result["clarification_history"]] == [
        "time_range",
        "visit_type_scope",
    ]
    assert all(h["source"] == "user" for h in result["clarification_history"])
    # Each code asked once, and every answer reached the SQL prompt.
    assert result["asked_codes"] == ["time_range", "visit_type_scope"]
    assert "上个月" in llm.sql_requests[0]["question"]
    assert "自然月" in llm.sql_requests[0]["question"]
    assert "补充条件" in llm.sql_requests[0]["question"]


def test_skip_uses_default_and_records_assumption(knowledge):
    llm = FakeLLMClient(sql="SELECT 1")
    graph = make_graph(knowledge, llm)
    cfg = config()

    result = graph.invoke({"original_question": "上个月门诊人次是多少"}, config=cfg)
    result = graph.invoke(Command(resume=answer(result, "skip")), config=cfg)
    question = first_question(result)
    assert question["code"] == "visit_type_scope"

    result = graph.invoke(Command(resume=answer(result, "skip")), config=cfg)
    assert result["status"] == "ok"
    assert [h["source"] for h in result["clarification_history"]] == ["default", "default"]
    assert {a["code"] for a in result["assumptions"]} == {"time_range", "visit_type_scope"}
    assert {a["value"] for a in result["assumptions"]} == {"自然月", "仅门诊"}


def test_max_rounds_caps_clarification(knowledge):
    llm = FakeLLMClient(sql="SELECT 2")
    graph = make_graph(knowledge, llm)
    cfg = config()

    result = graph.invoke({"original_question": FIVE_RULE_QUESTION}, config=cfg)
    assert result["max_rounds"] == 3
    questions = []
    for round_no in (1, 2, 3):
        question = first_question(result)
        assert question["round"] == round_no
        questions.append(question["code"])
        result = graph.invoke(
            Command(resume=answer(result, question["default_option_id"])),
            config=cfg,
        )

    assert "__interrupt__" not in result
    assert result["status"] == "ok"
    assert result["clarification_round"] == 3
    assert len(result["clarification_history"]) == 3
    # No question is repeated, even though a 4th blocking item exists.
    assert len(set(questions)) == 3
    assert "department_entity" not in questions
    # Every answered slot must reach the SQL prompt, not only time/metric.
    rewritten = llm.sql_requests[0]["question"]
    assert "统计时间字段" in rewritten
    assert "患者范围" in rewritten


def test_resume_does_not_rerun_plan(knowledge):
    llm = FakeLLMClient(sql="SELECT 3")
    graph = make_graph(knowledge, llm)
    cfg = config()

    result = graph.invoke({"original_question": FIVE_RULE_QUESTION}, config=cfg)
    # time_range is asked first; time_field_scope (round 2) is the only
    # polished code inside the first three rounds.
    for _ in range(2):
        result = graph.invoke(Command(resume=answer(result, "natural_month")), config=cfg)

    assert llm.calls.count("polish_clarification") == 1

    result = graph.invoke(Command(resume=answer(result, "last_30_days")), config=cfg)
    assert result["status"] == "ok"
    assert llm.calls.count("polish_clarification") == 1


def test_polish_rewrites_wording_but_keeps_option_identity():
    provider = FakeKnowledgeProvider(ambiguities=[{
        "code": "department_entity",
        "category": "entity",
        "blocking": True,
        "priority": 60,
        "matched_term": "心内科",
        "question": "科室名称是精确匹配还是名称包含？",
        "options": ["精确匹配", "名称包含"],
        "default": "名称包含",
    }])
    llm = FakeLLMClient(
        sql="SELECT 4",
        polish={
            "question": "您说的“心内科”是指科室名称正好是“心内科”，还是名称里包含“心内科”也算？",
            "options": [
                {"id": "exact", "label": "名称正好是“心内科”"},
                {"id": "contains", "label": "名称包含“心内科”即可"},
            ],
        },
    )
    result = make_graph(provider, llm).invoke(
        {"original_question": "心内科人次"}, config=config()
    )

    question = first_question(result)
    assert question["question"].startswith("您说的“心内科”")
    assert [o["id"] for o in question["options"]] == ["exact", "contains"]
    # ids/values/defaults are frozen; only labels may change.
    assert [o["value"] for o in question["options"]] == ["精确匹配", "名称包含"]
    assert question["default_option_id"] == "contains"


def test_sql_failure_returns_error(knowledge):
    llm = FakeLLMClient(sql=None)
    result = make_graph(knowledge, llm).invoke(
        {"original_question": "2025年1月急诊人次"}, config=config()
    )

    assert result["status"] == "error"
    assert result["sql"] is None
    assert result["error"]["code"] == "sql_generation_failed"


def test_llm_supplement_adds_non_blocking_ambiguity(knowledge):
    llm = FakeLLMClient(
        sql="SELECT 5",
        supplements=[{
            "code": "llm_semantic_x",
            "category": "semantic",
            "matched_term": "急诊",
            "question": "“急诊”是否包含急诊留观？",
            "options": ["包含", "不包含"],
            "default": "包含",
            "blocking": False,
            "priority": 90,
            "source": "llm",
        }],
    )
    result = make_graph(knowledge, llm).invoke(
        {"original_question": "2025年1月急诊人次"}, config=config()
    )

    assert "__interrupt__" not in result
    assert result["status"] == "ok"
    codes = [item["code"] for item in result["ambiguities"]]
    assert "llm_semantic_x" in codes
