"""Resolve user terms to schema columns, enums, metrics, or time ranges."""

from __future__ import annotations

import re

from app.knowledge.models import TermInfo, TermResolution


class Glossary:
    def __init__(self, terms: list[TermInfo]) -> None:
        self.terms = terms

    @staticmethod
    def _matched_text(question: str, term: TermInfo) -> str | None:
        candidates = [term.term, *term.aliases]
        matches = []
        for candidate in candidates:
            if not candidate:
                continue
            if candidate in question:
                if len(candidate) == 1 and candidate not in {"男", "女"}:
                    continue
                matches.append(candidate)
        return max(matches, key=len) if matches else None

    def resolve(self, question: str) -> list[TermResolution]:
        question = question or ""
        results: list[TermResolution] = []
        seen: set[tuple[str, str]] = set()
        for term in self.terms:
            matched = self._matched_text(question, term)
            if not matched:
                continue
            target = dict(term.target or {})
            key = (term.target_type, str(sorted(target.items())))
            if key in seen:
                continue
            seen.add(key)
            results.append(
                TermResolution(
                    term=matched,
                    canonical_term=term.term,
                    target_type=term.target_type,
                    target=target,
                    confidence=term.confidence,
                    source=term.source,
                    match=term.match or term.target_type,
                    values=term.values or list(target.get("values", [])),
                )
            )

        # Department names are not in the schema dictionary. Mapping a token
        # ending with "科" to visit.dept_name is a pattern, not a fabricated
        # dictionary value; mark it low confidence.
        has_department = any(
            r.target.get("table") == "visit"
            and r.target.get("column") == "dept_name"
            for r in results
        )
        if not has_department:
            match = re.search(r"([一-龥]{2,10}科)", question)
            if match:
                token = match.group(1)
                token = re.sub(
                    r"^(上个月|本月|今年|去年|最近|近|这|那|在|去|到|的|查|看|患者|门诊|住院|急诊)+",
                    "",
                    token,
                )
                if len(token) < 2:
                    token = match.group(1)
                results.append(
                    TermResolution(
                        term=token,
                        canonical_term=token,
                        target_type="column",
                        target={
                            "table": "visit",
                            "column": "dept_name",
                            "value": token,
                        },
                        confidence="low",
                        source="heuristic.department",
                        match="contains",
                    )
                )

        order = {"high": 0, "medium": 1, "low": 2}
        results.sort(
            key=lambda r: (-len(r.term), order.get(r.confidence, 3), r.canonical_term)
        )
        return results

    def to_prompt_context(self, terms: list[TermResolution]) -> str:
        if not terms:
            return ""
        lines = ["## 术语解析"]
        for term in terms:
            target = term.target
            if term.target_type == "metric":
                rendered = f"指标 {target.get('metric_id')}"
            elif term.target_type == "table":
                rendered = f"表 {target.get('table')}"
            elif term.target_type == "time_range":
                rendered = f"时间字段 {target.get('table')}.{target.get('column')}"
            else:
                rendered = f"{target.get('table')}.{target.get('column')}"
                if target.get("value"):
                    rendered += f" = {target['value']}"
                elif target.get("values"):
                    rendered += f" IN {target['values']}"
            lines.append(
                f"- {term.term} -> {rendered} "
                f"({term.match or term.target_type}, {term.confidence}, {term.source})"
            )
        return "\n".join(lines)
