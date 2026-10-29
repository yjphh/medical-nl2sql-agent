"""Metric lookup and prompt-context rendering."""

from __future__ import annotations

from app.knowledge.models import MetricInfo


class MetricCatalog:
    def __init__(self, metrics: list[MetricInfo]) -> None:
        self.metrics = metrics
        self._map = {m.id: m for m in metrics}

    def get(self, metric_id: str) -> MetricInfo | None:
        return self._map.get(metric_id)

    def search(self, question: str, top_k: int = 5) -> list[MetricInfo]:
        q = (question or "").lower()
        scored: list[tuple[int, MetricInfo]] = []
        for metric in self.metrics:
            score = 0
            name = metric.name.lower()
            aliases = [a.lower() for a in metric.aliases]
            if name and name in q:
                score += 100
            if any(alias and alias in q for alias in aliases):
                score += 90
            if any(
                word and word in q
                for word in (metric.definition.lower(), metric.category.lower())
            ):
                score += 10
            if any(t.lower() in q for t in metric.related_tables):
                score += 5

            # Disambiguate generic aliases such as "次均费用" using the
            # explicit 门诊/住院 category words in the question.
            if "门诊" in q and "门急诊" in metric.category:
                score += 8
            if "住院" in q and "住院" in metric.category:
                score += 8
            if "手术" in q and "手术" in metric.category:
                score += 8

            # A small fallback for common generic wording.
            if (
                score == 0
                and "次均费用" in q
                and ("次均费用" in metric.name or "均次费用" in metric.name)
            ):
                score += 60
            if score == 0 and "药占比" in q and "药占比" in metric.name:
                score += 60
            if score == 0 and "医保" in q and "医保" in metric.name:
                score += 20

            if score > 0:
                scored.append((score, metric))

        # If a name/alias matched, do not dilute the result with weak
        # definition-only matches from unrelated metrics.
        strong = [pair for pair in scored if pair[0] >= 50]
        pool = strong or scored
        pool.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [m for _, m in pool[:top_k]]

    def to_prompt_context(self, metrics: list[MetricInfo]) -> str:
        if not metrics:
            return ""
        lines = ["## 指标口径"]
        for m in metrics:
            lines.append(f"\n### {m.name} ({m.id})")
            if m.aliases:
                lines.append(f"- 别名: {', '.join(m.aliases)}")
            if m.definition:
                lines.append(f"- 定义: {m.definition}")
            if m.formula:
                lines.append(f"- 公式: {m.formula}")
            if m.time_field:
                lines.append(f"- 时间字段: {m.time_field}")
            if m.related_tables:
                lines.append(f"- 相关表: {', '.join(m.related_tables)}")
            if m.default_interpretation:
                lines.append(f"- 默认口径: {m.default_interpretation}")
            if m.unit:
                lines.append(f"- 单位: {m.unit}")
            if m.gold_sql_ref:
                lines.append(f"- 标准 SQL: {m.gold_sql_ref}")
        return "\n".join(lines)
