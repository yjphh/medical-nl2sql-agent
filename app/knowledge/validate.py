"""Validate the six knowledge JSON files and their cross references."""

from __future__ import annotations

import argparse
import sys

from app.knowledge.loader import KnowledgeDataError, load_knowledge
from app.knowledge.models import KnowledgeBundle


def _safe_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def validate_bundle(bundle: KnowledgeBundle) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if len(bundle.tables) < 13:
        errors.append(f"schema: expected >=13 tables, got {len(bundle.tables)}")
    if sum(len(t.columns) for t in bundle.tables) < 700:
        errors.append("schema: too few columns")
    if len(bundle.metrics) != 35:
        errors.append(f"metrics: expected 35, got {len(bundle.metrics)}")
    sql_gold = [g for g in bundle.gold_items if g.kind == "sql"]
    formula_gold = [g for g in bundle.gold_items if g.kind == "formula"]
    if len(bundle.gold_items) != 35:
        errors.append(f"gold: expected 35, got {len(bundle.gold_items)}")
    if len(sql_gold) != 29:
        errors.append(f"gold: expected 29 sql, got {len(sql_gold)}")
    if len(formula_gold) != 6:
        errors.append(f"gold: expected 6 formula, got {len(formula_gold)}")
    if any(not g.tables for g in bundle.gold_items):
        errors.append("gold: some items have empty tables")
    if len(bundle.terms) < 30:
        errors.append(f"glossary: expected >=30 terms, got {len(bundle.terms)}")
    if len(bundle.ambiguities) < 6:
        errors.append(f"ambiguities: expected >=6, got {len(bundle.ambiguities)}")
    if not (10 <= len(bundle.examples) <= 15):
        errors.append(f"examples: expected 10-15, got {len(bundle.examples)}")

    metric_ids = [m.id for m in bundle.metrics]
    if len(metric_ids) != len(set(metric_ids)):
        errors.append("metrics: duplicate ids")
    metric_names = [m.name for m in bundle.metrics]
    if len(metric_names) != len(set(metric_names)):
        errors.append("metrics: duplicate names")
    gold_ids = [g.id for g in bundle.gold_items]
    if len(gold_ids) != len(set(gold_ids)):
        errors.append("gold: duplicate ids")

    for term in bundle.terms:
        if not term.source:
            errors.append(f"glossary: {term.term} missing source")
    term_names = [t.term for t in bundle.terms]
    duplicates = {name for name in term_names if term_names.count(name) > 1}
    if duplicates:
        warnings.append(f"glossary: duplicate terms {sorted(duplicates)[:10]}")

    for rule in bundle.ambiguities:
        if not rule.category:
            errors.append(f"ambiguities: {rule.code} missing category")
        if not isinstance(rule.priority, int):
            errors.append(f"ambiguities: {rule.code} priority is not int")

    return errors, warnings


def main() -> None:
    _safe_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    try:
        bundle = load_knowledge(args.data_dir)
    except KnowledgeDataError as exc:
        print("知识库加载失败:")
        print(exc)
        raise SystemExit(1)

    errors, warnings = validate_bundle(bundle)
    for warning in warnings:
        print("WARN:", warning)
    if errors:
        for error in errors:
            print("ERROR:", error)
        raise SystemExit(1)

    print("知识库校验通过")
    print(f"  schema: {len(bundle.tables)} tables, "
          f"{sum(len(t.columns) for t in bundle.tables)} columns")
    print(f"  metrics: {len(bundle.metrics)}")
    print(f"  gold: {len(bundle.gold_items)} "
          f"({len([g for g in bundle.gold_items if g.kind == 'sql'])} sql, "
          f"{len([g for g in bundle.gold_items if g.kind == 'formula'])} formula)")
    print(f"  glossary: {len(bundle.terms)} terms")
    print(f"  ambiguities: {len(bundle.ambiguities)} rules")
    print(f"  examples: {len(bundle.examples)}")
    print(f"  needs_value_dictionary: {len(bundle.needs_value_dictionary)}")


if __name__ == "__main__":
    main()
