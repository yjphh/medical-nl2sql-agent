"""Retrieve relevant SQL examples by metric, table, and text overlap."""

from __future__ import annotations

import re

from app.knowledge.models import MetricInfo, SQLExample, TermResolution


def _bigrams(text: str) -> set[str]:
    text = re.sub(r"\s+", "", text or "").lower()
    text = re.sub(r"[^\w一-龥]", "", text)
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


class ExampleCatalog:
    def __init__(self, examples: list[SQLExample]) -> None:
        self.examples = examples

    def search(
        self,
        question: str,
        metrics: list[MetricInfo] | None = None,
        terms: list[TermResolution] | None = None,
        top_k: int = 3,
    ) -> list[SQLExample]:
        q = question or ""
        q_grams = _bigrams(q)
        wanted_tables: set[str] = set()
        for metric in metrics or []:
            wanted_tables.update(metric.related_tables)
            for name in [metric.name, *metric.aliases]:
                if name and name in q:
                    wanted_tables.update(metric.related_tables)
        for term in terms or []:
            table = (term.target or {}).get("table")
            if table:
                wanted_tables.add(table)

        keywords = {
            "排名", "占比", "比例", "平均", "人次", "费用", "收入",
            "手术", "住院", "门诊", "检查", "检验", "诊断", "医保",
        }

        scored: list[tuple[int, SQLExample]] = []
        for example in self.examples:
            score = 0
            for metric in metrics or []:
                names = [metric.name, *metric.aliases]
                if any(name and name in example.question for name in names):
                    score += 50
            score += 10 * len(wanted_tables.intersection(example.tables))
            overlap = len(q_grams.intersection(_bigrams(example.question)))
            score += min(overlap * 3, 30)
            example_text = f"{example.question} {example.pattern} {example.notes}"
            if any(word in q and word in example_text for word in keywords):
                score += 5
            if score > 0:
                scored.append((score, example))

        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [example for _, example in scored[:top_k]]

    @staticmethod
    def to_prompt_context(examples: list[SQLExample]) -> str:
        if not examples:
            return ""
        lines = ["## 相似 SQL 示例"]
        for example in examples:
            lines.append(f"\n### {example.id}")
            lines.append(f"问题: {example.question}")
            lines.append(f"表: {', '.join(example.tables)}；模式: {example.pattern}")
            lines.append("SQL:")
            lines.append(example.sql[:1200])
        return "\n".join(lines)
