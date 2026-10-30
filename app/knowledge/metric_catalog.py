"""Metric lookup and prompt-context rendering."""

from __future__ import annotations

from app.knowledge.models import MetricInfo


def _bigrams(text: str) -> set[str]:
    text = (text or "").lower()
    if len(text) < 2:
        return {text} if text else set()
    return {text[index : index + 2] for index in range(len(text) - 1)}


def _coverage(term: str, question: str) -> float:
    """Share of ``term``'s character bigrams present in ``question``.

    Used only when the term is not a literal substring: "住院收入" does not
    appear in "住院总收入", but it covers 2 of its 3 bigrams.
    """
    term_grams = _bigrams(term)
    if not term_grams:
        return 0.0
    return len(term_grams & _bigrams(question)) / len(term_grams)


def _scope_words(question: str) -> set[str]:
    scope: set[str] = set()
    if "住院" in question:
        scope.add("住院")
    if "门诊" in question or "门急诊" in question or "急诊" in question:
        scope.add("门急诊")
    return scope


class MetricCatalog:
    def __init__(self, metrics: list[MetricInfo]) -> None:
        self.metrics = metrics
        self._map = {m.id: m for m in metrics}

    def get(self, metric_id: str) -> MetricInfo | None:
        return self._map.get(metric_id)

    def search(self, question: str, top_k: int = 5) -> list[MetricInfo]:
        q = (question or "").lower()
        scope = _scope_words(q)
        entries = [
            entry
            for metric in self.metrics
            if (entry := self._score(metric, q, scope))["score"] > 0
        ]

        # A match nested inside a longer match is the less specific reading:
        # "急诊人次" inside "门急诊人次" must not beat the longer phrase.
        for entry in entries:
            span = entry["span"]
            if span is None:
                continue
            start, length = span
            for other in entries:
                other_span = other["span"]
                if other is entry or other_span is None:
                    continue
                start2, length2 = other_span
                if length2 > length and start2 <= start and start + length <= start2 + length2:
                    entry["score"] -= 40
                    break

        scored = [(entry["score"], entry["metric"]) for entry in entries if entry["score"] > 0]
        # If a name/alias matched, do not dilute the result with weak
        # definition-only matches from unrelated metrics.
        strong = [pair for pair in scored if pair[0] >= 50]
        pool = strong or scored
        pool.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [m for _, m in pool[:top_k]]

    @staticmethod
    def _score(metric: MetricInfo, q: str, scope: set[str]) -> dict:
        score = 0
        span: tuple[int, int] | None = None
        best_length = 0
        terms = [(metric.name, 100)] + [(alias, 90) for alias in metric.aliases]
        for term, weight in terms:
            term_l = (term or "").lower()
            if not term_l:
                continue
            position = q.find(term_l)
            if position >= 0 and len(term_l) > best_length:
                best_length = len(term_l)
                span = (position, len(term_l))
                score = max(score, weight)

        if score == 0:
            # No literal hit: tolerate reordered compounds such as
            # "住院收入" vs "住院总收入".
            for term, weight in terms:
                coverage = _coverage(term, q)
                if coverage >= 0.6:
                    fuzzy = int(weight * coverage * 0.6)
                    if fuzzy > score:
                        score = fuzzy
                        span = None

        if score == 0 and any(
            word and word in q
            for word in (metric.definition.lower(), metric.category.lower())
        ):
            score += 10
        if any(t.lower() in q for t in metric.related_tables):
            score += 5

        # Explicit 门诊/住院 wording is stronger than a generic shared alias:
        # "住院总收入" must not resolve to the outpatient revenue metric.
        metric_scope = (
            "住院"
            if "住院" in metric.category
            else ("门急诊" if "门急诊" in metric.category else "")
        )
        if scope and metric_scope:
            score += 30 if metric_scope in scope else -60

        # A small fallback for common generic wording.
        if (
            score <= 0
            and "次均费用" in q
            and ("次均费用" in metric.name or "均次费用" in metric.name)
        ):
            score = 60
        if score <= 0 and "药占比" in q and "药占比" in metric.name:
            score = 60
        if score <= 0 and "医保" in q and "医保" in metric.name:
            score = 20

        return {"score": score, "metric": metric, "span": span}

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
