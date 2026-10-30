from fastapi import FastAPI

from app.api import routes_knowledge, routes_queries, routes_sessions
from app.config import settings

app = FastAPI(title="Medical NL2SQL Agent", version="0.1.0")

app.include_router(routes_sessions.router)
app.include_router(routes_queries.router)
app.include_router(routes_knowledge.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    run()
