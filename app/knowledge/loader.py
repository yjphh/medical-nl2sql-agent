"""Load and validate the six knowledge JSON files."""

from __future__ import annotations

import json
from pathlib import Path

from app.knowledge.models import (
    AmbiguityRule,
    GoldItem,
    KnowledgeBundle,
    MetricInfo,
    SQLExample,
    TableInfo,
    TermInfo,
)


class KnowledgeDataError(RuntimeError):
    """Raised when a knowledge file is missing, inconsistent, or malformed."""


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise KnowledgeDataError(f"missing file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise KnowledgeDataError(f"invalid JSON {path}: {exc}") from exc


def _table_index(tables: list[TableInfo]) -> tuple[dict[str, TableInfo], dict[str, set[str]]]:
    table_map = {t.name: t for t in tables}
    columns = {t.name: {c.name for c in t.columns} for t in tables}
    return table_map, columns


def _validate(
    bundle: KnowledgeBundle,
    table_map: dict[str, TableInfo],
    columns: dict[str, set[str]],
    metric_ids: set[str],
    gold_ids: set[str],
) -> list[str]:
    errors: list[str] = []

    for table in bundle.tables:
        for pk in table.primary_key:
            if pk not in columns.get(table.name, set()):
                errors.append(f"schema: {table.name} primary key {pk} missing")
        for rel in table.relationships:
            target = table_map.get(rel.to_table)
            if target is None:
                errors.append(f"schema: {table.name} relationship target {rel.to_table} missing")
                continue
            for part in rel.on.replace("=", " ").split():
                if "." not in part:
                    continue
                tname, cname = part.strip().split(".", 1)
                if tname not in table_map:
                    errors.append(f"schema: {table.name} relationship references table {tname}")
                elif cname not in columns.get(tname, set()):
                    errors.append(f"schema: {table.name} relationship references {tname}.{cname}")

    for metric in bundle.metrics:
        if metric.gold_sql_ref and metric.gold_sql_ref not in gold_ids:
            errors.append(f"metrics: {metric.id} gold_sql_ref {metric.gold_sql_ref} missing")
        for table_name in metric.related_tables:
            if table_name not in table_map:
                errors.append(f"metrics: {metric.id} related table {table_name} missing")
        if metric.time_field and "." in metric.time_field:
            tname, cname = metric.time_field.split(".", 1)
            if tname not in table_map or cname not in columns.get(tname, set()):
                errors.append(f"metrics: {metric.id} time_field {metric.time_field} missing")

    for gold in bundle.gold_items:
        if gold.metric_id not in metric_ids:
            errors.append(f"gold: {gold.id} metric_id {gold.metric_id} missing")
        for table_name in gold.tables:
            if table_name not in table_map:
                errors.append(f"gold: {gold.id} table {table_name} missing")

    for term in bundle.terms:
        target = term.target or {}
        if term.target_type == "metric":
            if target.get("metric_id") not in metric_ids:
                errors.append(f"glossary: {term.term} metric target missing")
        elif term.target_type == "table":
            if target.get("table") not in table_map:
                errors.append(f"glossary: {term.term} table target missing")
        else:
            table_name = target.get("table")
            column_name = target.get("column")
            if table_name not in table_map or column_name not in columns.get(table_name, set()):
                errors.append(f"glossary: {term.term} target {table_name}.{column_name} missing")

    for rule in bundle.ambiguities:
        for path in rule.related_columns:
            if "." not in path:
                errors.append(f"ambiguities: {rule.code} related column {path} malformed")
                continue
            table_name, column_name = path.split(".", 1)
            if table_name not in table_map or column_name not in columns.get(table_name, set()):
                errors.append(f"ambiguities: {rule.code} related column {path} missing")
        for metric_id in rule.related_metrics:
            if metric_id not in metric_ids:
                errors.append(f"ambiguities: {rule.code} related metric {metric_id} missing")

    for example in bundle.examples:
        for table_name in example.tables:
            if table_name not in table_map:
                errors.append(f"examples: {example.id} table {table_name} missing")

    return errors


def load_knowledge(data_dir: str | Path = "data") -> KnowledgeBundle:
    data_dir = Path(data_dir)
    schema_raw = _load_json(data_dir / "schema.json")
    metrics_raw = _load_json(data_dir / "metrics.json")
    gold_raw = _load_json(data_dir / "gold_sql.json")
    glossary_raw = _load_json(data_dir / "glossary.json")
    ambiguities_raw = _load_json(data_dir / "ambiguities.json")
    examples_raw = _load_json(data_dir / "sql_examples_sqlite.json")

    schema_version = str(schema_raw.get("version", ""))
    versions = {
        "schema": schema_version,
        "metrics": str(metrics_raw.get("version", "")),
        "gold_sql": str(gold_raw.get("version", "")),
        "glossary": str(glossary_raw.get("version", "")),
        "ambiguities": str(ambiguities_raw.get("version", "")),
        "sql_examples_sqlite": str(examples_raw.get("version", "")),
        "sql_examples_schema": str(examples_raw.get("schema_version", "")),
    }

    tables = [TableInfo.from_dict(t) for t in schema_raw.get("tables", [])]
    metrics = [MetricInfo.from_dict(m) for m in metrics_raw.get("metrics", [])]
    gold_items = [GoldItem.from_dict(g) for g in gold_raw.get("items", [])]
    terms = [TermInfo.from_dict(t) for t in glossary_raw.get("terms", [])]
    ambiguities = [AmbiguityRule.from_dict(r) for r in ambiguities_raw.get("rules", [])]
    examples = [SQLExample.from_dict(e) for e in examples_raw.get("examples", [])]

    bundle = KnowledgeBundle(
        data_dir=str(data_dir),
        schema_version=schema_version,
        dialect=str(schema_raw.get("dialect", "")),
        tables=tables,
        metrics=metrics,
        gold_items=gold_items,
        terms=terms,
        needs_value_dictionary=list(glossary_raw.get("needs_value_dictionary", [])),
        ambiguities=ambiguities,
        examples=examples,
        versions=versions,
    )

    errors: list[str] = []
    if schema_version and any(v != schema_version for v in versions.values()):
        errors.append(f"version mismatch: {versions}")
    table_map, columns = _table_index(tables)
    metric_ids = {m.id for m in metrics}
    gold_ids = {g.id for g in gold_items}
    errors.extend(_validate(bundle, table_map, columns, metric_ids, gold_ids))

    if errors:
        raise KnowledgeDataError("\n".join(errors))
    return bundle
