"""Command-line demo for the knowledge layer."""

from __future__ import annotations

import sys

from app.knowledge.loader import KnowledgeDataError
from app.knowledge.provider import FileKnowledgeProvider


def _safe_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _target_text(term) -> str:
    target = term.target or {}
    if term.target_type == "metric":
        return f"指标 {target.get('metric_id')}"
    if term.target_type == "table":
        return f"表 {target.get('table')}"
    if term.target_type == "time_range":
        return f"时间字段 {target.get('table')}.{target.get('column')}"
    text = f"{target.get('table')}.{target.get('column')}"
    if target.get("value"):
        text += f" = {target['value']}"
    elif target.get("values"):
        text += f" IN {target['values']}"
    return text


def main() -> None:
    _safe_stdout()
    if len(sys.argv) < 2:
        print('用法: python -m app.knowledge.inspect "你的问题"')
        return
    question = sys.argv[1]
    print(f"问题: {question}\n")

    try:
        provider = FileKnowledgeProvider("data")
    except KnowledgeDataError as exc:
        print("知识库加载失败:")
        print(exc)
        raise SystemExit(1)

    terms = provider.resolve_terms(question)
    print("1. 术语解析:")
    for term in terms:
        print(
            f"   - {term.term} -> {_target_text(term)} "
            f"({term.match or term.target_type}, {term.confidence}, {term.source})"
        )
    if not terms:
        print("   (未识别到术语)")

    metrics = provider.search_metrics(question)
    print("\n2. 指标候选:")
    for metric in metrics:
        print(f"   - {metric.name} ({metric.id}) gold={metric.gold_sql_ref}")
    if not metrics:
        print("   (未匹配到指标)")

    report = provider.detect_ambiguities(question, terms)
    print("\n3. 歧义检测:")
    for item in report.items:
        flag = "blocking" if item["blocking"] else "optional"
        print(f"   - [{item['code']}] {item['question']} ({flag})")
        print(f"     选项: {item['options']}；默认: {item['default']}")
    if not report.items:
        print("   (无歧义，可直接生成)")

    examples = provider.search_sql_examples(question, metrics=metrics, terms=terms)
    print("\n4. 相似 SQL 示例:")
    for example in examples:
        print(f"   - {example.id}: {example.question}")
    if not examples:
        print("   (无相似示例)")

    context = provider.get_schema_context(question)
    print("\n5. Schema 上下文:")
    print(f"   相关表: {context.tables}")
    print(f"   字符数: {context.char_count}")
    print("   关联关系:")
    for path in context.join_paths:
        print(f"     - {path['on']} ({path['type']})")
    print("   预览:")
    print("   " + context.prompt_text[:1000].replace("\n", "\n   "))


if __name__ == "__main__":
    main()
