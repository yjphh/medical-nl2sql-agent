from dataclasses import dataclass, field


@dataclass
class Session:
    session_id: str
    user_id: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)


class SessionRepository:
    """会话与多轮澄清历史存储。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, session_id: str, user_id: str | None = None) -> Session:
        session = Session(session_id=session_id, user_id=user_id)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def append_turn(self, session_id: str, role: str, content: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            session = self.create(session_id)
        session.history.append({"role": role, "content": content})
