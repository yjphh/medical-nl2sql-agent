"""Schema context must contain the columns and joins a metric actually needs."""

import pytest

from app.knowledge.provider import FileKnowledgeProvider


@pytest.fixture(scope="module")
def provider():
    return FileKnowledgeProvider("data")


def test_cost_tables_expose_join_edges(provider):
    names = [table.name for table in provider.schema_catalog.tables]
    pairs = {
        (path["from_table"], path["to_table"])
        for path in provider.schema_catalog.join_paths(names)
    }
    # Both fact tables are used by revenue/ratio metrics but had no edges.
    assert ("advice_cost", "visit") in pairs
    assert ("encounter_medical_cost", "visit") in pairs


def test_money_columns_survive_the_column_budget(provider):
    context = provider.get_schema_context("门诊药品收入")
    assert "total_cost" in context.columns["encounter_medical_cost"]


def test_pii_columns_are_not_ranked_first(provider):
    context = provider.get_schema_context("2025年1月门诊人次")
    columns = context.columns["visit"]
    assert columns[:5] != ["name", "id_card", "id_card_name"]
    assert "treatment_occurrence_time" in columns
