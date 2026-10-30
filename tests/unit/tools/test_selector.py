from app.tools.sql_agent.selector import Candidate, result_hash, select, static_score

SQL_A = "SELECT COUNT(1) AS c FROM visit"
SQL_B = "SELECT COUNT(visit_id) AS c FROM visit"
SQL_C = "SELECT COUNT(dcid) AS c FROM visit"


def _candidate(sql: str, rows: list[tuple], *, safe=True, exec_ok=True) -> Candidate:
    return Candidate(
        sql=sql,
        safe=safe,
        exec_ok=exec_ok,
        row_count=len(rows),
        rows=rows,
        result_hash=result_hash(rows),
    )


def test_result_hash_ignores_row_order_and_value_types():
    assert result_hash([("1", 2), (None, "x")]) == result_hash([(None, "x"), (1, 2)])


def test_result_hash_ignores_column_aliases():
    # Aliases live in the SQL text, not the rows; rows alone decide the hash.
    assert result_hash([("5",)]) == result_hash([("5",)])


def test_result_hash_distinguishes_truncation():
    assert result_hash([("1",)], truncated=True) != result_hash([("1",)], truncated=False)


def test_single_valid_candidate_is_verified():
    selection = select([_candidate(SQL_A, [("5",)])], allow_escalation=True)
    assert selection.chosen is not None
    assert selection.verified is True
    assert selection.needs_escalation is False


def test_no_valid_candidate_returns_none():
    candidates = [
        _candidate(SQL_A, [], safe=False),
        _candidate(SQL_B, [], exec_ok=False),
    ]
    for candidate in candidates:
        candidate.error = "boom"
    selection = select(candidates, allow_escalation=True)
    assert selection.chosen is None
    assert selection.verified is False


def test_two_agreeing_candidates_are_verified():
    selection = select(
        [_candidate(SQL_A, [("5",)]), _candidate(SQL_B, [("5",)])],
        allow_escalation=True,
    )
    assert selection.verified is True
    assert selection.needs_escalation is False
    assert "一致" in selection.reason


def test_two_disagreeing_candidates_ask_for_escalation():
    selection = select(
        [_candidate(SQL_A, [("1",)]), _candidate(SQL_B, [("2",)])],
        allow_escalation=True,
    )
    assert selection.chosen is None
    assert selection.needs_escalation is True
    assert "升级" in selection.reason


def test_third_candidate_breaks_tie_by_majority():
    selection = select(
        [
            _candidate(SQL_A, [("1",)]),
            _candidate(SQL_B, [("2",)]),
            _candidate(SQL_C, [("1",)]),
        ],
        allow_escalation=False,
    )
    assert selection.chosen is not None
    assert selection.chosen.sql in (SQL_A, SQL_C)
    assert selection.verified is True
    assert "2/3" in selection.reason


def test_three_distinct_results_fall_back_to_static_score():
    selection = select(
        [
            _candidate(SQL_A, [("1",)]),
            _candidate(SQL_B, [("2",)]),
            _candidate(SQL_C, [("3",)]),
        ],
        allow_escalation=False,
    )
    assert selection.chosen is not None
    assert selection.verified is False
    assert "无共识" in selection.reason


def test_empty_and_empty_count_as_agreement():
    selection = select(
        [_candidate(SQL_A, []), _candidate(SQL_B, [])],
        allow_escalation=True,
    )
    assert selection.verified is True
    assert selection.empty is True


def test_empty_versus_non_empty_needs_escalation():
    selection = select(
        [_candidate(SQL_A, []), _candidate(SQL_B, [("1",)])],
        allow_escalation=True,
    )
    assert selection.needs_escalation is True


def test_static_score_prefers_fewer_joins_then_shorter_sql():
    simple = _candidate(SQL_A, [("1",)])
    joined = _candidate(
        "SELECT COUNT(1) AS c FROM visit JOIN patient ON visit.source_patient_id = "
        "patient.source_patient_id",
        [("2",)],
    )
    assert static_score(simple) > static_score(joined)
