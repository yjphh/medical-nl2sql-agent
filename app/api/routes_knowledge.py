"""Read-only knowledge endpoints used by the frontend."""

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.runtime import get_deps

router = APIRouter(prefix="/metrics", tags=["knowledge"])


class MetricOut(BaseModel):
    id: str
    name: str
    category: str = ""
    aliases: list[str] = Field(default_factory=list)
    definition: str = ""
    unit: str = ""


class MetricListOut(BaseModel):
    items: list[MetricOut] = Field(default_factory=list)
    total: int = 0


def _matches(metric, needle: str) -> bool:
    haystack = [metric.id, metric.name, metric.category, metric.definition]
    haystack.extend(metric.aliases)
    return any(needle in (value or "").lower() for value in haystack)


@router.get("", response_model=MetricListOut)
async def list_metrics(
    q: str | None = Query(default=None, max_length=100),
    top_k: int = Query(default=50, ge=1, le=200),
) -> MetricListOut:
    metrics = get_deps().knowledge.bundle.metrics
    if q and q.strip():
        needle = q.strip().lower()
        metrics = [metric for metric in metrics if _matches(metric, needle)]
    items = [
        MetricOut(
            id=metric.id,
            name=metric.name,
            category=metric.category,
            aliases=list(metric.aliases),
            definition=metric.definition,
            unit=metric.unit,
        )
        for metric in metrics[:top_k]
    ]
    return MetricListOut(items=items, total=len(metrics))
