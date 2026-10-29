#!/usr/bin/env python
"""Build data/*.json for the medical NL2SQL knowledge layer.

Stages:
  python scripts/build_knowledge_data.py schema
  python scripts/build_knowledge_data.py gold
  python scripts/build_knowledge_data.py metrics
  python scripts/build_knowledge_data.py glossary
  python scripts/build_knowledge_data.py ambiguities
  python scripts/build_knowledge_data.py examples
  python scripts/build_knowledge_data.py all
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DEFAULT_DOCX = Path(
    r"C:\Users\w\Desktop\大三上\SRP\SRP开发\补充.docx"
)
DEFAULT_XLSX = Path(
    r"C:\Users\w\Desktop\大三上\SRP\SRP开发\医疗系统数据集\大屏指标校验脚本(2).xlsx"
)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

NEW_TABLES = {
    "examination_exam": {
        "description": "检查记录-检查项目，一行一个检查项目",
        "grain": "一行一个检查项目",
        "primary_key": ["docid"],
    },
    "prescription_record": {
        "description": "处方记录，一行一张处方",
        "grain": "一行一张处方",
        "primary_key": ["dcid"],
    },
    "diagnose_record": {
        "description": "诊断记录，一行一个诊断",
        "grain": "一行一个诊断",
        "primary_key": ["dcid"],
    },
}

PII_NAMES = {
    "name",
    "id_card",
    "birth_date",
    "patient_phone",
    "health_card_id",
    "medical_card_id",
    "source_patient_id",
    "address",
    "phone",
    "contact",
    "work_unit_name",
}

TIME_ROLES = {
    "treatment_occurrence_time": "event_time",
    "visit_date_time": "visit_time",
    "admission_date_time": "admit_time",
    "discharge_date_time": "discharge_time",
    "settle_date_time": "settle_time",
    "cost_date": "cost_time",
    "prescription_date": "prescription_time",
    "diag_n_osis_date": "diagnosis_time",
    "diagnose_time": "diagnosis_time",
    "operation_start_date_time": "operation_time",
    "operation_date_time": "operation_time",
    "execute_date": "exam_time",
    "inspection_date": "inspection_time",
    "request_date_time": "request_time",
    "report_date": "report_time",
    "sample_execute_time": "sample_time",
    "sample_receive_time": "sample_time",
    "confirm_diagnose_date_time": "diagnosis_time",
    "operation_time": "operation_time",
    "handle_time": "handle_time",
    "birth_date": "birth_date",
}

ENUMS = {
    ("visit", "activity_type_code"): [
        {"value": "1", "label": "门诊"},
        {"value": "2", "label": "急诊"},
        {"value": "5", "label": "住院"},
        {"value": "6", "label": "住院（其他）"},
        {"value": "9", "label": "住院（其他）"},
    ],
    ("patient", "sex_code"): [
        {"value": "1", "label": "男"},
        {"value": "2", "label": "女"},
    ],
    ("medical_expense_rec", "medical_cost_pay_type_code"): [
        {"value": "01", "label": "医保类型01"},
        {"value": "02", "label": "医保类型02"},
        {"value": "03", "label": "医保类型03"},
        {"value": "09", "label": "医保类型09"},
    ],
    ("operation_operation", "operation_level_code"): [
        {"value": "1", "label": "一级手术"},
        {"value": "2", "label": "二级手术"},
        {"value": "3", "label": "三级手术"},
        {"value": "4", "label": "四级手术"},
    ],
    ("operation_record", "operation_period_type_code"): [
        {"value": "1", "label": "择期"},
    ],
    ("visit", "modify_flag"): [
        {"value": "1", "label": "新增"},
        {"value": "2", "label": "修改"},
        {"value": "3", "label": "删除"},
    ],
    ("examination_exam", "qualitativeresults_code"): [
        {"value": "1", "label": "正常"},
        {"value": "2", "label": "异常"},
        {"value": "3", "label": "不确定"},
    ],
    ("examination_exam", "source_patient_id_type"): [
        {"value": "OV", "label": "门诊档案"},
        {"value": "IV", "label": "住院档案"},
        {"value": "HV", "label": "体检档案"},
        {"value": "LG", "label": "留观档案"},
        {"value": "TV", "label": "其他档案"},
    ],
}

RELATIONSHIPS = [
    # from_table, from_col, to_table, to_col, type, note, source, confidence, fanout, filter
    (
        "visit", "source_patient_id", "patient", "source_patient_id",
        "many_to_one", "患者维度；一个患者可以有多条就诊记录", "excel_join", "high", "none", None,
    ),
    (
        "visit", "visit_id", "medical_expense_rec", "visit_id",
        "one_to_many", "费用明细；join 后行数会放大，聚合前先按 visit_id 汇总",
        "excel_join", "high", "to_many", None,
    ),
    (
        "visit", "visit_id", "examination_record", "visit_id",
        "one_to_many", "检查记录；join 后行数会放大", "excel_join", "high", "to_many", None,
    ),
    (
        "visit", "visit_id", "inspection_record", "visit_id",
        "one_to_many", "检验记录；join 后行数会放大", "excel_join", "high", "to_many", None,
    ),
    (
        "visit", "visit_id", "operation_record", "visit_id",
        "one_to_many", "手术记录；join 后行数会放大", "excel_join", "high", "to_many", None,
    ),
    (
        "medical_expense_rec", "source_patient_id", "patient", "source_patient_id",
        "many_to_one", "患者维度（可选直连）", "excel_join", "high", "none", None,
    ),
    (
        "examination_record", "dcid", "examination_exam", "dcid",
        "one_to_many", "一个检查记录对应多个检查项目", "data_dictionary", "high", "to_many", None,
    ),
    (
        "prescription_record", "visit_id", "visit", "visit_id",
        "many_to_one", "处方所属就诊", "data_dictionary", "high", "none", None,
    ),
    (
        "prescription_record", "source_patient_id", "patient", "source_patient_id",
        "many_to_one", "处方所属患者", "data_dictionary", "high", "none", None,
    ),
    (
        "diagnose_record", "visit_id", "visit", "visit_id",
        "many_to_one", "诊断所属就诊", "excel_join", "high", "none", None,
    ),
    (
        "operation_record", "dcid", "operation_operation", "dcid",
        "one_to_many", "一个手术记录对应多个手术操作", "data_dictionary", "high", "to_many", None,
    ),
    (
        "operation_operation", "operation_code", "qmjk_dict", "dict_code",
        "many_to_one", "手术编码字典；dict_type 取值待确认", "excel_join", "medium", "none",
        "qmjk_dict.dict_type = '<待确认>'",
    ),
    (
        "diagnose_record", "diagnose_code", "qmjk_dict", "dict_code",
        "many_to_one", "诊断编码字典", "excel_join", "high", "none",
        "qmjk_dict.dict_type = 'ICD-10'",
    ),
]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_docx_tables(path: Path) -> dict[str, dict]:
    root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
    tables: dict[str, dict] = {}
    for tbl in root.iter(W + "tbl"):
        rows: list[list[str]] = []
        for tr in tbl.iter(W + "tr"):
            cells = [
                "".join(t.text or "" for t in tc.iter(W + "t")).strip()
                for tc in tr.iter(W + "tc")
            ]
            if any(cells):
                rows.append(cells)
        if len(rows) < 3:
            continue
        title = rows[0][0]
        match = re.search(r"（([a-z_]+)）", title)
        if not match:
            continue
        name = match.group(1)
        fields = [r for r in rows[2:] if len(r) >= 7 and r[0]]
        tables[name] = {"title": title, "fields": fields}
    return tables


def map_type(fmt: str, length: str, dictionary: str) -> str:
    fmt = (fmt or "").strip().upper()
    length = (length or "").strip().upper()
    numbers = re.findall(r"\d+", length)
    n = int(numbers[-1]) if numbers else 0
    if fmt.startswith("DT") or length.startswith("DT"):
        return "DATETIME"
    if fmt in {"D", "DATE"} or length.startswith("D"):
        return "DATE"
    if fmt.startswith("L"):
        return "VARCHAR(1)"
    if fmt.startswith("B"):
        return "BLOB"
    if fmt.startswith("S") or dictionary:
        return f"VARCHAR({n})" if n else "VARCHAR(255)"
    if fmt.startswith("N"):
        if n and n <= 9:
            return "INTEGER"
        return f"NUMERIC({n})" if n else "NUMERIC"
    return f"VARCHAR({n})" if n else "VARCHAR(255)"


def column_from_docx(row: list[str]) -> dict:
    node, cn_name, fmt, length, required, dictionary, definition = row[:7]
    desc = cn_name
    if definition:
        desc = f"{cn_name}；{definition}" if cn_name else definition
    nullable = required.strip().upper() != "Y"
    col = {
        "name": node,
        "type": map_type(fmt, length, dictionary),
        "nullable": nullable,
        "is_required": "N" if nullable else "Y",
        "description": desc[:1000],
    }
    if cn_name and cn_name != node:
        col["aliases"] = [cn_name]
    if dictionary:
        col["dictionary"] = dictionary
    if node in PII_NAMES:
        col["is_pii"] = True
        col["pii_policy"] = "禁止出现在 SELECT 输出"
    if node in TIME_ROLES:
        col["time_role"] = TIME_ROLES[node]
    return col


def build_new_tables(docx_path: Path) -> dict[str, dict]:
    parsed = parse_docx_tables(docx_path)
    missing = set(NEW_TABLES) - set(parsed)
    if missing:
        raise SystemExit(f"docx missing tables: {sorted(missing)}")
    out: dict[str, dict] = {}
    for name, meta in NEW_TABLES.items():
        fields = parsed[name]["fields"]
        columns = [column_from_docx(r) for r in fields]
        out[name] = {
            "name": name,
            "description": meta["description"],
            "grain": meta["grain"],
            "primary_key": meta["primary_key"],
            "columns": columns,
            "relationships": [],
        }
    return out


def fix_existing_tables(schema: dict) -> None:
    by_name = {t["name"]: t for t in schema["tables"]}
    exam = by_name.get("examination_record")
    if exam:
        for col in exam.get("columns", []):
            if col["name"] == "dcdid":
                col["name"] = "dcid"
        exam["primary_key"] = ["dcid"]
    op = by_name.get("operation_operation")
    if op:
        for col in op.get("columns", []):
            if col["name"] == "modify_flag":
                col["type"] = "VARCHAR(2)"


def add_semantics(schema: dict) -> None:
    by_name = {t["name"]: t for t in schema["tables"]}
    for (table_name, col_name), values in ENUMS.items():
        table = by_name.get(table_name)
        if not table:
            continue
        for col in table.get("columns", []):
            if col["name"] == col_name:
                col["enum_values"] = values
                col.pop("dictionary", None)
                break
    for table in schema["tables"]:
        for col in table.get("columns", []):
            if "nullable" not in col:
                col["nullable"] = str(col.get("is_required", "N")).upper() != "Y"
            if "is_required" not in col:
                col["is_required"] = "N" if col["nullable"] else "Y"
            if col["name"] in PII_NAMES:
                col["is_pii"] = True
                col.setdefault("pii_policy", "禁止出现在 SELECT 输出")
            if col["name"] in TIME_ROLES:
                col["time_role"] = TIME_ROLES[col["name"]]


def add_relationships(schema: dict) -> None:
    by_name = {t["name"]: t for t in schema["tables"]}
    for (from_table, from_col, to_table, to_col, typ, note, source, confidence, fanout, filt) in RELATIONSHIPS:
        table = by_name.get(from_table)
        if not table:
            continue
        table.setdefault("relationships", [])
        if any(r.get("to_table") == to_table and r.get("on", "").find(from_col) >= 0 for r in table["relationships"]):
            continue
        rel = {
            "name": f"{from_table}_to_{to_table}",
            "to_table": to_table,
            "on": f"{from_table}.{from_col} = {to_table}.{to_col}",
            "type": typ,
            "note": note,
            "source": source,
            "confidence": confidence,
            "fanout": fanout,
        }
        if filt:
            rel["filter"] = filt
        table["relationships"].append(rel)


def validate_schema(schema: dict) -> list[str]:
    errors: list[str] = []
    tables = {t["name"]: t for t in schema["tables"]}
    if len(tables) != 13:
        errors.append(f"expected 13 tables, got {len(tables)}")
    for name, table in tables.items():
        cols = {c["name"] for c in table.get("columns", [])}
        for pk in table.get("primary_key", []):
            if pk not in cols:
                errors.append(f"{name}: primary key {pk} missing")
        for rel in table.get("relationships", []):
            target = tables.get(rel.get("to_table"))
            if target is None:
                errors.append(f"{name}: relationship target {rel.get('to_table')} missing")
                continue
            on = rel.get("on", "")
            for part in re.findall(r"[a-zA-Z_][\w]*\.[a-zA-Z_][\w]*", on):
                tname, cname = part.split(".", 1)
                if tname not in tables:
                    errors.append(f"{name}: relationship references table {tname}")
                elif cname not in {c["name"] for c in tables[tname].get("columns", [])}:
                    errors.append(f"{name}: relationship references {part}")
    return errors


def stage_schema(docx_path: Path = DEFAULT_DOCX) -> None:
    schema = load_json(DATA / "schema.json")
    schema["version"] = "1.1.0"
    schema["dialect"] = schema.get("dialect", "oracle")
    fix_existing_tables(schema)
    new_tables = build_new_tables(docx_path)
    existing = {t["name"]: t for t in schema["tables"]}
    for name, table in new_tables.items():
        existing[name] = table
    schema["tables"] = list(existing.values())
    add_semantics(schema)
    add_relationships(schema)
    errors = validate_schema(schema)
    if errors:
        for e in errors:
            print("ERROR:", e)
        raise SystemExit(1)
    write_json(DATA / "schema.json", schema)
    print(f"schema: {len(schema['tables'])} tables, "
          f"{sum(len(t['columns']) for t in schema['tables'])} columns")
    for t in schema["tables"]:
        print(f"  {t['name']}: {len(t['columns'])} cols, {len(t.get('relationships', []))} rels")


NAME_TO_ID = {
    "门诊人次": "outpatient_visits",
    "急诊人次": "emergency_visits",
    "医保患者人次": "insured_visits",
    "男性患者人次": "male_patients",
    "女性患者人次": "female_visits",
    "检查人次": "examination_visits",
    "检验人次": "inspection_visits",
    "门急诊总收入": "outpatient_total_revenue",
    "门诊药品收入": "outpatient_drug_revenue",
    "门诊药品处方数": "outpatient_prescription_count",
    "检查项目排名": "examination_ranking",
    "门诊费用排名": "outpatient_cost_ranking",
    "住院人次": "hospitalization_visits",
    "住院医保人次": "inpatient_insured_visits",
    "住院检查人次": "inpatient_examination_visits",
    "住院检验人次": "inpatient_inspection_visits",
    "住院总费用": "inpatient_total_cost",
    "住院药品费用": "inpatient_drug_cost",
    "住院医保患者费用": "inpatient_insured_cost",
    "住院医保患者药品费用": "inpatient_insured_drug_cost",
    "住院诊断排名": "inpatient_diagnosis_ranking",
    "费用构成": "cost_composition",
    "总手术例数": "total_surgeries",
    "择期手术例数": "elective_surgeries",
    "一级手术例数": "level1_surgeries",
    "二级手术例数": "level2_surgeries",
    "三级手术例数": "level3_surgeries",
    "四级手术例数": "level4_surgeries",
    "手术排名": "surgery_ranking",
}

METRIC_ORDER = [
    "outpatient_visits", "emergency_visits", "insured_visits", "male_patients",
    "female_visits", "examination_visits", "inspection_visits",
    "outpatient_total_revenue", "outpatient_drug_revenue",
    "outpatient_prescription_count", "examination_ranking",
    "outpatient_cost_ranking", "outpatient_insured_ratio",
    "outpatient_avg_cost", "patient_avg_drug_cost", "outpatient_drug_ratio",
    "hospitalization_visits", "inpatient_insured_visits",
    "inpatient_examination_visits", "inpatient_inspection_visits",
    "inpatient_total_cost", "inpatient_drug_cost", "inpatient_insured_cost",
    "inpatient_insured_drug_cost", "inpatient_diagnosis_ranking",
    "cost_composition", "inpatient_insured_ratio", "inpatient_avg_cost",
    "total_surgeries", "elective_surgeries", "level1_surgeries",
    "level2_surgeries", "level3_surgeries", "level4_surgeries",
    "surgery_ranking",
]

FORMULA_METRICS = [
    (
        "outpatient_insured_ratio", "门急诊医保患者比例",
        "医保患者人次 / (门诊人次 + 急诊人次) * 100%",
        ["visit", "medical_expense_rec"],
    ),
    (
        "outpatient_avg_cost", "门诊均次费用",
        "门急诊总收入 / (门诊人次 + 急诊人次)",
        ["visit", "medical_expense_rec"],
    ),
    (
        "patient_avg_drug_cost", "患者次均药费",
        "门诊药品收入 / (门诊人次 + 急诊人次)",
        ["visit", "encounter_medical_cost"],
    ),
    (
        "outpatient_drug_ratio", "门诊药占比",
        "门诊药品收入 / 门急诊总收入 * 100%",
        ["encounter_medical_cost"],
    ),
    (
        "inpatient_insured_ratio", "医保患者比例（住院）",
        "住院医保人次 / 住院人次 * 100%",
        ["visit", "medical_expense_rec"],
    ),
    (
        "inpatient_avg_cost", "住院次均费用",
        "住院总费用 / 住院人次",
        ["visit", "medical_expense_rec"],
    ),
]


def extract_tables(sql: str, schema_tables: set[str]) -> list[str]:
    found = re.findall(r"(?i)(?:from|join)\s+([a-zA-Z_][\w]*)", sql or "")
    return sorted({t for t in found if t in schema_tables})


def stage_gold() -> None:
    schema = load_json(DATA / "schema.json")
    schema_tables = {t["name"] for t in schema["tables"]}
    gold = load_json(DATA / "gold_sql.json")
    items = []
    seen = set()
    for item in gold.get("items", []):
        name = item.get("metric_name", "")
        metric_id = NAME_TO_ID.get(name)
        if not metric_id:
            raise SystemExit(f"unknown gold metric_name: {name}")
        if metric_id in seen:
            raise SystemExit(f"duplicate metric: {metric_id}")
        seen.add(metric_id)
        tables = extract_tables(item.get("sql", ""), schema_tables)
        if not tables:
            raise SystemExit(f"no tables extracted for {name}")
        items.append({
            "id": f"gold_{metric_id}",
            "metric_id": metric_id,
            "metric_name": name,
            "kind": "sql",
            "sql": item.get("sql", ""),
            "tables": tables,
            "notes": item.get("notes", "原始 Oracle SQL"),
        })
    for metric_id, name, formula, tables in FORMULA_METRICS:
        items.append({
            "id": f"gold_formula_{metric_id}",
            "metric_id": metric_id,
            "metric_name": name,
            "kind": "formula",
            "formula": formula,
            "tables": tables,
            "notes": "公式指标，无可执行 SQL",
        })
    order = {mid: i for i, mid in enumerate(METRIC_ORDER)}
    items.sort(key=lambda x: order.get(x["metric_id"], 999))
    gold["version"] = "1.1.0"
    gold["dialect"] = "oracle"
    gold["items"] = items
    if len(items) != 35:
        raise SystemExit(f"expected 35 gold items, got {len(items)}")
    write_json(DATA / "gold_sql.json", gold)
    print(f"gold: {len(items)} items "
          f"({sum(1 for x in items if x['kind']=='sql')} sql, "
          f"{sum(1 for x in items if x['kind']=='formula')} formula)")


METRICS = [
    # id, name, category, aliases, definition, formula, tables, time_field, default, notes, unit
    ("outpatient_visits", "门诊人次", "门急诊情况", ["门诊量", "门诊就诊人次"],
     "统计周期内门诊就诊记录去重人次",
     "COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time)",
     ["visit"], "visit.treatment_occurrence_time", "按就诊发生日期、自然月",
     ["人次 vs 人数", "门诊是否包含急诊"], "人次"),
    ("emergency_visits", "急诊人次", "门急诊情况", ["急诊量"],
     "统计周期内急诊就诊记录去重人次",
     "COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time)",
     ["visit"], "visit.treatment_occurrence_time", "按就诊发生日期、自然月",
     ["急诊是否包含留观"], "人次"),
    ("insured_visits", "医保患者人次", "门急诊情况", ["医保人次", "参保患者人次"],
     "统计周期内医保患者的门诊/急诊就诊记录去重人次",
     "COUNT(DISTINCT CASE WHEN activity_type_code IN ('1','2') THEN visit_id || id_card || treatment_occurrence_time END)",
     ["visit", "medical_expense_rec"], "visit.treatment_occurrence_time", "按就诊发生日期、自然月",
     ["医保类型范围", "医保患者 vs 医保结算"], "人次"),
    ("male_patients", "男性患者人次", "门急诊情况", ["男性患者", "男性门诊人次"],
     "统计周期内男性患者的门诊/急诊就诊人次",
     "COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time)",
     ["visit", "patient"], "visit.treatment_occurrence_time", "按自然月统计",
     ["男性是否只算门诊"], "人次"),
    ("female_visits", "女性患者人次", "门急诊情况", ["女性患者", "女性门诊人次"],
     "统计周期内女性患者的门诊/急诊就诊人次",
     "COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time)",
     ["visit", "patient"], "visit.treatment_occurrence_time", "按自然月统计",
     ["女性是否只算门诊"], "人次"),
    ("examination_visits", "检查人次", "门急诊情况", ["检查量", "检查人数"],
     "统计周期内门诊/急诊检查记录去重人次",
     "COUNT(DISTINCT 检查记录)",
     ["visit", "examination_record"], "examination_record.execute_date", "按检查日期、自然月",
     ["检查人次 vs 检查项目数"], "人次"),
    ("inspection_visits", "检验人次", "门急诊情况", ["检验量", "检验人数"],
     "统计周期内门诊/急诊检验记录去重人次",
     "COUNT(DISTINCT 检验记录)",
     ["visit", "inspection_record"], "inspection_record.inspection_date", "按检验日期、自然月",
     ["检验人次 vs 检验项目数"], "人次"),
    ("outpatient_total_revenue", "门急诊总收入", "门急诊情况", ["门急诊收入", "总收入", "门诊总收入"],
     "统计周期内门急诊结算的总费用",
     "SUM(IFNULL(outpatient_cost, 0))",
     ["medical_expense_rec"], "medical_expense_rec.settle_date_time", "按结算日期、自然月",
     ["门急诊 vs 住院", "按结算日期归属", "是否含医保/自费"], "元"),
    ("outpatient_drug_revenue", "门诊药品收入", "门急诊情况", ["药品收入", "门诊药费"],
     "统计周期内门诊药品费用合计",
     "SUM(IFNULL(total_cost, 0))",
     ["encounter_medical_cost"], "encounter_medical_cost.cost_date", "按费用发生日期、自然月",
     ["药品项目类型范围", "门诊 vs 急诊"], "元"),
    ("outpatient_prescription_count", "门诊药品处方数", "门急诊情况", ["处方数", "药品处方数"],
     "统计周期内门诊药品处方数量",
     "COUNT(1)",
     ["prescription_record"], "prescription_record.prescription_date", "按处方日期、自然月",
     ["处方数 vs 处方明细数"], "张"),
    ("examination_ranking", "检查项目排名", "门急诊情况", ["检查排名"],
     "统计周期内各检查项目的检查数量排名",
     "GROUP BY 检查项目 COUNT(1) ORDER BY DESC",
     ["examination_exam", "examination_record", "visit"], "examination_record.execute_date", "按检查日期、自然月",
     ["按项目还是按部位", "排名取前几"], "排名"),
    ("outpatient_cost_ranking", "门诊费用排名", "门急诊情况", ["费用排名"],
     "统计周期内门诊各费用项目的金额排名",
     "GROUP BY 费用项目 SUM(total_cost) ORDER BY DESC",
     ["encounter_medical_cost", "qmjk_dict"], "encounter_medical_cost.charging_date", "按收费日期、自然月",
     ["按项目还是按患者", "排名取前几"], "排名"),
    ("outpatient_insured_ratio", "门急诊医保患者比例", "门急诊情况", ["门急诊医保占比"],
     "医保患者人次占门诊+急诊人次的比例",
     "医保患者人次 / (门诊人次 + 急诊人次) * 100%",
     ["visit", "medical_expense_rec"], "visit.treatment_occurrence_time", "按自然月统计",
     ["分母是否含急诊", "医保类型范围"], "%"),
    ("outpatient_avg_cost", "门诊均次费用", "门急诊情况", ["门诊次均费用", "均次费用"],
     "门急诊总收入除以门诊+急诊人次",
     "门急诊总收入 / (门诊人次 + 急诊人次)",
     ["visit", "medical_expense_rec"], "visit.treatment_occurrence_time", "按自然月统计",
     ["分子按结算日期，分母按就诊日期", "是否含急诊"], "元/人次"),
    ("patient_avg_drug_cost", "患者次均药费", "门急诊情况", ["次均药费"],
     "门诊药品收入除以门诊+急诊人次",
     "门诊药品收入 / (门诊人次 + 急诊人次)",
     ["visit", "encounter_medical_cost"], "visit.treatment_occurrence_time", "按自然月统计",
     ["药品收入按费用日期", "分母范围"], "元/人次"),
    ("outpatient_drug_ratio", "门诊药占比", "门急诊情况", ["药占比"],
     "门诊药品收入占门急诊总收入的比例",
     "门诊药品收入 / 门急诊总收入 * 100%",
     ["encounter_medical_cost"], "encounter_medical_cost.cost_date", "按自然月统计",
     ["药品范围", "分母总收入范围"], "%"),
    ("hospitalization_visits", "住院人次", "住院情况", ["住院量"],
     "统计周期内住院就诊记录去重人次",
     "COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time)",
     ["visit"], "visit.treatment_occurrence_time", "按就诊发生日期、自然月",
     ["住院类型 5/6/9", "人次 vs 人数"], "人次"),
    ("inpatient_insured_visits", "住院医保人次", "住院情况", ["住院医保患者人次"],
     "统计周期内住院医保患者人次",
     "COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time)",
     ["visit", "medical_expense_rec"], "visit.treatment_occurrence_time", "按自然月统计",
     ["医保类型范围", "住院类型 5/6/9"], "人次"),
    ("inpatient_examination_visits", "住院检查人次", "住院情况", ["住院检查量"],
     "统计周期内住院检查记录去重人次",
     "COUNT(DISTINCT 检查记录)",
     ["visit", "examination_record"], "examination_record.execute_date", "按检查日期、自然月",
     ["住院范围", "检查人次 vs 项目数"], "人次"),
    ("inpatient_inspection_visits", "住院检验人次", "住院情况", ["住院检验量"],
     "统计周期内住院检验记录去重人次",
     "COUNT(DISTINCT 检验记录)",
     ["visit", "inspection_record"], "inspection_record.inspection_date", "按检验日期、自然月",
     ["住院范围", "检验人次 vs 项目数"], "人次"),
    ("inpatient_total_cost", "住院总费用", "住院情况", ["住院收入"],
     "统计周期内住院结算的总费用",
     "SUM(IFNULL(hospitalization_total_cost, 0))",
     ["medical_expense_rec"], "medical_expense_rec.settle_date_time", "按结算日期、自然月",
     ["按结算日期归属", "是否含医保/自费"], "元"),
    ("inpatient_drug_cost", "住院药品费用", "住院情况", ["住院药费"],
     "统计周期内住院药品费用合计",
     "SUM(IFNULL(total_cost, 0))",
     ["advice_cost"], "advice_cost.cost_date", "按费用日期、自然月",
     ["药品项目类型", "住院范围"], "元"),
    ("inpatient_insured_cost", "住院医保患者费用", "住院情况", ["住院医保费用"],
     "统计周期内住院医保患者费用合计",
     "SUM(IFNULL(hospitalization_total_cost, 0))",
     ["medical_expense_rec"], "medical_expense_rec.settle_date_time", "按结算日期、自然月",
     ["医保类型范围", "住院范围"], "元"),
    ("inpatient_insured_drug_cost", "住院医保患者药品费用", "住院情况", ["住院医保药费"],
     "统计周期内住院医保患者药品费用合计",
     "SUM(IFNULL(total_cost, 0))",
     ["advice_cost"], "advice_cost.cost_date", "按费用日期、自然月",
     ["医保范围", "药品项目类型"], "元"),
    ("inpatient_diagnosis_ranking", "住院诊断排名", "住院情况", ["诊断排名"],
     "统计周期内住院诊断数量排名",
     "GROUP BY 诊断 COUNT(1) ORDER BY DESC",
     ["diagnose_record", "qmjk_dict", "visit"], "diagnose_record.diag_n_osis_date", "按诊断日期、自然月",
     ["按诊断编码还是名称", "排名取前几"], "排名"),
    ("cost_composition", "费用构成", "住院情况", ["收入构成", "费用结构"],
     "统计周期内住院费用按项目类型的构成",
     "GROUP BY item_type_code SUM(total_cost)",
     ["advice_cost", "qmjk_dict"], "advice_cost.cost_date", "按费用日期、自然月",
     ["项目类型映射", "金额还是占比"], "元/%"),
    ("inpatient_insured_ratio", "医保患者比例（住院）", "住院情况", ["住院医保占比"],
     "住院医保人次占住院人次的比例",
     "住院医保人次 / 住院人次 * 100%",
     ["visit", "medical_expense_rec"], "visit.treatment_occurrence_time", "按自然月统计",
     ["医保类型范围", "住院类型 5/6/9"], "%"),
    ("inpatient_avg_cost", "住院次均费用", "住院情况", ["住院次均"],
     "住院总费用除以住院人次",
     "住院总费用 / 住院人次",
     ["visit", "medical_expense_rec"], "medical_expense_rec.settle_date_time", "按自然月统计",
     ["分子按结算日期", "分母按就诊日期"], "元/人次"),
    ("total_surgeries", "总手术例数", "手术情况", ["手术台次", "手术量"],
     "统计周期内手术操作例数",
     "COUNT(DISTINCT 手术操作)",
     ["operation_record", "operation_operation"], "operation_operation.operation_start_date_time", "按手术开始时间、自然月",
     ["手术例数 vs 手术人次", "手术级别范围"], "例"),
    ("elective_surgeries", "择期手术例数", "手术情况", ["择期手术量"],
     "统计周期内择期手术例数",
     "COUNT(DISTINCT 手术操作) WHERE operation_period_type_code='1'",
     ["operation_record", "operation_operation"], "operation_operation.operation_start_date_time", "按手术开始时间、自然月",
     ["择期定义", "手术级别范围"], "例"),
    ("level1_surgeries", "一级手术例数", "手术情况", ["一级手术量"],
     "统计周期内一级手术例数",
     "COUNT(DISTINCT 手术操作) WHERE operation_level_code='1'",
     ["operation_record", "operation_operation"], "operation_operation.operation_start_date_time", "按手术开始时间、自然月",
     ["手术级别口径"], "例"),
    ("level2_surgeries", "二级手术例数", "手术情况", ["二级手术量"],
     "统计周期内二级手术例数",
     "COUNT(DISTINCT 手术操作) WHERE operation_level_code='2'",
     ["operation_record", "operation_operation"], "operation_operation.operation_start_date_time", "按手术开始时间、自然月",
     ["手术级别口径"], "例"),
    ("level3_surgeries", "三级手术例数", "手术情况", ["三级手术量"],
     "统计周期内三级手术例数",
     "COUNT(DISTINCT 手术操作) WHERE operation_level_code='3'",
     ["operation_record", "operation_operation"], "operation_operation.operation_start_date_time", "按手术开始时间、自然月",
     ["手术级别口径"], "例"),
    ("level4_surgeries", "四级手术例数", "手术情况", ["四级手术量"],
     "统计周期内四级手术例数",
     "COUNT(DISTINCT 手术操作) WHERE operation_level_code='4'",
     ["operation_record", "operation_operation"], "operation_operation.operation_start_date_time", "按手术开始时间、自然月",
     ["手术级别口径"], "例"),
    ("surgery_ranking", "手术排名", "手术情况", ["手术项目排名"],
     "统计周期内各手术项目的数量排名",
     "GROUP BY operation_code COUNT(1) ORDER BY DESC",
     ["operation_record", "operation_operation", "qmjk_dict"], "operation_operation.operation_start_date_time", "按手术开始时间、自然月",
     ["按手术编码还是名称", "排名取前几"], "排名"),
]

FORMULA_IDS = {
    "outpatient_insured_ratio", "outpatient_avg_cost", "patient_avg_drug_cost",
    "outpatient_drug_ratio", "inpatient_insured_ratio", "inpatient_avg_cost",
}


def stage_metrics() -> None:
    gold = load_json(DATA / "gold_sql.json")
    gold_ids = {x["id"] for x in gold["items"]}
    items = []
    for (mid, name, category, aliases, definition, formula, tables, time_field, default, notes, unit) in METRICS:
        gold_ref = f"gold_formula_{mid}" if mid in FORMULA_IDS else f"gold_{mid}"
        if gold_ref not in gold_ids:
            raise SystemExit(f"missing gold ref {gold_ref} for {mid}")
        items.append({
            "id": mid,
            "name": name,
            "category": category,
            "aliases": aliases,
            "definition": definition,
            "formula": formula,
            "gold_sql_ref": gold_ref,
            "related_tables": tables,
            "time_field": time_field,
            "default_interpretation": default,
            "ambiguity_notes": notes,
            "unit": unit,
        })
    if len(items) != 35:
        raise SystemExit(f"expected 35 metrics, got {len(items)}")
    write_json(DATA / "metrics.json", {"version": "1.1.0", "metrics": items})
    print(f"metrics: {len(items)}")


TABLE_TERMS = {
    "visit": "就诊记录",
    "patient": "患者",
    "medical_expense_rec": "医疗费用记录",
    "encounter_medical_cost": "门急诊费用",
    "advice_cost": "医嘱费用",
    "examination_record": "检查记录",
    "examination_exam": "检查项目",
    "inspection_record": "检验记录",
    "operation_record": "手术记录",
    "operation_operation": "手术操作",
    "prescription_record": "处方记录",
    "diagnose_record": "诊断记录",
    "qmjk_dict": "字典",
}

TIME_TERMS = [
    ("就诊时间", ["诊疗时间", "发生时间"], "visit", "treatment_occurrence_time"),
    ("就诊日期", ["就诊日"], "visit", "visit_date_time"),
    ("入院时间", ["入院日期"], "visit", "admission_date_time"),
    ("出院时间", ["出院日期"], "visit", "discharge_date_time"),
    ("结算时间", ["结算日期"], "medical_expense_rec", "settle_date_time"),
    ("门急诊费用发生日期", ["门急诊费用日期"], "encounter_medical_cost", "cost_date"),
    ("住院费用发生日期", ["住院费用日期"], "advice_cost", "cost_date"),
    ("检查日期", ["检查时间"], "examination_record", "execute_date"),
    ("检验日期", ["检验时间"], "inspection_record", "inspection_date"),
    ("处方日期", ["开方日期"], "prescription_record", "prescription_date"),
    ("诊断日期", ["诊断时间"], "diagnose_record", "diag_n_osis_date"),
    ("手术开始时间", ["手术时间"], "operation_operation", "operation_start_date_time"),
    ("手术日期", ["手术记录日期"], "operation_record", "operation_date_time"),
    ("出生日期", ["生日"], "patient", "birth_date"),
]

ENUM_TERMS = [
    ("门诊", ["门诊患者"], "visit", "activity_type_code", "1"),
    ("急诊", ["急诊患者"], "visit", "activity_type_code", "2"),
    ("住院", ["住院患者"], "visit", "activity_type_code", None, ["5", "6", "9"]),
    ("门急诊", ["门急诊患者"], "visit", "activity_type_code", None, ["1", "2"]),
    ("男性", ["男", "男性患者"], "patient", "sex_code", "1"),
    ("女性", ["女", "女性患者"], "patient", "sex_code", "2"),
    ("一级手术", ["一级手术例数"], "operation_operation", "operation_level_code", "1"),
    ("二级手术", ["二级手术例数"], "operation_operation", "operation_level_code", "2"),
    ("三级手术", ["三级手术例数"], "operation_operation", "operation_level_code", "3"),
    ("四级手术", ["四级手术例数"], "operation_operation", "operation_level_code", "4"),
    ("择期手术", ["择期"], "operation_record", "operation_period_type_code", "1"),
    ("医保", ["医保患者", "医保结算"], "medical_expense_rec", "medical_cost_pay_type_code", None,
     ["01", "02", "03", "09"]),
    ("检查结果正常", ["正常"], "examination_exam", "qualitativeresults_code", "1"),
    ("检查结果异常", ["异常"], "examination_exam", "qualitativeresults_code", "2"),
    ("检查结果不确定", ["不确定"], "examination_exam", "qualitativeresults_code", "3"),
]

COST_TERMS = [
    ("药品收入", ["药品费用"], ["1", "2", "3", "11"]),
    ("检查收入", ["检查费用"], ["5"]),
    ("检验收入", ["检验费用"], ["4"]),
    ("治疗收入", ["治疗费用"], ["7"]),
    ("手术收入", ["手术费用"], ["6"]),
    ("材料收入", ["材料费用"], ["10", "12"]),
]


def stage_glossary() -> None:
    schema = load_json(DATA / "schema.json")
    metrics = load_json(DATA / "metrics.json")["metrics"]
    tables = {t["name"]: t for t in schema["tables"]}
    columns = {
        t["name"]: {c["name"] for c in t.get("columns", [])}
        for t in schema["tables"]
    }
    terms: list[dict] = []
    seen: set[str] = set()

    def add(term, aliases, target_type, target, source, confidence="high",
            match=None, values=None):
        if not term or term in seen:
            return
        seen.add(term)
        item = {
            "term": term,
            "aliases": aliases or [],
            "target_type": target_type,
            "target": target,
            "source": source,
            "confidence": confidence,
        }
        if match:
            item["match"] = match
        if values is not None:
            item["values"] = values
        terms.append(item)

    for name, term in TABLE_TERMS.items():
        if name in tables:
            add(term, [], "table", {"table": name}, "schema.table")

    for term, aliases, table, column in TIME_TERMS:
        if table in columns and column in columns[table]:
            add(term, aliases, "time_range", {"table": table, "column": column},
                "schema.time", match="time_range")

    for row in ENUM_TERMS:
        term, aliases, table, column, value = row[:5]
        values = row[5] if len(row) > 5 else None
        if table in columns and column in columns[table]:
            if values:
                add(term, aliases, "enum",
                    {"table": table, "column": column, "values": values},
                    "schema.enum", match="in")
            else:
                add(term, aliases, "enum",
                    {"table": table, "column": column, "value": value},
                    "schema.enum", match="enum")

    for term, aliases, values in COST_TERMS:
        add(term, aliases, "enum",
            {"table": "advice_cost", "column": "item_type_code", "values": values},
            "excel_note", match="in")

    add("科室", ["科室名称"], "column",
        {"table": "visit", "column": "dept_name"}, "schema.column",
        match="contains")
    add("医生", ["医师"], "column",
        {"table": "visit", "column": "diagnose_doctor_name"}, "schema.column",
        match="contains")
    add("患者", [], "table", {"table": "patient"}, "schema.table")
    add("性别", [], "column", {"table": "patient", "column": "sex_code"},
        "schema.column", match="enum")

    for m in metrics:
        add(m["name"], m.get("aliases", []), "metric",
            {"metric_id": m["id"]}, "metrics", match="metric")

    needs_value_dictionary = [
        {"table": "visit", "column": "dept_name", "reason": "科室具体名称"},
        {"table": "diagnose_record", "column": "diagnose_name", "reason": "诊断名称"},
        {"table": "examination_exam", "column": "examination_item_name", "reason": "检查项目名称"},
        {"table": "medical_expense_rec", "column": "medical_cost_pay_type_code", "reason": "医保类型细分标签"},
    ]

    errors = []
    for item in terms:
        target = item["target"]
        if item["target_type"] == "metric":
            if target["metric_id"] not in {m["id"] for m in metrics}:
                errors.append(f"metric target missing: {target}")
        elif item["target_type"] == "table":
            if target["table"] not in tables:
                errors.append(f"table target missing: {target}")
        else:
            table, column = target.get("table"), target.get("column")
            if table not in tables or column not in columns.get(table, set()):
                errors.append(f"column target missing: {target}")
    if errors:
        for e in errors:
            print("ERROR:", e)
        raise SystemExit(1)
    if len(terms) < 30:
        raise SystemExit(f"glossary too small: {len(terms)}")
    write_json(DATA / "glossary.json", {
        "version": "1.1.0",
        "terms": terms,
        "needs_value_dictionary": needs_value_dictionary,
    })
    print(f"glossary: {len(terms)} terms")


AMBIGUITIES = [
    {
        "code": "time_range",
        "category": "time",
        "trigger_terms": ["上个月", "本月", "最近", "近", "今年", "去年"],
        "question": "您说的『{term}』是指自然月还是最近30天？",
        "options": ["自然月", "最近30天"],
        "default": "自然月",
        "blocking": True,
        "priority": 10,
        "related_columns": [
            "visit.treatment_occurrence_time",
            "medical_expense_rec.settle_date_time",
            "encounter_medical_cost.cost_date",
            "advice_cost.cost_date",
        ],
        "related_metrics": [],
    },
    {
        "code": "time_field_scope",
        "category": "time",
        "trigger_terms": ["住院", "出院", "结算", "费用", "检查", "检验", "手术"],
        "question": "这个指标按哪个时间字段统计？",
        "options": ["就诊时间", "入院时间", "出院时间", "结算时间", "费用发生时间"],
        "default": "按指标默认时间字段",
        "blocking": True,
        "priority": 20,
        "related_columns": [
            "visit.treatment_occurrence_time",
            "visit.admission_date_time",
            "visit.discharge_date_time",
            "medical_expense_rec.settle_date_time",
            "encounter_medical_cost.cost_date",
            "advice_cost.cost_date",
            "examination_record.execute_date",
            "inspection_record.inspection_date",
            "operation_operation.operation_start_date_time",
        ],
        "related_metrics": ["hospitalization_visits", "inpatient_total_cost", "total_surgeries"],
    },
    {
        "code": "visit_type_scope",
        "category": "entity",
        "trigger_terms": ["门诊人次", "门急诊", "住院人次", "门诊量"],
        "question": "门诊人次是只统计门诊，还是包含急诊？住院是否包含 5/6/9 类型？",
        "options": ["仅门诊", "门诊+急诊", "住院(5/6/9)"],
        "default": "仅门诊",
        "blocking": True,
        "priority": 30,
        "related_columns": ["visit.activity_type_code"],
        "related_metrics": ["outpatient_visits", "emergency_visits", "hospitalization_visits"],
    },
    {
        "code": "metric_scope",
        "category": "metric",
        "trigger_terms": ["人次", "人数", "例数", "费用", "收入", "次均", "药占比"],
        "question": "您要的是哪个指标口径？",
        "options": ["人次", "人数", "例数", "总收入", "次均费用"],
        "default": "按指标默认口径",
        "blocking": True,
        "priority": 40,
        "related_columns": [],
        "related_metrics": [
            "outpatient_visits", "outpatient_total_revenue", "outpatient_avg_cost",
            "inpatient_total_cost", "inpatient_avg_cost",
        ],
    },
    {
        "code": "patient_scope",
        "category": "patient",
        "trigger_terms": ["住院患者", "出院", "在院", "患者范围"],
        "question": "统计范围包括出院患者、在院患者，还是全部？",
        "options": ["出院患者", "在院患者", "全部"],
        "default": "全部",
        "blocking": True,
        "priority": 50,
        "related_columns": [
            "visit.admission_date_time",
            "visit.discharge_date_time",
            "visit.visit_status_code",
        ],
        "related_metrics": ["hospitalization_visits", "inpatient_total_cost"],
    },
    {
        "code": "department_entity",
        "category": "entity",
        "trigger_terms": ["科室", "心内科", "外科", "内科"],
        "question": "科室是精确匹配，还是按名称包含？",
        "options": ["精确匹配", "名称包含"],
        "default": "名称包含",
        "blocking": True,
        "priority": 60,
        "related_columns": ["visit.dept_name", "visit.dept_code"],
        "related_metrics": [],
    },
    {
        "code": "aggregation",
        "category": "metric",
        "trigger_terms": ["总量", "合计", "平均", "排名", "去重"],
        "question": "您要总量、平均值、去重计数，还是排名？",
        "options": ["总量", "平均值", "去重计数", "排名"],
        "default": "按指标默认",
        "blocking": True,
        "priority": 70,
        "related_columns": [],
        "related_metrics": ["examination_ranking", "surgery_ranking", "cost_composition"],
    },
    {
        "code": "insurance_scope",
        "category": "insurance",
        "trigger_terms": ["医保", "职工医保", "居民医保", "医保患者"],
        "question": "医保患者是全部医保，还是只算职工/居民医保？",
        "options": ["全部医保", "职工医保", "居民医保"],
        "default": "全部医保",
        "blocking": False,
        "priority": 80,
        "related_columns": [
            "medical_expense_rec.medical_cost_pay_type_code",
            "patient.ml_insurance_category_code",
        ],
        "related_metrics": ["insured_visits", "outpatient_insured_ratio", "inpatient_insured_ratio"],
    },
]


def stage_ambiguities() -> None:
    schema = load_json(DATA / "schema.json")
    metrics = load_json(DATA / "metrics.json")["metrics"]
    tables = {t["name"]: t for t in schema["tables"]}
    columns = {t["name"]: {c["name"] for c in t.get("columns", [])} for t in schema["tables"]}
    metric_ids = {m["id"] for m in metrics}
    errors = []
    for rule in AMBIGUITIES:
        for path in rule.get("related_columns", []):
            table, column = path.split(".", 1)
            if table not in tables or column not in columns.get(table, set()):
                errors.append(f"{rule['code']}: related column missing {path}")
        for mid in rule.get("related_metrics", []):
            if mid not in metric_ids:
                errors.append(f"{rule['code']}: related metric missing {mid}")
    if errors:
        for e in errors:
            print("ERROR:", e)
        raise SystemExit(1)
    write_json(DATA / "ambiguities.json", {
        "version": "1.1.0",
        "rules": AMBIGUITIES,
    })
    print(f"ambiguities: {len(AMBIGUITIES)} rules")


SQLITE_EXAMPLES = [
    {
        "id": "ex_sqlite_outpatient_visits",
        "source_gold_id": "gold_outpatient_visits",
        "question": "统计2025年1月门诊人次",
        "tables": ["visit"],
        "pattern": "filter+count_distinct",
        "sql": "SELECT COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time) AS cnt FROM visit WHERE activity_type_code = '1' AND modify_flag IN ('1','2') AND treatment_occurrence_time >= '2025-01-01' AND treatment_occurrence_time < '2025-02-01'",
        "notes": "按就诊发生日期；人次去重",
    },
    {
        "id": "ex_sqlite_emergency_visits",
        "source_gold_id": "gold_emergency_visits",
        "question": "统计2025年1月急诊人次",
        "tables": ["visit"],
        "pattern": "filter+count_distinct",
        "sql": "SELECT COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time) AS cnt FROM visit WHERE activity_type_code = '2' AND modify_flag IN ('1','2') AND treatment_occurrence_time >= '2025-01-01' AND treatment_occurrence_time < '2025-02-01'",
        "notes": "按就诊发生日期；人次去重",
    },
    {
        "id": "ex_sqlite_insured_visits",
        "source_gold_id": "gold_insured_visits",
        "question": "统计2025年1月医保患者人次",
        "tables": ["visit", "medical_expense_rec"],
        "pattern": "exists+count_distinct",
        "sql": "SELECT COUNT(DISTINCT v.visit_id || v.id_card || v.treatment_occurrence_time) AS cnt FROM visit v WHERE v.activity_type_code IN ('1','2') AND v.modify_flag IN ('1','2') AND v.treatment_occurrence_time >= '2025-01-01' AND v.treatment_occurrence_time < '2025-02-01' AND EXISTS (SELECT 1 FROM medical_expense_rec m WHERE m.visit_id = v.visit_id AND m.medical_cost_pay_type_code IN ('01','02','03','09') AND m.modify_flag IN ('1','2'))",
        "notes": "医保代码 01/02/03/09；门诊+急诊",
    },
    {
        "id": "ex_sqlite_male_patients",
        "source_gold_id": "gold_male_patients",
        "question": "统计2025年1月男性患者人次",
        "tables": ["visit", "patient"],
        "pattern": "join+filter+count_distinct",
        "sql": "SELECT COUNT(DISTINCT v.visit_id || v.id_card || v.treatment_occurrence_time) AS cnt FROM visit v JOIN patient p ON p.source_patient_id = v.source_patient_id WHERE v.activity_type_code IN ('1','2') AND p.sex_code = '1' AND v.modify_flag IN ('1','2') AND p.modify_flag IN ('1','2') AND v.treatment_occurrence_time >= '2025-01-01' AND v.treatment_occurrence_time < '2025-02-01'",
        "notes": "性别代码 1=男；join 患者",
    },
    {
        "id": "ex_sqlite_outpatient_total_revenue",
        "source_gold_id": "gold_outpatient_total_revenue",
        "question": "统计2025年1月门急诊总收入",
        "tables": ["medical_expense_rec"],
        "pattern": "sum",
        "sql": "SELECT IFNULL(SUM(outpatient_cost), 0) AS total FROM medical_expense_rec WHERE modify_flag IN ('1','2') AND settle_date_time >= '2025-01-01' AND settle_date_time < '2025-02-01'",
        "notes": "按结算日期；空值按0",
    },
    {
        "id": "ex_sqlite_outpatient_drug_revenue",
        "source_gold_id": "gold_outpatient_drug_revenue",
        "question": "统计2025年1月门诊药品收入",
        "tables": ["encounter_medical_cost"],
        "pattern": "filter+sum",
        "sql": "SELECT IFNULL(SUM(total_cost), 0) AS total FROM encounter_medical_cost WHERE modify_flag IN ('1','2') AND item_type IN ('1','2','3','11') AND patient_type_code IN ('01','02') AND cost_date >= '2025-01-01' AND cost_date < '2025-02-01'",
        "notes": "药品项目类型 1/2/3/11；按费用日期",
    },
    {
        "id": "ex_sqlite_outpatient_prescription_count",
        "source_gold_id": "gold_outpatient_prescription_count",
        "question": "统计2025年1月门诊药品处方数",
        "tables": ["prescription_record"],
        "pattern": "filter+count",
        "sql": "SELECT COUNT(1) AS cnt FROM prescription_record WHERE modify_flag IN ('1','2') AND prescription_date >= '2025-01-01' AND prescription_date < '2025-02-01'",
        "notes": "按处方日期计数",
    },
    {
        "id": "ex_sqlite_examination_visits",
        "source_gold_id": "gold_examination_visits",
        "question": "统计2025年1月门诊检查人次",
        "tables": ["visit", "examination_record"],
        "pattern": "join+filter+count_distinct",
        "sql": "SELECT COUNT(DISTINCT r.dcid) AS cnt FROM examination_record r JOIN visit v ON v.visit_id = r.visit_id WHERE r.modify_flag IN ('1','2') AND v.activity_type_code IN ('1','2') AND r.execute_date >= '2025-01-01' AND r.execute_date < '2025-02-01'",
        "notes": "按检查日期；检查记录去重",
    },
    {
        "id": "ex_sqlite_examination_ranking",
        "source_gold_id": "gold_examination_ranking",
        "question": "2025年1月检查项目排名前10",
        "tables": ["examination_exam", "examination_record", "visit"],
        "pattern": "join+group+rank",
        "sql": "SELECT e.examination_item_code, e.examination_item_name, COUNT(1) AS cnt FROM examination_record r JOIN examination_exam e ON r.dcid = e.dcid JOIN visit v ON v.visit_id = r.visit_id WHERE r.modify_flag IN ('1','2') AND e.modify_flag IN ('1','2') AND v.activity_type_code IN ('1','2') AND r.execute_date >= '2025-01-01' AND r.execute_date < '2025-02-01' GROUP BY e.examination_item_code, e.examination_item_name ORDER BY cnt DESC LIMIT 10",
        "notes": "检查记录 join 检查项目；按项目计数",
    },
    {
        "id": "ex_sqlite_hospitalization_visits",
        "source_gold_id": "gold_hospitalization_visits",
        "question": "统计2025年1月住院人次",
        "tables": ["visit"],
        "pattern": "filter+count_distinct",
        "sql": "SELECT COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time) AS cnt FROM visit WHERE activity_type_code IN ('5','6','9') AND modify_flag IN ('1','2') AND treatment_occurrence_time >= '2025-01-01' AND treatment_occurrence_time < '2025-02-01'",
        "notes": "住院类型 5/6/9",
    },
    {
        "id": "ex_sqlite_inpatient_total_cost",
        "source_gold_id": "gold_inpatient_total_cost",
        "question": "统计2025年1月住院总费用",
        "tables": ["medical_expense_rec"],
        "pattern": "sum",
        "sql": "SELECT IFNULL(SUM(hospitalization_total_cost), 0) AS total FROM medical_expense_rec WHERE modify_flag IN ('1','2') AND settle_date_time >= '2025-01-01' AND settle_date_time < '2025-02-01'",
        "notes": "按结算日期；空值按0",
    },
    {
        "id": "ex_sqlite_cost_composition",
        "source_gold_id": "gold_cost_composition",
        "question": "2025年1月住院费用构成",
        "tables": ["advice_cost", "qmjk_dict"],
        "pattern": "group+sum",
        "sql": "SELECT a.item_type_code, IFNULL(SUM(a.total_cost), 0) AS total FROM advice_cost a WHERE a.modify_flag IN ('1','2') AND a.cost_date >= '2025-01-01' AND a.cost_date < '2025-02-01' GROUP BY a.item_type_code ORDER BY total DESC",
        "notes": "按费用项目类型汇总",
    },
    {
        "id": "ex_sqlite_total_surgeries",
        "source_gold_id": "gold_total_surgeries",
        "question": "统计2025年1月总手术例数",
        "tables": ["operation_record", "operation_operation"],
        "pattern": "join+filter+count_distinct",
        "sql": "SELECT COUNT(DISTINCT o.docid) AS cnt FROM operation_record r JOIN operation_operation o ON r.dcid = o.dcid WHERE r.modify_flag IN ('1','2') AND o.modify_flag IN ('1','2') AND o.operation_level_code IN ('1','2','3','4') AND o.operation_start_date_time >= '2025-01-01' AND o.operation_start_date_time < '2025-02-01'",
        "notes": "手术级别 1-4；手术操作去重",
    },
    {
        "id": "ex_sqlite_surgery_ranking",
        "source_gold_id": "gold_surgery_ranking",
        "question": "2025年1月手术排名前10",
        "tables": ["operation_record", "operation_operation", "qmjk_dict"],
        "pattern": "join+group+rank",
        "sql": "SELECT o.operation_code, IFNULL(q.dict_label, o.operation_name) AS item_name, COUNT(1) AS cnt FROM operation_record r JOIN operation_operation o ON r.dcid = o.dcid LEFT JOIN qmjk_dict q ON q.dict_code = o.operation_code WHERE r.modify_flag IN ('1','2') AND o.modify_flag IN ('1','2') AND o.operation_level_code IN ('1','2','3','4') AND o.operation_start_date_time >= '2025-01-01' AND o.operation_start_date_time < '2025-02-01' GROUP BY o.operation_code, IFNULL(q.dict_label, o.operation_name) ORDER BY cnt DESC LIMIT 10",
        "notes": "按手术编码分组；字典名称可选",
    },
    {
        "id": "ex_sqlite_inpatient_diagnosis_ranking",
        "source_gold_id": "gold_inpatient_diagnosis_ranking",
        "question": "2025年1月住院诊断排名前10",
        "tables": ["diagnose_record", "qmjk_dict", "visit"],
        "pattern": "join+group+rank",
        "sql": "SELECT d.diagnose_code, IFNULL(q.dict_label, d.diagnose_name) AS item_name, COUNT(1) AS cnt FROM diagnose_record d JOIN visit v ON v.visit_id = d.visit_id LEFT JOIN qmjk_dict q ON q.dict_type = 'ICD-10' AND q.dict_code = d.diagnose_code WHERE d.modify_flag IN ('1','2') AND v.activity_type_code IN ('5','6','9') AND d.diag_n_osis_date >= '2025-01-01' AND d.diag_n_osis_date < '2025-02-01' GROUP BY d.diagnose_code, IFNULL(q.dict_label, d.diagnose_name) ORDER BY cnt DESC LIMIT 10",
        "notes": "住院诊断；字典类型 ICD-10",
    },
]


def sqlite_type(t: str) -> str:
    t = (t or "").upper()
    if "INT" in t:
        return "INTEGER"
    if any(x in t for x in ("NUM", "DEC", "NUMBER", "FLOAT", "DOUBLE", "REAL")):
        return "REAL"
    if "BLOB" in t:
        return "BLOB"
    return "TEXT"


def make_temp_db(schema: dict) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for table in schema["tables"]:
        defs = ", ".join(
            f'"{c["name"]}" {sqlite_type(c.get("type", "TEXT"))}'
            for c in table["columns"]
        )
        conn.execute(f'CREATE TABLE "{table["name"]}" ({defs})')
    return conn


def stage_examples() -> None:
    schema = load_json(DATA / "schema.json")
    conn = make_temp_db(schema)
    try:
        for ex in SQLITE_EXAMPLES:
            try:
                conn.execute(ex["sql"]).fetchmany(5)
            except Exception as exc:
                raise SystemExit(f"{ex['id']} failed: {exc}") from exc
    finally:
        conn.close()
    if not (10 <= len(SQLITE_EXAMPLES) <= 15):
        raise SystemExit(f"expected 10-15 examples, got {len(SQLITE_EXAMPLES)}")
    write_json(DATA / "sql_examples_sqlite.json", {
        "version": "1.1.0",
        "dialect": "sqlite",
        "schema_version": schema.get("version", "1.1.0"),
        "examples": SQLITE_EXAMPLES,
    })
    print(f"sql_examples_sqlite: {len(SQLITE_EXAMPLES)} examples, all executed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=[
        "schema", "gold", "metrics", "glossary", "ambiguities", "examples", "all"
    ])
    parser.add_argument("--docx", type=Path, default=DEFAULT_DOCX)
    args = parser.parse_args()
    stages = ["schema", "gold", "metrics", "glossary", "ambiguities", "examples"] \
        if args.stage == "all" else [args.stage]
    for stage in stages:
        if stage == "schema":
            stage_schema(args.docx)
        elif stage == "gold":
            stage_gold()
        elif stage == "metrics":
            stage_metrics()
        elif stage == "glossary":
            stage_glossary()
        elif stage == "ambiguities":
            stage_ambiguities()
        elif stage == "examples":
            stage_examples()


if __name__ == "__main__":
    main()
