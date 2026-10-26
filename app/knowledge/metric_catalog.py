import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MetricInfo:
    name: str
    definition: str
    formula: str = ""
    related_tables: list[str] = field(default_factory=list)


@dataclass
class MetricCatalog:
    metrics: list[MetricInfo] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "MetricCatalog":
        path = Path(path)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        metrics = [MetricInfo(**m) for m in raw.get("metrics", [])]
        return cls(metrics=metrics)

    def search(self, keywords: list[str]) -> list[MetricInfo]:
        results: list[MetricInfo] = []
        for metric in self.metrics:
            text = f"{metric.name} {metric.definition}".lower()
            if any(k.lower() in text for k in keywords):
                results.append(metric)
        return results
