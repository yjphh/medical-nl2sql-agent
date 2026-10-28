from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    user_id: str | None = None


class SessionOut(BaseModel):
    session_id: str
    user_id: str | None = None


@router.post("", response_model=SessionOut)
async def create_session(body: SessionCreate) -> SessionOut:
    # TODO: persist session
    return SessionOut(session_id="placeholder", user_id=body.user_id)


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: str) -> SessionOut:
    return SessionOut(session_id=session_id)
