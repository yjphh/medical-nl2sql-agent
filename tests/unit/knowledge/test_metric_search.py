"""Regression tests for metric retrieval.

These phrasings used to resolve to the wrong metric, producing SQL that ran
successfully and "verified" against the wrong number.
"""

import pytest

from app.knowledge.provider import FileKnowledgeProvider


@pytest.fixture(scope="module")
def provider():
    return FileKnowledgeProvider("data")


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("住院总收入", "inpatient_total_cost"),
        ("门诊量", "outpatient_visits"),
        ("2025年1月急诊人次", "emergency_visits"),
        ("四级手术占比", "level4_surgeries"),
        ("住院药占比", "inpatient_drug_cost"),
        ("门诊药品收入", "outpatient_drug_revenue"),
        ("各科室门诊人次", "outpatient_visits"),
    ],
)
def test_common_phrasings_resolve_to_the_expected_metric(provider, question, expected):
    hits = provider.search_metrics(question)
    assert hits, question
    assert hits[0].id == expected


def test_compound_scope_keeps_both_sides(provider):
    """"门急诊人次" is a compound; both 门诊 and 急诊 metrics stay candidates."""
    ids = [metric.id for metric in provider.search_metrics("门急诊人次")]
    assert {"outpatient_visits", "emergency_visits"} <= set(ids)


def test_nested_match_does_not_outrank_the_longer_phrase(provider):
    """"急诊人次" is nested inside "门急诊人次" and must not win outright."""
    hits = provider.search_metrics("门急诊人次")
    assert hits[0].id != "emergency_visits" or len(hits) > 1
