def build_ambiguity_prompt(question: str, matched_codes: list[str]) -> str:
    return "\n".join([
        "下面是一个医疗数据问题。规则已经命中了这些歧义：",
        f"问题：{question}",
        f"已命中 code：{matched_codes}",
        "",
        "请只补充规则没有覆盖的语义歧义，不要重复已命中的 code。",
        "每条必须给 code、term、question、options、default。",
        "blocking 一律输出 false；是否 blocking 由规则层决定。",
        '只输出 JSON：{"items": [...]}。',
        "没有新歧义就输出 {\"items\": []}。",
    ])
