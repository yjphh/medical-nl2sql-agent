from app.graph.state import GraphState


def run(state: GraphState, deps) -> dict:
    question = state.get("rewritten_question") or state["original_question"]
    context = deps.knowledge.get_schema_context(question)
    metrics = deps.knowledge.search_metrics(question)
    examples = deps.knowledge.search_sql_examples(
        question, metrics=metrics, terms=deps.knowledge.resolve_terms(question)
    )
    result = deps.llm.generate_sql(
        question=question,
        context=context.prompt_text,
        metrics=metrics,
        examples=examples,
    )
    if not result.sql:
        return {
            "sql": None,
            "sql_source": "deepseek",
            "status": "error",
            "error": {
                "code": "sql_generation_failed",
                "message": result.error or "empty_sql",
            },
            "trace": [{
                "node": "generate_sql",
                "ok": False,
                "error": result.error,
            }],
        }
    return {
        "sql": result.sql,
        "sql_source": "deepseek",
        "sql_assumptions": result.assumptions,
        "sql_confidence": result.confidence,
        "status": "ok",
        "trace": [{
            "node": "generate_sql",
            "ok": True,
            "confidence": result.confidence,
        }],
    }
