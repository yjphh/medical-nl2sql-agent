"""Build the deterministic SQLite mirror used for execution feedback.

``data/schema.json`` is the only schema source.  Every value is synthetic:
a fixed seed decides the rows, join keys are drawn from shared pools so joins
return rows, and PII columns are filled with obviously fake values.

Usage::

    python -m app.tools.sql_agent.build_db
    python -m app.tools.sql_agent.build_db --no-verify
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from app.tools.sql_agent.executor import execute_readonly

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "data" / "schema.json"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "synthetic" / "medical.db"
DEFAULT_EXAMPLES_PATH = PROJECT_ROOT / "data" / "sql_examples_sqlite.json"

PATIENT_COUNT = 60
VISIT_COUNT = 240

MONEY_HINTS = ("cost", "fee", "amount", "price", "charge", "income", "revenue", "balance", "pay")

CODE_POOLS: dict[str, list[tuple[str, str]]] = {
    "modify_flag": [("1", "新增"), ("2", "修改")],
    "activity_type_code": [
        ("1", "门诊"),
        ("2", "急诊"),
        ("5", "住院"),
        ("6", "住院（其他）"),
        ("9", "住院（其他）"),
    ],
    "sex_code": [("1", "男"), ("2", "女")],
    "patient_type_code": [("01", "门诊患者"), ("02", "急诊患者"), ("03", "住院患者")],
    "medical_cost_pay_type_code": [
        ("01", "城镇职工医保"),
        ("02", "城乡居民医保"),
        ("03", "公务员医保"),
        ("09", "其他医保"),
    ],
    "item_type": [("1", "药品"), ("2", "检查"), ("3", "检验"), ("11", "材料")],
    "item_type_code": [("1", "药品"), ("2", "检查"), ("3", "检验"), ("11", "材料")],
    "operation_level_code": [("1", "一级手术"), ("2", "二级手术"), ("3", "三级手术"), ("4", "四级手术")],
    "qualitativeresults_code": [("1", "正常"), ("2", "异常"), ("3", "不确定")],
}

ICD_CODES = [
    ("I10", "原发性高血压"),
    ("E11.9", "2型糖尿病"),
    ("J18.9", "肺炎"),
    ("K35.9", "急性阑尾炎"),
    ("I25.1", "冠心病"),
    ("N18.9", "慢性肾病"),
    ("J44.9", "慢性阻塞性肺疾病"),
    ("S72.0", "股骨颈骨折"),
]
OPERATION_CODES = [
    ("OP001", "阑尾切除术"),
    ("OP002", "腹腔镜胆囊切除术"),
    ("OP003", "冠状动脉支架置入术"),
    ("OP004", "全髋关节置换术"),
    ("OP005", "剖宫产术"),
    ("OP006", "骨折内固定术"),
]
EXAM_ITEMS = [
    ("EX001", "腹部彩超"),
    ("EX002", "胸部CT"),
    ("EX003", "心电图"),
    ("EX004", "头颅MRI"),
    ("EX005", "腹部平片"),
]

INTERNAL_KEYS = ("_dt", "_patient")


# --------------------------------------------------------------------------- #
# schema -> SQLite DDL
# --------------------------------------------------------------------------- #
def sqlite_type(declared: str) -> str:
    kind = declared.upper()
    if kind.startswith("INT"):
        return "INTEGER"
    if "BLOB" in kind:
        return "BLOB"
    if any(token in kind for token in ("NUMBER", "NUMERIC", "DEC", "REAL", "FLOAT", "DOUBLE")):
        return "NUMERIC"
    return "TEXT"


def create_tables(connection: sqlite3.Connection, schema: dict) -> None:
    for table in schema["tables"]:
        primary_key = set(table.get("primary_key") or [])
        definitions = []
        for column in table["columns"]:
            definition = f'"{column["name"]}" {sqlite_type(column["type"])}'
            if column["name"] in primary_key:
                definition += " PRIMARY KEY"
            definitions.append(definition)
        connection.execute(
            f'CREATE TABLE "{table["name"]}" (\n  ' + ",\n  ".join(definitions) + "\n)"
        )


INDEXES = [
    ("patient", "source_patient_id"),
    ("visit", "visit_id"),
    ("visit", "source_patient_id"),
    ("medical_expense_rec", "visit_id"),
    ("encounter_medical_cost", "visit_id"),
    ("prescription_record", "visit_id"),
    ("examination_record", "visit_id"),
    ("examination_exam", "dcid"),
    ("inspection_record", "visit_id"),
    ("operation_record", "visit_id"),
    ("operation_operation", "dcid"),
    ("advice_cost", "visit_id"),
    ("diagnose_record", "visit_id"),
]


def create_indexes(connection: sqlite3.Connection, schema: dict) -> None:
    columns = {table["name"]: {c["name"] for c in table["columns"]} for table in schema["tables"]}
    for position, (table, column) in enumerate(INDEXES):
        if column in columns.get(table, set()):
            connection.execute(
                f'CREATE INDEX IF NOT EXISTS "idx_{position}_{table}_{column}" '
                f'ON "{table}" ("{column}")'
            )


# --------------------------------------------------------------------------- #
# synthetic rows
# --------------------------------------------------------------------------- #
def generate_data(schema: dict, seed: int = 42) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    patients = _patient_rows(rng)
    visits = _visit_rows(rng, patients)
    examinations, exams = _examination_rows(rng, visits)
    operations, operation_items = _operation_rows(rng, visits)
    data = {
        "patient": patients,
        "visit": visits,
        "medical_expense_rec": _expense_rows(rng, visits),
        "encounter_medical_cost": _encounter_cost_rows(rng, visits),
        "prescription_record": _prescription_rows(rng, visits),
        "examination_record": examinations,
        "examination_exam": exams,
        "inspection_record": _inspection_rows(rng, visits),
        "operation_record": operations,
        "operation_operation": operation_items,
        "advice_cost": _advice_rows(rng, visits),
        "diagnose_record": _diagnose_rows(rng, visits),
        "qmjk_dict": _dict_rows(),
    }
    tables = {table["name"]: table for table in schema["tables"]}
    for name, rows in data.items():
        table = tables.get(name)
        if table is None:
            continue
        for index, row in enumerate(rows):
            _complete_row(table, row, rng, index)
    return data


def _patient_rows(rng: random.Random) -> list[dict]:
    rows = []
    for index in range(PATIENT_COUNT):
        rows.append(
            {
                "dcid": f"PAT{index:04d}",
                "source_patient_id": f"P{index:04d}",
                "source_patient_id_type": "01",
                "sex_code": rng.choice(["1", "2"]),
                "birth_date": _random_birth_date(rng),
                "name": f"患者{index:04d}",
                "id_card": f"11010119900101{index % 1000:03d}X",
                "id_card_name": f"患者{index:04d}",
                "health_card_id": f"H{index:06d}",
                "patient_phone": f"138{index:08d}",
            }
        )
    return rows


def _visit_rows(rng: random.Random, patients: list[dict]) -> list[dict]:
    rows = []
    for index in range(VISIT_COUNT):
        patient = rng.choice(patients)
        activity = _pick_activity(rng)
        arrived = _random_moment(rng)
        inpatient = activity in ("5", "6", "9")
        row = {
            "dcid": f"V{index:06d}",
            "id": f"V{index:06d}",
            "visit_id": f"VIS{index:06d}",
            "clinic_id": f"VIS{index:06d}",
            "activity_type_code": activity,
            "patient_type_code": _patient_type(activity),
            "treatment_occurrence_time": _fmt_dt(arrived),
            "visit_org_code": "ORG001",
            "author_org_code": "ORG001",
        }
        if inpatient:
            row["hospitalization_id"] = f"HZ{index:06d}"
        row.update(_person_fields(patient))
        row["_dt"] = arrived
        row["_patient"] = patient
        rows.append(row)
    return rows


def _expense_rows(rng: random.Random, visits: list[dict]) -> list[dict]:
    rows = []
    for visit in visits:
        outpatient = visit["activity_type_code"] in ("1", "2")
        for _ in range(rng.randint(1, 2)):
            settled = visit["_dt"] + timedelta(hours=rng.randint(0, 48))
            row = {
                "dcid": f"MER{len(rows):06d}",
                "id": f"MER{len(rows):06d}",
                "visit_id": visit["visit_id"],
                "patient_type_code": visit["patient_type_code"],
                "visit_date_time": _fmt_dt(visit["_dt"]),
                "settle_date_time": _fmt_dt(settled),
                "medical_cost_pay_type_code": rng.choice(["01", "02", "03", "09"]),
                "outpatient_cost": round(rng.uniform(50, 1500), 2) if outpatient else 0,
                "hospitalization_total_cost": 0.0
                if outpatient
                else round(rng.uniform(1000, 50000), 2),
            }
            if not outpatient:
                row["hospitalization_id"] = visit.get("hospitalization_id")
            row.update(_person_fields(visit["_patient"]))
            row["_dt"] = visit["_dt"]
            rows.append(row)
    return rows


def _encounter_cost_rows(rng: random.Random, visits: list[dict]) -> list[dict]:
    rows = []
    for visit in visits:
        item_type = rng.choice(["1", "2", "3", "11"])
        row = {
            "dcid": f"EMC{len(rows):06d}",
            "id": f"EMC{len(rows):06d}",
            "visit_id": visit["visit_id"],
            "patient_type_code": visit["patient_type_code"],
            "visit_date_time": _fmt_dt(visit["_dt"]),
            "cost_date": _fmt_dt(visit["_dt"]),
            "item_type": item_type,
            "item_name": _code_label("item_type", item_type),
            "total_cost": round(rng.uniform(20, 2000), 2),
            "self_cost": round(rng.uniform(0, 500), 2),
            "self_care_cost": round(rng.uniform(0, 200), 2),
        }
        row.update(_person_fields(visit["_patient"]))
        row["_dt"] = visit["_dt"]
        rows.append(row)
    return rows


def _prescription_rows(rng: random.Random, visits: list[dict]) -> list[dict]:
    rows = []
    for visit in visits:
        if visit["activity_type_code"] not in ("1", "2") or rng.random() > 0.7:
            continue
        diagnose_code, diagnose_name = rng.choice(ICD_CODES)
        row = {
            "dcid": f"PR{len(rows):06d}",
            "id": f"PR{len(rows):06d}",
            "visit_id": visit["visit_id"],
            "visit_date_time": _fmt_dt(visit["_dt"]),
            "activity_type_code": visit["activity_type_code"],
            "patient_type_code": visit["patient_type_code"],
            "prescription_date": _fmt_date(visit["_dt"]),
            "diagnose_code": diagnose_code,
            "diagnose_name": diagnose_name,
            "age": rng.randint(18, 85),
        }
        row.update(_person_fields(visit["_patient"]))
        row["_dt"] = visit["_dt"]
        rows.append(row)
    return rows


def _examination_rows(rng: random.Random, visits: list[dict]) -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    exams: list[dict] = []
    for visit in visits:
        if rng.random() > 0.6:
            continue
        record_dcid = f"ER{len(records):06d}"
        records.append(
            {
                "dcid": record_dcid,
                "id": record_dcid,
                "visit_id": visit["visit_id"],
                "patient_type_code": visit["patient_type_code"],
                "visit_date_time": _fmt_dt(visit["_dt"]),
                "execute_date": _fmt_date(visit["_dt"]),
                **{key: value for key, value in _person_fields(visit["_patient"]).items()},
                "_dt": visit["_dt"],
            }
        )
        for _ in range(rng.randint(1, 3)):
            item_code, item_name = rng.choice(EXAM_ITEMS)
            exams.append(
                {
                    "docid": f"EE{len(exams):06d}",
                    "dcid": record_dcid,
                    "id": f"EE{len(exams):06d}",
                    "examination_item_code": item_code,
                    "examination_item_name": item_name,
                    "operation_date_time": _fmt_dt(visit["_dt"]),
                    "modify_flag": "1",
                    "_dt": visit["_dt"],
                }
            )
    return records, exams


def _inspection_rows(rng: random.Random, visits: list[dict]) -> list[dict]:
    rows = []
    for visit in visits:
        if rng.random() > 0.4:
            continue
        row = {
            "dcid": f"IR{len(rows):06d}",
            "id": f"IR{len(rows):06d}",
            "visit_id": visit["visit_id"],
            "patient_type_code": visit["patient_type_code"],
            "activity_type_code": visit["activity_type_code"],
            "record_title": "检验报告",
        }
        row.update(_person_fields(visit["_patient"]))
        row["_dt"] = visit["_dt"]
        rows.append(row)
    return rows


def _operation_rows(rng: random.Random, visits: list[dict]) -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    items: list[dict] = []
    for visit in visits:
        if visit["activity_type_code"] not in ("5", "6", "9") or rng.random() > 0.6:
            continue
        record_dcid = f"OR{len(records):06d}"
        records.append(
            {
                "dcid": record_dcid,
                "id": record_dcid,
                "visit_id": visit["visit_id"],
                "patient_type_code": "03",
                "visit_date_time": _fmt_dt(visit["_dt"]),
                "hospitalization_id": visit.get("hospitalization_id"),
                "age": str(rng.randint(18, 85)),
                "_dt": visit["_dt"],
            }
        )
        for _ in range(rng.randint(1, 2)):
            code, name = rng.choice(OPERATION_CODES)
            start = visit["_dt"] + timedelta(hours=rng.randint(1, 24))
            items.append(
                {
                    "docid": f"OO{len(items):06d}",
                    "dcid": record_dcid,
                    "id": f"OO{len(items):06d}",
                    "operation_code": code,
                    "operation_name": name,
                    "operation_level_code": rng.choice(["1", "2", "3", "4"]),
                    "operation_start_date_time": _fmt_dt(start),
                    "operation_end_date_time": _fmt_dt(start + timedelta(hours=1)),
                    "modify_flag": "1",
                    "_dt": visit["_dt"],
                }
            )
    return records, items


def _advice_rows(rng: random.Random, visits: list[dict]) -> list[dict]:
    rows = []
    for visit in visits:
        if rng.random() > 0.7:
            continue
        for _ in range(rng.randint(1, 2)):
            item_type_code = rng.choice(["1", "2", "3", "11"])
            rows.append(
                {
                    "dcid": f"AC{len(rows):06d}",
                    "visit_id": visit["visit_id"],
                    "cost_date": _fmt_date(visit["_dt"]),
                    "item_type_code": item_type_code,
                    "total_cost": round(rng.uniform(10, 3000), 2),
                    "self_cost": round(rng.uniform(0, 800), 2),
                    "self_care_cost": round(rng.uniform(0, 300), 2),
                    "modify_flag": "1",
                    "_dt": visit["_dt"],
                }
            )
    return rows


def _diagnose_rows(rng: random.Random, visits: list[dict]) -> list[dict]:
    rows = []
    for visit in visits:
        inpatient = visit["activity_type_code"] in ("5", "6", "9")
        if not inpatient and rng.random() > 0.2:
            continue
        code, name = rng.choice(ICD_CODES)
        row = {
            "dcid": f"DR{len(rows):06d}",
            "id": f"DR{len(rows):06d}",
            "visit_id": visit["visit_id"],
            "patient_type_code": visit["patient_type_code"],
            "diag_n_osis_date": _fmt_dt(visit["_dt"]),
            "diagnose_code": code,
            "diagnose_name": name,
            "dept_code": "DPT01",
            "dept_name": "内科",
        }
        row.update(_person_fields(visit["_patient"]))
        row["_dt"] = visit["_dt"]
        rows.append(row)
    return rows


def _dict_rows() -> list[dict]:
    rows = [
        {"dict_type": "ICD-10", "dict_code": code, "dict_label": label}
        for code, label in ICD_CODES
    ]
    rows += [
        {"dict_type": "operation", "dict_code": code, "dict_label": label}
        for code, label in OPERATION_CODES
    ]
    return rows


# --------------------------------------------------------------------------- #
# generic column filling
# --------------------------------------------------------------------------- #
def _complete_row(table: dict, row: dict, rng: random.Random, index: int) -> None:
    base = row.get("_dt") or datetime(2025, 1, 15, 9, 0, 0)
    for column in table["columns"]:
        name = column["name"]
        if name in row or name.endswith("_name"):
            continue
        row[name] = _value_for(column, rng, base, index)

    for column in table["columns"]:
        name = column["name"]
        if name in row:
            continue
        if column.get("is_pii"):
            row[name] = _pii_value(name, index, rng)
            continue
        if not name.endswith("_name"):
            continue
        label = _derive_label(name, row)
        if label is not None:
            row[name] = label
        elif "org_name" in name:
            row[name] = "示范医院"


def _value_for(column: dict, rng: random.Random, base: datetime, index: int):
    name = column["name"]
    kind = column["type"].upper()
    if column.get("is_pii"):
        return _pii_value(name, index, rng)
    if name in CODE_POOLS:
        return rng.choice([code for code, _ in CODE_POOLS[name]])
    if "BLOB" in kind:
        return None
    if "DATE" in kind or "TIME" in kind:
        return _date_value(name, kind, base, rng)
    if any(token in kind for token in ("INT", "NUM", "DEC", "REAL", "FLOAT", "DOUBLE")):
        return _number_value(name, rng)
    if name.endswith("_flag"):
        return "1"
    if name.endswith("_code"):
        return f"{rng.randint(1, 4):02d}"
    if name.endswith("_id"):
        return f"X{index:06d}"
    return None


def _pii_value(name: str, index: int, rng: random.Random) -> str:
    low = name.lower()
    if low in ("name", "id_card_name"):
        return f"患者{index:04d}"
    if low == "id_card":
        return f"11010119900101{index % 1000:03d}X"
    if low == "health_card_id":
        return f"H{index:06d}"
    if low == "medical_card_id":
        return f"M{index:06d}"
    if low == "patient_phone":
        return f"138{index:08d}"
    if low == "source_patient_id":
        return f"P{index % PATIENT_COUNT:04d}"
    if low == "birth_date":
        return _random_birth_date(rng)
    if "work_unit" in low:
        return "某单位"
    return f"X{index:06d}"


def _derive_label(name: str, row: dict) -> str | None:
    key = name[: -len("_name")]
    for code_key in (key + "_code", key):
        if code_key not in row:
            continue
        pool = CODE_POOLS.get(code_key)
        if pool:
            return dict(pool).get(str(row[code_key]))
    return None


def _number_value(name: str, rng: random.Random):
    low = name.lower()
    if "age" in low:
        return rng.randint(18, 85)
    if any(hint in low for hint in MONEY_HINTS):
        return round(rng.uniform(10, 3000), 2)
    if "day" in low:
        return rng.randint(1, 15)
    if any(hint in low for hint in ("count", "times", "num", "qty", "quantity")):
        return rng.randint(1, 5)
    return rng.randint(1, 100)


def _date_value(name: str, kind: str, base: datetime, rng: random.Random) -> str:
    if "birth" in name.lower():
        return _random_birth_date(rng)
    moment = base + timedelta(days=rng.choice([0, 0, 0, 1, -1]), minutes=rng.randint(0, 600))
    if "TIME" in kind:
        return _fmt_dt(moment)
    return _fmt_date(moment)


def _person_fields(patient: dict) -> dict:
    return {
        "source_patient_id": patient["source_patient_id"],
        "name": patient["name"],
        "id_card": patient["id_card"],
        "id_card_name": patient["name"],
        "health_card_id": patient["health_card_id"],
        "sex_code": patient["sex_code"],
        "birth_date": patient["birth_date"],
        "patient_phone": patient.get("patient_phone"),
    }


def _pick_activity(rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.55:
        return "1"
    if roll < 0.70:
        return "2"
    return rng.choice(["5", "6", "9"])


def _patient_type(activity: str) -> str:
    if activity in ("5", "6", "9"):
        return "03"
    return "02" if activity == "2" else "01"


def _code_label(pool: str, code: str) -> str:
    return dict(CODE_POOLS.get(pool, [])).get(code, code)


def _random_moment(rng: random.Random) -> datetime:
    start = datetime(2024, 12, 1)
    span = int((datetime(2025, 4, 1) - start).total_seconds())
    return start + timedelta(seconds=rng.randrange(span))


def _random_birth_date(rng: random.Random) -> str:
    born = datetime(1950, 1, 1) + timedelta(days=rng.randint(0, 20_000))
    return born.strftime("%Y-%m-%d")


def _fmt_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_date(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# build + verify
# --------------------------------------------------------------------------- #
def insert_rows(connection: sqlite3.Connection, table: dict, rows: list[dict]) -> None:
    names = [column["name"] for column in table["columns"]]
    quoted = ", ".join(f'"{name}"' for name in names)
    placeholders = ", ".join("?" for _ in names)
    values = [tuple(row.get(name) for name in names) for row in rows]
    connection.executemany(
        f'INSERT INTO "{table["name"]}" ({quoted}) VALUES ({placeholders})', values
    )


def build(
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
    db_path: str | Path = DEFAULT_DB_PATH,
    seed: int = 42,
) -> dict[str, int]:
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    data = generate_data(schema, seed=seed)
    destination = Path(db_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    connection = sqlite3.connect(destination)
    try:
        create_tables(connection, schema)
        for table in schema["tables"]:
            insert_rows(connection, table, data.get(table["name"], []))
        create_indexes(connection, schema)
        connection.commit()
    finally:
        connection.close()
    return {name: len(rows) for name, rows in data.items()}


def verify_examples(
    db_path: str | Path = DEFAULT_DB_PATH,
    examples_path: str | Path = DEFAULT_EXAMPLES_PATH,
    *,
    max_rows: int = 50,
    timeout_s: float = 5.0,
) -> int:
    """Run every SQLite example against the mirror; return the failure count."""
    examples = json.loads(Path(examples_path).read_text(encoding="utf-8"))["examples"]
    failures = 0
    empty = 0
    for example in examples:
        result = execute_readonly(db_path, example["sql"], max_rows=max_rows, timeout_s=timeout_s)
        if not result.ok:
            failures += 1
            print(f"[FAIL] {example['id']}: {result.error}")
            continue
        if result.row_count == 0:
            empty += 1
        print(f"[ok]   {example['id']}: rows={result.row_count}")
    if empty:
        print(f"warning: {empty} example(s) returned no rows")
    print(f"examples: {len(examples) - failures}/{len(examples)} executed")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the SQLite mirror for SQL generation")
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH))
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-verify", action="store_true")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    counts = build(schema_path=args.schema, db_path=args.db, seed=args.seed)
    for name, count in counts.items():
        print(f"  {name:24s} {count:5d} rows")
    print(f"built {args.db}")

    if args.no_verify:
        return 0
    failures = verify_examples(args.db)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
