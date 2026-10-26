from app.graph.state import GraphState


def run(state: GraphState) -> GraphState:
    """识别用户意图并拉取相关 schema 上下文。"""
    # TODO: LLM 意图分类 + schema_catalog 检索
    return {
        **state,
        "intent": {},
        "schema_context": {},
    }
