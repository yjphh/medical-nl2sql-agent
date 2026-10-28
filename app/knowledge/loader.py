from pathlib import Path

from app.knowledge.metric_catalog import MetricCatalog
from app.knowledge.schema_catalog import SchemaCatalog


def load_knowledge(data_dir: str | Path = "data") -> tuple[SchemaCatalog, MetricCatalog]:
    data_dir = Path(data_dir)
    schema_catalog = SchemaCatalog.load(data_dir / "schema.json")
    metric_catalog = MetricCatalog.load(data_dir / "metrics.json")
    return schema_catalog, metric_catalog
