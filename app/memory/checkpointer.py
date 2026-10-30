from langgraph.checkpoint.memory import MemorySaver

_CHECKPOINTER: MemorySaver | None = None


def get_checkpointer() -> MemorySaver:
    global _CHECKPOINTER
    if _CHECKPOINTER is None:
        _CHECKPOINTER = MemorySaver()
    return _CHECKPOINTER


class GraphCheckpointer:
    """Backward-compatible wrapper used by the old scaffolding."""

    def __init__(self, backend: str = "memory") -> None:
        self.backend = backend

    def get_checkpointer(self) -> MemorySaver:
        return get_checkpointer()
