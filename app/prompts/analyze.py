def build_analyze_prompt(question: str, candidates: list[dict], context: str) -> str:
    lines = [
        "下面是一个医疗数据问题，以及知识层给出的候选指标。",
        f"问题：{question}",
        "",
        "候选指标：",
    ]
    for item in candidates:
        lines.append(
            f"- id={item['id']}；名称={item['name']}；"
            f"别名={item.get('aliases', [])}；定义={item.get('definition', '')}"
        )
    lines += [
        "",
        "相关 schema：",
        context[:4000],
        "",
        "请选择最符合问题的一个指标。",
        '只输出 JSON：{"metric_id": "..."}。',
        "如果无法判断，metric_id 输出空字符串。",
    ]
    return "\n".join(lines)
