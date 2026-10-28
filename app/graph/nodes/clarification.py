from app.graph.state import GraphState


def run(state: GraphState) -> GraphState:
    """生成澄清问题，等待用户回答后返回（工作流在此暂停一轮）。"""
    # TODO: 根据 ambiguity.items 生成澄清文案
    return {
        **state,
        "status": "needs_clarification",
        "clarification": None,
    }
