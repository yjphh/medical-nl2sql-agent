"""Deterministic fakes so graph tests never call the network.

The graph only depends on the small ``LLMClient`` protocol in
``app.graph.llm``; these fakes implement the same four methods and record
what was asked.
"""

from __future__ import annotations

from app.graph.llm import SQLResult
from app.tools.sql_agent.executor import ExecutionResult


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


class FakeSQLAgentLLM:
    """Queue-driven fake for ``app.tools.sql_agent.core``.

    Each entry in ``batches`` is returned by one ``generate_candidates`` call;
    ``repairs`` is consumed one entry per ``repair`` call.  No network.
    """

    def __init__(
        self,
        batches: list[list[dict]],
        repairs: list[dict] | None = None,
        *,
        default_batch: list[dict] | None = None,
    ) -> None:
        self.batches = [list(batch) for batch in batches]
        self.repairs = list(repairs or [])
        self.default_batch = None if default_batch is None else list(default_batch)
        self.generate_calls: list[dict] = []
        self.repair_calls: list[dict] = []

    def generate_candidates(
        self, question, schema_context, *, examples=None, n=1, schema_links=None, repair=None
    ):
        self.generate_calls.append({
            "question": question,
            "n": n,
            "repair": repair,
            "schema_links": schema_links,
        })
        if self.batches:
            return self.batches.pop(0)
        if self.default_batch is not None:
            return list(self.default_batch)
        return []

    def repair(self, question, sql, error, schema_context, *, examples=None):
        self.repair_calls.append({"sql": sql, "error": error})
        return self.repairs.pop(0) if self.repairs else {"sql": sql}


class FakeExecutor:
    """Map SQL to rows or to an error, recording every execution."""

    def __init__(
        self,
        rows_by_sql: dict[str, list[tuple]] | None = None,
        errors_by_sql: dict[str, str] | None = None,
    ) -> None:
        self.rows_by_sql = dict(rows_by_sql or {})
        self.errors_by_sql = dict(errors_by_sql or {})
        self.calls: list[str] = []

    def __call__(self, sql: str) -> ExecutionResult:
        self.calls.append(sql)
        if sql in self.errors_by_sql:
            return ExecutionResult(ok=False, error=self.errors_by_sql[sql])
        rows = self.rows_by_sql.get(sql, [("1",)])
        return ExecutionResult(
            ok=True,
            columns=["value"],
            rows=rows,
            row_count=len(rows),
        )
