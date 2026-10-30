def build_clarify_prompt(payload: dict) -> str:
    options = payload.get("options", [])
    return "\n".join([
        "把下面的澄清问题改写成医院工作人员能直接看懂的中文。",
        "只能改 question 和 option 的 label。",
        "不能改 option 的 id 和 value，不能新增或删除 option，不能改 default_option_id。",
        "不要出现内部字段名、metric_id、SQL 术语。",
        "",
        f"原始 code：{payload.get('code')}",
        f"命中词：{payload.get('matched_term', '')}",
        f"原始问题：{payload.get('question')}",
        f"选项：{options}",
        f"默认：{payload.get('default_option_id')}",
        "",
        '只输出 JSON：{"question": "...", "options": [{"id": "...", "label": "..."}]}。',
    ])
