import pandas as pd
import json
import uuid


def convert_excel_to_json():
    file_path = '大屏指标校验脚本(2).xlsx'
    # 读取 Excel，跳过空行
    df = pd.read_excel(file_path)

    metrics = []
    gold_sqls = []

    # 记录上一个分类（因为 Excel 里分类是合并单元格，会有 NaN）
    current_category = ""

    for index, row in df.iterrows():
        # 处理分类
        if pd.notna(row['分类']):
            current_category = str(row['分类']).strip()

        # 处理指标名称
        if pd.isna(row['指标名称']):
            continue

        metric_name = str(row['指标名称']).strip()
        sql_text = str(row['校验sql']).strip() if pd.notna(row['校验sql']) else ""

        # 生成唯一 ID
        metric_id = f"m_{uuid.uuid4().hex[:6]}"

        # 1. 组装 metrics.json 的数据
        metric_item = {
            "id": metric_id,
            "name": metric_name,
            "category": current_category,
            "aliases": [],  # 留空，后续你可以自己填同义词
            "definition": f"统计{current_category}中的{metric_name}",
            "formula": sql_text if not sql_text.startswith("select") else "",  # 如果是纯公式（如比例），放这里
            "gold_sql_ref": f"gold_{metric_id}" if sql_text.startswith("select") else "",
            "related_tables": [],  # 留空，后续可以从 SQL 里提取
            "time_field": "treatment_occurrence_time",  # 默认时间字段，后续可改
            "default_interpretation": "按自然月统计",
            "ambiguity_notes": [],
            "unit": "人次" if "人次" in metric_name else ""
        }
        metrics.append(metric_item)

        # 2. 组装 gold_sql.json 的数据（如果有 SQL）
        if sql_text.startswith("select"):
            gold_item = {
                "id": f"gold_{metric_id}",
                "metric_id": metric_id,
                "metric_name": metric_name,
                "kind": "sql",
                "sql": sql_text,
                "tables": [],
                "notes": "原始 Oracle SQL"
            }
            gold_sqls.append(gold_item)

    # 写入 metrics.json
    with open('metrics.json', 'w', encoding='utf-8') as f:
        json.dump({"version": "1.0.0", "metrics": metrics}, f, ensure_ascii=False, indent=2)

    # 写入 gold_sql.json
    with open('gold_sql.json', 'w', encoding='utf-8') as f:
        json.dump({"version": "1.0.0", "dialect": "oracle", "items": gold_sqls}, f, ensure_ascii=False, indent=2)

    print(f"转换成功！共生成 {len(metrics)} 个指标，{len(gold_sqls)} 条 SQL。")


if __name__ == "__main__":
    convert_excel_to_json()