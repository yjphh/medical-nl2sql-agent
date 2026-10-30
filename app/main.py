import logging
import sqlite3
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI

from app.api import routes_knowledge, routes_queries, routes_sessions
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BUILD_DB_COMMAND = ".venv\\Scripts\\python.exe -m app.tools.sql_agent.build_db"


@lru_cache(maxsize=1)
def _check_knowledge() -> dict:
    try:
        from app.knowledge.loader import load_knowledge
    except ImportError as exc:  # pragma: no cover - broken install
        return {"ok": False, "detail": f"import failed: {exc}"}
    try:
        bundle = load_knowledge("data")
    except Exception as exc:  # noqa: BLE001 - the probe must never raise
        return {
            "ok": False,
            "detail": f"{type(exc).__name__}: {exc}",
            "fix": "检查 data/*.json（见 docs/MEMBER_A_KNOWLEDGE.md）",
        }
    return {
        "ok": True,
        "tables": len(bundle.tables),
        "metrics": len(bundle.metrics),
    }


def _check_database() -> dict:
    """The mirror is gitignored, so a fresh clone has no database at all."""
    path = Path(settings.sqlite_db_path)
    if not path.exists():
        return {
            "ok": False,
            "path": str(path),
            "detail": "database missing",
            "fix": BUILD_DB_COMMAND,
        }
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = connection.execute("SELECT COUNT(1) FROM visit").fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return {"ok": False, "path": str(path), "detail": str(exc), "fix": BUILD_DB_COMMAND}
    if rows == 0:
        return {"ok": False, "path": str(path), "detail": "visit table is empty", "fix": BUILD_DB_COMMAND}
    return {"ok": True, "path": str(path), "visits": rows}


def health_report() -> dict:
    database = _check_database()
    knowledge = _check_knowledge()
    problems = [name for name, check in (("database", database), ("knowledge", knowledge)) if not check["ok"]]
    report = {
        "status": "ok" if not problems else "degraded",
        "version": "0.1.0",
        "checks": {"database": database, "knowledge": knowledge},
    }
    if problems:
        fixes = sorted({c["fix"] for c in (database, knowledge) if c.get("fix")})
        report["message"] = f"后端未就绪：{', '.join(problems)} 不可用。"
        if fixes:
            report["message"] += " 修复：" + "；".join(fixes)
    return report


def prune_expired() -> int:
    """Drop sessions/queries past the TTL, and their graph checkpoints.

    A query row without its checkpoint thread (or the reverse) is dead weight
    at best and a 409 at worst, so both sides are removed together.
    """
    from app.memory.checkpointer import drop_thread
    from app.memory.session_repository import repository

    expired = repository.prune()
    for query_id in expired:
        drop_thread(query_id)
    if expired:
        logger.info("pruned %d expired queries", len(expired))
    return len(expired)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    report = health_report()
    if report["status"] != "ok":
        logger.error("startup check failed: %s", report.get("message"))
        logger.error("run: %s", BUILD_DB_COMMAND)
    else:
        logger.info("startup check passed (database + knowledge)")
    try:
        prune_expired()
    except Exception:
        logger.warning("session cleanup failed", exc_info=True)
    yield


app = FastAPI(title="Medical NL2SQL Agent", version="0.1.0", lifespan=lifespan)

app.include_router(routes_sessions.router)
app.include_router(routes_queries.router)
app.include_router(routes_knowledge.router)


@app.get("/health")
def health() -> dict:
    """Probe the SQLite mirror and the knowledge bundle, not just the process."""
    return health_report()


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    run()
