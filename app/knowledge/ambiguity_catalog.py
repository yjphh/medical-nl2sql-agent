"""Match ambiguity rules, render questions, and sort by priority."""

from __future__ import annotations

from app.knowledge.models import AmbiguityReport, AmbiguityRule


class AmbiguityCatalog:
    def __init__(self, rules: list[AmbiguityRule]) -> None:
        self.rules = rules

    @staticmethod
    def _trigger(question: str, rule: AmbiguityRule) -> str | None:
        hits = [t for t in rule.trigger_terms if t and t in question]
        return max(hits, key=len) if hits else None

    def detect(
        self,
        question: str,
        terms=None,
        asked_codes: list[str] | None = None,
    ) -> AmbiguityReport:
        question = question or ""
        asked = set(asked_codes or [])
        items: list[dict] = []
        for rule in self.rules:
            if rule.code in asked:
                continue
            trigger = self._trigger(question, rule)
            if not trigger:
                continue
            rendered = rule.question.replace("{term}", trigger)
            items.append({
                "code": rule.code,
                "category": rule.category,
                "matched_term": trigger,
                "question": rendered,
                "options": rule.options,
                "default": rule.default,
                "blocking": rule.blocking,
                "priority": rule.priority,
                "related_columns": rule.related_columns,
                "related_metrics": rule.related_metrics,
            })
        items.sort(key=lambda item: (item["priority"], item["code"]))
        return AmbiguityReport(
            items=items,
            has_blocking=any(item["blocking"] for item in items),
        )

    @staticmethod
    def to_prompt_context(report: AmbiguityReport) -> str:
        if not report.items:
            return ""
        lines = ["## 待澄清问题"]
        for item in report.items:
            blocking = "blocking" if item["blocking"] else "optional"
            lines.append(
                f"- [{item['code']}] {item['question']} "
                f"选项: {item['options']}；默认: {item['default']}；{blocking}"
            )
        return "\n".join(lines)
