class GraphCheckpointer:
    """LangGraph checkpointer 封装（预留持久化实现）。"""

    def __init__(self, backend: str = "memory") -> None:
        self.backend = backend

    def get_checkpointer(self):
        # TODO: 接入 langgraph checkpoint (sqlite/postgres)
        return None
