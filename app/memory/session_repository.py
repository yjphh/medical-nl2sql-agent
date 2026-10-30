from dataclasses import dataclass, field


@dataclass
class QueryRecord:
    query_id: str
    session_id: str
    question: str
    status: str = "pending"
    sql: str | None = None


@dataclass
class Session:
    session_id: str
    user_id: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)
    query_ids: list[str] = field(default_factory=list)


class SessionRepository:
    """In-memory sessions and queries.

    The LangGraph checkpoint is keyed by ``query_id`` (one thread per query),
    so this repository only keeps the session -> query index and the answer
    history; it never stores graph state.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._queries: dict[str, QueryRecord] = {}

    def create(self, session_id: str, user_id: str | None = None) -> Session:
        session = Session(session_id=session_id, user_id=user_id)
        self._sessions[session_id] = session
        return session

    def get_or_create(self, session_id: str, user_id: str | None = None) -> Session:
        return self.get(session_id) or self.create(session_id, user_id)

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def create_query(self, query_id: str, session_id: str, question: str) -> QueryRecord:
        self.get_or_create(session_id)
        record = QueryRecord(query_id=query_id, session_id=session_id, question=question)
        self._queries[query_id] = record
        self._sessions[session_id].query_ids.append(query_id)
        return record

    def get_query(self, query_id: str) -> QueryRecord | None:
        return self._queries.get(query_id)

    def update_query(self, query_id: str, status: str, sql: str | None = None) -> None:
        record = self._queries.get(query_id)
        if record is None:
            return
        record.status = status
        if sql is not None:
            record.sql = sql

    def append_turn(self, session_id: str, role: str, content: str) -> None:
        session = self.get_or_create(session_id)
        session.history.append({"role": role, "content": content})


repository = SessionRepository()
