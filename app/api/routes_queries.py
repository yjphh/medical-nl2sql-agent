from fastapi import APIRouter

from app.api.schemas import QueryRequest, QueryResponse

router = APIRouter(prefix="/queries", tags=["queries"])


@router.post("", response_model=QueryResponse)
async def run_query(body: QueryRequest) -> QueryResponse:
    # TODO: invoke LangGraph workflow
    return QueryResponse(
        session_id=body.session_id,
        status="not_implemented",
        answer=None,
        sql=None,
        clarification=None,
    )
