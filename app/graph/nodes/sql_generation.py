from app.graph.state import GraphState


def run(state: GraphState) -> GraphState:
    """基于意图与 schema 上下文生成 SQL。"""
    # TODO: 调用 app.tools.sql_generator
    return {
        **state,
        "sql": None,
    }
