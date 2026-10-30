from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.graph.llm import DeepSeekLLMClient, LLMClient
from app.knowledge.provider import FileKnowledgeProvider


@dataclass
class GraphDeps:
    knowledge: FileKnowledgeProvider
    llm: LLMClient
    settings: Any
    # Injected by tests (and optionally by deployments) to keep the SQL
    # generation subgraph offline; ``None`` builds the real client/executor.
    sql_llm: Any | None = None
    sql_executor: Any | None = None


def build_deps(
    knowledge: FileKnowledgeProvider | None = None,
    llm: LLMClient | None = None,
    app_settings: Any | None = None,
) -> GraphDeps:
    app_settings = app_settings or settings
    knowledge = knowledge or FileKnowledgeProvider("data")
    llm = llm or DeepSeekLLMClient(
        model=app_settings.llm_model,
        api_key=app_settings.llm_api_key,
        base_url=app_settings.llm_base_url,
        timeout=getattr(app_settings, "llm_timeout_seconds", 30.0),
        max_retries=getattr(app_settings, "llm_max_retries", 1),
    )
    return GraphDeps(knowledge=knowledge, llm=llm, settings=app_settings)
