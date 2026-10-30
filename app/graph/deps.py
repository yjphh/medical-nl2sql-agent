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
    )
    return GraphDeps(knowledge=knowledge, llm=llm, settings=app_settings)
