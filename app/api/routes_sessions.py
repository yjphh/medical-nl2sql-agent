import uuid

from fastapi import APIRouter, HTTPException

from app.api.schemas import SessionCreate, SessionOut
from app.memory.session_repository import repository

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionOut)
async def create_session(body: SessionCreate) -> SessionOut:
    session = repository.create(str(uuid.uuid4()), body.user_id)
    return SessionOut(session_id=session.session_id, user_id=session.user_id)


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: str) -> SessionOut:
    session = repository.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session_id")
    return SessionOut(session_id=session.session_id, user_id=session.user_id)
