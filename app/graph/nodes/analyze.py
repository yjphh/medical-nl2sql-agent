import re

from app.graph.state import GraphState


def run(state: GraphState, deps) -> dict:
    question = state["original_question"]
    terms = deps.knowledge.resolve_terms(question)
    metrics = deps.knowledge.search_metrics(question)
    context = deps.knowledge.get_schema_context(question)
    report = deps.knowledge.detect_ambiguities(question, terms)

    selected_metric_id = metrics[0].id if metrics else None
    if len(metrics) > 1:
        chosen = deps.llm.choose_metric(question, metrics, context.prompt_text)
        if chosen:
            selected_metric_id = chosen

    resolved_codes: list[str] = []
    if selected_metric_id:
        resolved_codes.append("metric_scope")
    # An explicit year/month/day resolves the relative-time question.
    if re.search(r"20\d{2}\s*[-年/]\s*\d{1,2}", question):
        resolved_codes.append("time_range")

    selected = next(
        (m for m in metrics if m.id == selected_metric_id),
        None,
    )
    return {
        "terms": [t.to_dict() for t in terms],
        "metrics": [m.to_dict() for m in metrics],
        "selected_metric_id": selected_metric_id,
        "schema_context": context.to_dict(),
        "ambiguities": report.items,
        "resolved_codes": resolved_codes,
        "intent": {
            "metric_id": selected_metric_id,
            "metric_name": selected.name if selected else None,
            "tables": selected.related_tables if selected else context.tables,
        },
        "trace": [{
            "node": "analyze_question",
            "metrics": [m.id for m in metrics],
            "selected_metric_id": selected_metric_id,
            "resolved_codes": resolved_codes,
        }],
    }
