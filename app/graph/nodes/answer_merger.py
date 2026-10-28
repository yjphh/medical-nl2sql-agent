from app.graph.state import GraphState


def run(state: GraphState) -> GraphState:
    """将查询结果合并为面向用户的自然语言答案。"""
    # TODO: LLM 归纳 + 结构化表格/指标展示
    return {
        **state,
        "status": "ok",
        "answer": None,
    }
