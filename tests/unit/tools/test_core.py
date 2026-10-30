from app.tools.sql_agent.core import estimate_n, generate_sql
from tests.fakes import FakeExecutor, FakeSQLAgentLLM

SCHEMA = "schema"
SQL_A = "SELECT COUNT(1) AS c FROM visit"
SQL_B = "SELECT COUNT(visit_id) AS c FROM visit"
SQL_C = "SELECT COUNT(dcid) AS c FROM visit"


def _run(batches, repairs=None, executor=None, **kwargs):
    llm = FakeSQLAgentLLM(batches, repairs)
    executor = executor or FakeExecutor()
    result = generate_sql("测试问题", SCHEMA, llm=llm, executor=executor, **kwargs)
    return result, llm, executor


def test_single_candidate_success():
    result, _llm, executor = _run([[{"sql": SQL_A}]], n=1)
    assert result.sql == SQL_A
    assert result.verified is True
    assert result.stats["llm_calls"] == 1
    assert result.stats["executions"] == 1
    assert executor.calls == [SQL_A]


def test_failed_candidate_is_repaired():
    executor = FakeExecutor(errors_by_sql={SQL_A: "no such column: nope"})
    result, llm, _ = _run(
        [[{"sql": SQL_A}]],
        repairs=[{"sql": SQL_B}],
        executor=executor,
        n=1,
    )
    assert result.sql == SQL_B
    assert result.verified is True
    assert result.stats["repairs"] == 1
    assert len(llm.repair_calls) == 1
    assert result.candidates[0].repaired is False
    assert result.candidates[1].repaired is True


def test_pii_candidate_is_rejected_without_execution():
    result, _, executor = _run([[{"sql": "SELECT id_card FROM visit"}]], n=1)
    assert result.sql is None
    assert result.error == "sql_no_valid_candidate"
    assert executor.calls == []
    assert result.candidates[0].safe is False
    assert result.candidates[0].safety_reason == "pii_in_select"


def test_two_candidates_agree():
    executor = FakeExecutor(rows_by_sql={SQL_A: [("5",)], SQL_B: [("5",)]})
    result, _, _ = _run([[{"sql": SQL_A}, {"sql": SQL_B}]], executor=executor, n=2)
    assert result.sql in (SQL_A, SQL_B)
    assert result.verified is True
    assert "一致" in result.reason
    assert result.stats["llm_calls"] == 1


def test_disagreement_escalates_to_third_candidate():
    executor = FakeExecutor(
        rows_by_sql={SQL_A: [("1",)], SQL_B: [("2",)], SQL_C: [("1",)]}
    )
    result, llm, _ = _run(
        [[{"sql": SQL_A}, {"sql": SQL_B}], [{"sql": SQL_C}]],
        executor=executor,
        n=2,
    )
    # SQL_A 和 SQL_C 结果一致，构成 2/3 多数；选中组内静态分最高者。
    assert result.sql in (SQL_A, SQL_C)
    assert result.verified is True
    assert result.result_empty is False
    assert len(llm.generate_calls) == 2
    assert {"node": "select", "action": "escalate"} in result.trace


def test_no_consensus_falls_back_to_static_score():
    executor = FakeExecutor(
        rows_by_sql={SQL_A: [("1",)], SQL_B: [("2",)], SQL_C: [("3",)]}
    )
    result, _, _ = _run(
        [[{"sql": SQL_A}, {"sql": SQL_B}], [{"sql": SQL_C}]],
        executor=executor,
        n=2,
    )
    assert result.sql is not None
    assert result.verified is False
    assert "无共识" in result.reason


def test_empty_results_count_as_agreement():
    executor = FakeExecutor(rows_by_sql={SQL_A: [], SQL_B: []})
    result, _, _ = _run([[{"sql": SQL_A}, {"sql": SQL_B}]], executor=executor, n=2)
    assert result.verified is True
    assert result.result_empty is True


def test_empty_versus_non_empty_triggers_escalation():
    executor = FakeExecutor(
        rows_by_sql={SQL_A: [], SQL_B: [("1",)], SQL_C: []}
    )
    result, _, _ = _run(
        [[{"sql": SQL_A}, {"sql": SQL_B}], [{"sql": SQL_C}]],
        executor=executor,
        n=2,
    )
    assert result.verified is True
    assert result.result_empty is True
    assert result.sql in (SQL_A, SQL_C)


def test_repair_budget_exhausted_returns_error():
    executor = FakeExecutor(errors_by_sql={SQL_A: "syntax error"})
    result, llm, _ = _run(
        [[{"sql": SQL_A}]],
        repairs=[{"sql": SQL_B}],
        executor=executor,
        n=1,
        max_repair_rounds=0,
    )
    assert result.sql is None
    assert result.error == "sql_no_valid_candidate"
    assert llm.repair_calls == []


def test_empty_generation_returns_error():
    result, _, executor = _run([[]], n=1)
    assert result.sql is None
    assert result.error == "empty_sql"
    assert executor.calls == []


def test_duplicate_sql_is_executed_once():
    executor = FakeExecutor()
    result, _, _ = _run(
        [[{"sql": SQL_A}, {"sql": "SELECT  COUNT(1) AS c FROM visit;"}]],
        executor=executor,
        n=2,
    )
    assert result.stats["executions"] == 1
    assert len(result.candidates) == 1


def test_estimate_n_heuristic():
    assert estimate_n("2025年1月急诊人次") == 1
    assert estimate_n("上个月检查项目排名") == 2
    assert estimate_n("各科室门诊人次占比") == 2
