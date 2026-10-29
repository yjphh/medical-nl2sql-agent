"""Single entry point for the knowledge layer used by the graph/API layer."""

from __future__ import annotations

from pathlib import Path

from app.knowledge.ambiguity_catalog import AmbiguityCatalog
from app.knowledge.context_builder import build_context
from app.knowledge.example_catalog import ExampleCatalog
from app.knowledge.glossary import Glossary
from app.knowledge.loader import load_knowledge
from app.knowledge.metric_catalog import MetricCatalog
from app.knowledge.models import (
    AmbiguityReport,
    KnowledgeBundle,
    MetricInfo,
    SchemaContext,
    SQLExample,
    TermResolution,
)
from app.knowledge.schema_catalog import SchemaCatalog


class FileKnowledgeProvider:
    def __init__(self, data_or_bundle: str | Path | KnowledgeBundle = "data") -> None:
        if isinstance(data_or_bundle, KnowledgeBundle):
            self.bundle = data_or_bundle
        else:
            self.bundle = load_knowledge(data_or_bundle)
        self.schema_catalog = SchemaCatalog(self.bundle.tables, self.bundle.dialect)
        self.metric_catalog = MetricCatalog(self.bundle.metrics)
        self.glossary = Glossary(self.bundle.terms)
        self.ambiguity_catalog = AmbiguityCatalog(self.bundle.ambiguities)
        self.example_catalog = ExampleCatalog(self.bundle.examples)

    def search_metrics(self, question: str, top_k: int = 5) -> list[MetricInfo]:
        return self.metric_catalog.search(question, top_k=top_k)

    def resolve_terms(self, question: str) -> list[TermResolution]:
        return self.glossary.resolve(question)

    def detect_ambiguities(
        self,
        question: str,
        terms: list[TermResolution] | None = None,
        asked_codes: list[str] | None = None,
    ) -> AmbiguityReport:
        return self.ambiguity_catalog.detect(
            question, terms=terms, asked_codes=asked_codes
        )

    def search_sql_examples(
        self,
        question: str,
        top_k: int = 3,
        metrics: list[MetricInfo] | None = None,
        terms: list[TermResolution] | None = None,
    ) -> list[SQLExample]:
        return self.example_catalog.search(
            question,
            metrics=metrics,
            terms=terms,
            top_k=top_k,
        )

    def get_schema_context(
        self,
        question: str,
        intent: dict | None = None,
        max_chars: int = 8000,
    ) -> SchemaContext:
        terms = self.resolve_terms(question)
        metrics = self.search_metrics(question)
        examples = self.search_sql_examples(question, metrics=metrics, terms=terms)
        return build_context(
            question=question,
            terms=terms,
            metrics=metrics,
            examples=examples,
            schema_catalog=self.schema_catalog,
            metric_catalog=self.metric_catalog,
            glossary=self.glossary,
            example_catalog=self.example_catalog,
            max_chars=max_chars,
        )
