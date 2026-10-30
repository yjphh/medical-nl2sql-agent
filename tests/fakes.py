"""Deterministic fakes so graph tests never call the network.

The graph only depends on the small ``LLMClient`` protocol in
``app.graph.llm``; these fakes implement the same four methods and record
what was asked.
"""

from __future__ import annotations

from app.graph.llm import SQLResult


class FakeLLMClient:
    def __init__(
        self,
        sql: str | None = "SELECT COUNT(*) AS value FROM visit",
        metric_id: str | None = None,
        polish: dict | None = None,
        supplements: list[dict] | None = None,
        assumptions: list[str] | None = None,
        confidence: float | None = 0.9,
    ) -> None:
        self.sql = sql
        self.metric_id = metric_id
        self.polish = polish
        self.supplements = supplements or []
        self.assumptions = assumptions or []
        self.confidence = confidence
        self.calls: list[str] = []
        self.sql_requests: list[dict] = []

    def choose_metric(self, question, candidates, context):
        self.calls.append("choose_metric")
        if self.metric_id:
            return self.metric_id
        return candidates[0].id if candidates else None

    def polish_clarification(self, payload):
        self.calls.append("polish_clarification")
        if self.polish is None:
            return None
        options = payload.get("options", [])
        labels = {o["id"]: o["label"] for o in options}
        polished = []
        for opt in self.polish.get("options", []):
            oid = opt.get("id")
            if oid not in labels:
                continue
            polished.append({"id": oid, "label": opt.get("label") or labels[oid]})
        if len(polished) != len(options):
            return None
        return {
            "question": self.polish.get("question") or payload.get("question", ""),
            "options": polished,
        }

    def supplement_ambiguities(self, question, matched_codes):
        self.calls.append("supplement_ambiguities")
        return [item for item in self.supplements if item.get("code") not in matched_codes]

    def generate_sql(self, question, context, metrics, examples):
        self.calls.append("generate_sql")
        self.sql_requests.append({
            "question": question,
            "metrics": [m.id for m in metrics],
            "examples": [e.id for e in examples],
        })
        if self.sql is None:
            return SQLResult(error="fake_sql_failure")
        return SQLResult(
            sql=self.sql,
            assumptions=list(self.assumptions),
            confidence=self.confidence,
        )


class FakeKnowledgeProvider:
    """Minimal provider for tests that should not touch ``data/``."""

    def __init__(self, metrics=None, ambiguities=None) -> None:
        self.metrics = metrics or []
        self.ambiguities = ambiguities or []

    def search_metrics(self, question, top_k=5):
        return list(self.metrics)

    def resolve_terms(self, question):
        return []

    def detect_ambiguities(self, question, terms=None, asked_codes=None):
        from app.knowledge.models import AmbiguityReport

        asked = set(asked_codes or [])
        items = [item for item in self.ambiguities if item.get("code") not in asked]
        return AmbiguityReport(items=items, has_blocking=any(i.get("blocking") for i in items))

    def search_sql_examples(self, question, top_k=3, metrics=None, terms=None):
        return []

    def get_schema_context(self, question, intent=None, max_chars=8000):
        from app.knowledge.models import SchemaContext

        return SchemaContext(
            tables=[],
            columns={},
            join_paths=[],
            prompt_text="schema",
            char_count=6,
        )
