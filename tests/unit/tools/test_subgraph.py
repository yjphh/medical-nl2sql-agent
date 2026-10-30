"""Regression tests: a subgraph node must not lose parent-facing fields.

LangGraph silently drops keys that exist in the subgraph state but not in the
parent ``GraphState``.  The SQL generation subgraph shares ``GraphState`` and
these tests pin the six fields the API depends on.
"""

from types import SimpleNamespace

from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.graph.state import GraphState
from app.graph.subgraphs.sql_generation import build_sql_subgraph
from tests.fakes import FakeExecutor, FakeSQLAgentLLM

SQL_OK = "SELECT COUNT(1) AS c FROM visit"
SCHEMA_CONTEXT = {"prompt_text": "schema"}


def _deps():
    return SimpleNamespace(settings=settings)


def _parent(sql_llm, executor):
    subgraph = build_sql_subgraph(
        _deps(),
        llm=sql_llm,
        executor=executor,
        examples=[],
        allowed_tables=["visit"],
    )
    graph = StateGraph(GraphState)
    graph.add_node("generate_sql", subgraph)
    graph.add_edge(START, "generate_sql")
    graph.add_edge("generate_sql", END)
    return graph.compile()


def test_parent_receives_all_sql_fields():
    sql_llm = FakeSQLAgentLLM([[{"sql": SQL_OK, "assumptions": ["按 visit 粒度统计"]}]])
    result = _parent(sql_llm, FakeExecutor()).invoke({
        "original_question": "问题",
        "schema_context": SCHEMA_CONTEXT,
    })

    assert result["sql"] == SQL_OK
    assert result["sql_source"] == "sqlite"
    assert result["status"] == "ok"
    assert result["error"] is None
    assert result["sql_assumptions"]
    assert "按 visit 粒度统计" in result["sql_assumptions"]
    assert result["trace"][0]["node"] == "generate_sql"
    assert result["trace"][0]["verified"] is True


def test_error_path_keeps_status_and_error():
    result = _parent(FakeSQLAgentLLM([[]]), FakeExecutor()).invoke({
        "original_question": "问题",
        "schema_context": SCHEMA_CONTEXT,
    })

    assert result["sql"] is None
    assert result["status"] == "error"
    assert result["error"]["code"] == "sql_generation_failed"
    assert result["trace"][0]["ok"] is False


def test_rewritten_question_and_schema_links_reach_the_llm():
    sql_llm = FakeSQLAgentLLM([[{"sql": SQL_OK}]])
    links = [{"table": "visit", "columns": ["visit_id"], "relationship": ""}]
    _parent(sql_llm, FakeExecutor()).invoke({
        "original_question": "上个月门诊人次",
        "rewritten_question": "上个月门诊人次\n补充条件：时间口径：自然月。",
        "schema_links": links,
        "schema_context": SCHEMA_CONTEXT,
    })

    call = sql_llm.generate_calls[0]
    assert call["question"].startswith("上个月门诊人次\n补充条件")
    assert call["schema_links"] == links


def test_result_rows_never_reach_the_parent_state():
    sql_llm = FakeSQLAgentLLM([[{"sql": SQL_OK}]])
    executor = FakeExecutor(rows_by_sql={SQL_OK: [("PATIENT-SECRET",)]})
    result = _parent(sql_llm, executor).invoke({
        "original_question": "问题",
        "schema_context": SCHEMA_CONTEXT,
    })

    assert result["status"] == "ok"
    assert "PATIENT-SECRET" not in str(result)
    assert result["trace"][0]["result_hash"]
