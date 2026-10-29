# 知识层 6 个 JSON 补全计划（data/）

> 输入：
> - `补充.docx`：3 张缺失表的完整字段定义
> - `大屏指标校验脚本(2).xlsx`：35 个指标（29 SQL + 6 公式）
> - 现有 `data/gold_sql.json`：29 条 Oracle SQL
> - 现有 `data/schema.json`：10 张表、632 列
>
> 本轮**只做 `data/`**，不改 `app/knowledge/`。

## 1. 范围

完成 6 个 JSON：

1. `schema.json`
2. `gold_sql.json`
3. `metrics.json`
4. `glossary.json`
5. `ambiguities.json`
6. `sql_examples_sqlite.json`

暂不做：

- `gold_sql_sqlite.json`
- `sql_examples.json`（Oracle 短示例）

## 2. 执行顺序

```text
schema → gold_sql → metrics → glossary → ambiguities → sql_examples_sqlite
```

`metrics` 的 `gold_sql_ref` 必须指向已存在的 gold ID，所以 **gold 先于 metrics**。

## 3. 第一步：schema 补 3 张表 + 关系图谱

### 3.1 从 docx 解析 3 张表

| 表 | 字段数 | 主键 | 说明 |
|---|---:|---|---|
| `examination_exam` | 47 | `docid` | 检查记录-检查项目，一行一个检查项目 |
| `prescription_record` | 69 | `dcid` | 处方记录，一行一张处方 |
| `diagnose_record` | 43 | `dcid` | 诊断记录，一行一个诊断 |

完成后：**13 张表、约 791 列**。

### 3.2 数据格式映射

docx 的列是：`节点标识`、`中文名称`、`数据格式`、`长度`、`必填`、`字典`、`定义`。

| docx 格式 | schema 类型 |
|---|---|
| `S1` + `AN..n` | `VARCHAR(n)` |
| `S3` + `AN..n` / `N..n` | `VARCHAR(n)`（代码） |
| `N` / `N1` + `N..n` | `NUMERIC(n)` 或 `INTEGER` |
| `D` + `D8` | `DATE` |
| `DT` + `DT15` | `DATETIME` |
| `L` + `T/F` | `VARCHAR(1)` / `BOOLEAN` |
| `B` / BLOB | `BLOB` / `TEXT` |
| 必填 `Y/N` | `nullable: false/true` |
| `字典` | 记录 `dictionary` 代码；有枚举值时写 `enum_values` |
| `中文名称` / `定义` | `description` / `aliases` |

### 3.3 新增关系

最小字段：`to_table`、`on`、`type`、`note`、`source`、`confidence`。

- `examination_record.dcid = examination_exam.dcid`（1:N）
- `prescription_record.visit_id = visit.visit_id`（N:1）
- `prescription_record.source_patient_id = patient.source_patient_id`（N:1）
- `diagnose_record.visit_id = visit.visit_id`（N:1）
- `diagnose_record.diagnose_code = qmjk_dict.dict_code` + `dict_type='ICD-10'`（N:1）

同时补齐现有 10 张表：

- `visit.source_patient_id = patient.source_patient_id`（N:1）
- `visit.visit_id = medical_expense_rec.visit_id`（1:N，扇出）
- `visit.visit_id = examination_record.visit_id`（1:N）
- `visit.visit_id = inspection_record.visit_id`（1:N）
- `visit.visit_id = operation_record.visit_id`（1:N）
- `operation_record.dcid = operation_operation.dcid`（1:N）
- `operation_operation.operation_code = qmjk_dict.dict_code` + `dict_type` 过滤（N:1）
- `advice_cost` / `encounter_medical_cost` 的 join 以 DDL/29 条 gold SQL 为准，不猜。

### 3.4 schema 示例

```json
{
  "name": "examination_exam",
  "description": "检查记录-检查项目，一行一个检查项目",
  "grain": "一行一个检查项目",
  "primary_key": ["docid"],
  "relationships": [
    {
      "to_table": "examination_record",
      "on": "examination_record.dcid = examination_exam.dcid",
      "type": "one_to_many",
      "note": "一个检查记录对应多个检查项目",
      "source": "data_dictionary",
      "confidence": "high"
    }
  ],
  "columns": [
    {
      "name": "docid",
      "type": "VARCHAR(100)",
      "nullable": false,
      "description": "子记录号"
    },
    {
      "name": "dcid",
      "type": "VARCHAR(100)",
      "nullable": false,
      "description": "记录号，关联主表 DCID"
    },
    {
      "name": "examination_item_code",
      "type": "VARCHAR(50)",
      "nullable": false,
      "description": "检查项目代码"
    }
  ]
}
```

> 实际字段以 docx 的 47/69/43 行为准，示例只展示结构。

## 4. 第二步：gold_sql.json 补到 35

先做 gold。

### 4.1 统一 ID

当前 29 条 gold 的 ID 是 `gold_m_xxxxxx`，不稳定。统一为：

- SQL 指标：`gold_<metric_id>`，例如 `gold_outpatient_visits`
- 公式指标：`gold_formula_<metric_id>`，例如 `gold_formula_outpatient_avg_cost`

`metric_id` 用稳定 ID，和 `metrics.json` 一一对应。

### 4.2 补 6 条公式

| gold id | 指标 | 公式 |
|---|---|---|
| `gold_formula_outpatient_insured_ratio` | 门急诊医保患者比例 | 医保患者人次/(门诊人次+急诊人次)×100% |
| `gold_formula_outpatient_avg_cost` | 门诊均次费用 | 门急诊总收入/(门诊人次+急诊人次) |
| `gold_formula_patient_avg_drug_cost` | 患者次均药费 | 门诊药品收入/(门诊人次+急诊人次) |
| `gold_formula_outpatient_drug_ratio` | 门诊药占比 | 门诊药品收入/门急诊总收入×100% |
| `gold_formula_inpatient_insured_ratio` | 医保患者比例（住院） | 住院医保人次/住院人次×100% |
| `gold_formula_inpatient_avg_cost` | 住院次均费用 | 住院总费用/住院人次 |

### 4.3 每条 SQL 补 `tables`

当前 29 条 `tables` 全为空。从 gold SQL 的 `FROM/JOIN` 提取，示例：

```json
{
  "id": "gold_outpatient_visits",
  "metric_id": "outpatient_visits",
  "metric_name": "门诊人次",
  "kind": "sql",
  "sql": "select ...",
  "tables": ["visit"],
  "notes": "原始 Oracle SQL"
}
```

### 4.4 公式条目示例

```json
{
  "id": "gold_formula_outpatient_avg_cost",
  "metric_id": "outpatient_avg_cost",
  "metric_name": "门诊均次费用",
  "kind": "formula",
  "formula": "门急诊总收入 / (门诊人次 + 急诊人次)",
  "tables": ["visit", "medical_expense_rec"],
  "notes": "公式指标，无可执行 SQL"
}
```

## 5. 第三步：metrics.json 补到 35

### 5.1 现有 5 条怎么处理

- 保留 4 个 ID：
  - `outpatient_visits`
  - `emergency_visits`
  - `hospitalization_visits`
  - `male_patients`
- 删除 `medical_insurance_ratio`，拆成：
  - `outpatient_insured_ratio`
  - `inpatient_insured_ratio`
- 新增 29 条，最终 35 条。

### 5.2 35 条 ID 与分类

**门急诊（16）**

`outpatient_visits`、`emergency_visits`、`insured_visits`、`male_patients`、`female_visits`、`examination_visits`、`inspection_visits`、`outpatient_total_revenue`、`outpatient_drug_revenue`、`outpatient_prescription_count`、`examination_ranking`、`outpatient_cost_ranking`、`outpatient_insured_ratio`、`outpatient_avg_cost`、`patient_avg_drug_cost`、`outpatient_drug_ratio`

**住院（12）**

`hospitalization_visits`、`inpatient_insured_visits`、`inpatient_examination_visits`、`inpatient_inspection_visits`、`inpatient_total_cost`、`inpatient_drug_cost`、`inpatient_insured_cost`、`inpatient_insured_drug_cost`、`inpatient_diagnosis_ranking`、`cost_composition`、`inpatient_insured_ratio`、`inpatient_avg_cost`

**手术（7）**

`total_surgeries`、`elective_surgeries`、`level1_surgeries`、`level2_surgeries`、`level3_surgeries`、`level4_surgeries`、`surgery_ranking`

### 5.3 每条字段

```json
{
  "id": "outpatient_total_revenue",
  "name": "门急诊总收入",
  "category": "门急诊情况",
  "aliases": ["门急诊收入", "总收入", "门诊总收入"],
  "definition": "统计周期内门急诊结算的总费用",
  "formula": "SUM(IFNULL(medical_expense_rec.outpatient_cost, 0))",
  "gold_sql_ref": "gold_outpatient_total_revenue",
  "related_tables": ["medical_expense_rec"],
  "time_field": "medical_expense_rec.settle_date_time",
  "default_interpretation": "按结算日期、自然月",
  "ambiguity_notes": ["门急诊 vs 住院", "按结算日期归属", "是否含医保/自费"],
  "unit": "元"
}
```

### 5.4 关键口径

- 住院范围：`activity_type_code IN ('5','6','9')`
- 门诊/急诊：`activity_type_code IN ('1','2')`
- 医保：`medical_cost_pay_type_code IN ('01','02','03','09')`
- 男性/女性：`patient.sex_code='1'/'2'`
- 时间字段：
  - `visit.treatment_occurrence_time`
  - `medical_expense_rec.settle_date_time`
  - `encounter_medical_cost.cost_date`
  - `examination_record.execute_date`
  - `inspection_record.inspection_date`
  - `prescription_record.prescription_date`
  - `diagnose_record.diag_n_osis_date`
  - `operation_operation.operation_start_date_time`

### 5.5 35 条映射表

| id | 指标 | 类型 | 相关表 | 时间字段 | 关键口径 | 单位 |
|---|---|---|---|---|---|---|
| `outpatient_visits` | 门诊人次 | SQL | visit | treatment_occurrence_time | activity_type_code='1' | 人次 |
| `emergency_visits` | 急诊人次 | SQL | visit | treatment_occurrence_time | activity_type_code='2' | 人次 |
| `insured_visits` | 医保患者人次 | SQL | visit, medical_expense_rec | treatment_occurrence_time | activity_type_code IN ('1','2')；医保代码 | 人次 |
| `male_patients` | 男性患者人次 | SQL | visit, patient | treatment_occurrence_time | sex_code='1' | 人次 |
| `female_visits` | 女性患者人次 | SQL | visit, patient | treatment_occurrence_time | sex_code='2' | 人次 |
| `examination_visits` | 检查人次 | SQL | visit, examination_record | execute_date | 检查记录去重 | 人次 |
| `inspection_visits` | 检验人次 | SQL | visit, inspection_record | inspection_date | 检验记录去重 | 人次 |
| `outpatient_total_revenue` | 门急诊总收入 | SQL | medical_expense_rec | settle_date_time | SUM(outpatient_cost) | 元 |
| `outpatient_drug_revenue` | 门诊药品收入 | SQL | encounter_medical_cost | cost_date | item_type IN ('1','2','3','11') | 元 |
| `outpatient_prescription_count` | 门诊药品处方数 | SQL | prescription_record | prescription_date | 处方计数 | 张 |
| `examination_ranking` | 检查项目排名 | SQL | examination_exam, examination_record, visit | execute_date | 按检查项目计数排名 | 排名 |
| `outpatient_cost_ranking` | 门诊费用排名 | SQL | encounter_medical_cost, qmjk_dict | charging_date | 按费用项目求和排名 | 排名 |
| `outpatient_insured_ratio` | 门急诊医保患者比例 | 公式 | visit, medical_expense_rec | treatment_occurrence_time | 医保人次/(门诊+急诊) | % |
| `outpatient_avg_cost` | 门诊均次费用 | 公式 | visit, medical_expense_rec | treatment_occurrence_time / settle_date_time | 总收入/(门诊+急诊) | 元/人次 |
| `patient_avg_drug_cost` | 患者次均药费 | 公式 | visit, encounter_medical_cost | treatment_occurrence_time / cost_date | 药品收入/(门诊+急诊) | 元/人次 |
| `outpatient_drug_ratio` | 门诊药占比 | 公式 | encounter_medical_cost | cost_date | 药品收入/总收入 | % |
| `hospitalization_visits` | 住院人次 | SQL | visit | treatment_occurrence_time | activity_type_code IN ('5','6','9') | 人次 |
| `inpatient_insured_visits` | 住院医保人次 | SQL | visit, medical_expense_rec | treatment_occurrence_time | 住院 + 医保代码 | 人次 |
| `inpatient_examination_visits` | 住院检查人次 | SQL | visit, examination_record | execute_date | 住院范围 | 人次 |
| `inpatient_inspection_visits` | 住院检验人次 | SQL | visit, inspection_record | inspection_date | 住院范围 | 人次 |
| `inpatient_total_cost` | 住院总费用 | SQL | medical_expense_rec | settle_date_time | SUM(hospitalization_total_cost) | 元 |
| `inpatient_drug_cost` | 住院药品费用 | SQL | advice_cost | cost_date | 药品 item_type | 元 |
| `inpatient_insured_cost` | 住院医保患者费用 | SQL | medical_expense_rec | settle_date_time | 医保 + 费用 | 元 |
| `inpatient_insured_drug_cost` | 住院医保患者药品费用 | SQL | advice_cost | cost_date | 医保 + 药品 | 元 |
| `inpatient_diagnosis_ranking` | 住院诊断排名 | SQL | diagnose_record, qmjk_dict, visit | diag_n_osis_date | 按诊断计数排名 | 排名 |
| `cost_composition` | 费用构成 | SQL | advice_cost, qmjk_dict | cost_date | 按 item_type 分组 | 元/% |
| `inpatient_insured_ratio` | 医保患者比例（住院） | 公式 | visit, medical_expense_rec | treatment_occurrence_time | 住院医保/住院人次 | % |
| `inpatient_avg_cost` | 住院次均费用 | 公式 | visit, medical_expense_rec | settle_date_time | 住院总费用/住院人次 | 元/人次 |
| `total_surgeries` | 总手术例数 | SQL | operation_record, operation_operation | operation_start_date_time | operation_level_code IN ('1','2','3','4') | 例 |
| `elective_surgeries` | 择期手术例数 | SQL | operation_record, operation_operation | operation_start_date_time | operation_period_type_code='1' | 例 |
| `level1_surgeries` | 一级手术例数 | SQL | operation_record, operation_operation | operation_start_date_time | operation_level_code='1' | 例 |
| `level2_surgeries` | 二级手术例数 | SQL | operation_record, operation_operation | operation_start_date_time | operation_level_code='2' | 例 |
| `level3_surgeries` | 三级手术例数 | SQL | operation_record, operation_operation | operation_start_date_time | operation_level_code='3' | 例 |
| `level4_surgeries` | 四级手术例数 | SQL | operation_record, operation_operation | operation_start_date_time | operation_level_code='4' | 例 |
| `surgery_ranking` | 手术排名 | SQL | operation_record, operation_operation, qmjk_dict | operation_start_date_time | 按 operation_code 计数排名 | 排名 |

## 6. 第四步：glossary.json 从 schema 清洗

不凭空编。主要来源是完善后的 `schema.json`，指标术语来自 `metrics.json`，费用映射来自 Excel 备注。

### 6.1 抽取规则

| 来源 | 生成什么 | 示例 |
|---|---|---|
| `columns[].enum_values` | 每个 label 一条术语 | 门诊 → `visit.activity_type_code='1'` |
| `xxx_code` / `xxx_name` 成对字段 | 用中文名称做术语，指向 code 列 | 性别 → `patient.sex_code` |
| 时间列（description 含日期/时间） | 时间术语 | 就诊时间 → `visit.treatment_occurrence_time` |
| `table.description` | 表级术语 | 就诊记录 → `visit` |
| `metrics.json` 的 `name/aliases` | 指标术语 | 门诊量 → `outpatient_visits` |
| Excel 备注 | 费用类型映射 | 药品收入 → `item_type IN ('1','2','3','11')` |
| docx 的 `字典` 列 | 记录字典代码 | `CC04_30_017` |

### 6.2 结构

```json
{
  "term": "门诊",
  "aliases": ["门诊患者", "门诊量"],
  "target_type": "enum",
  "match": "enum",
  "target": {"table": "visit", "column": "activity_type_code", "value": "1"},
  "source": "schema.enum",
  "confidence": "high"
}
```

```json
{
  "term": "就诊时间",
  "aliases": ["发生时间"],
  "target_type": "time_range",
  "match": "time_range",
  "target": {"table": "visit", "column": "treatment_occurrence_time"},
  "source": "schema.time",
  "confidence": "high"
}
```

### 6.3 不捏造

- 科室具体名称：schema 只有 `dept_name` 字段，没有科室字典 → 标 `needs_value_dictionary`。
- 诊断名称、药品名称、检查项目名称：只有 code/name，没有值列表 → 不编具体值。
- 职工医保/居民医保细分：只有代码，具体含义需数据字典确认。
- 每条术语必须有 `source`；没有来源的不进 glossary。

目标：≥30 条。

## 7. 第五步：ambiguities.json 补到 8 条

| code | 问什么 | 默认 |
|---|---|---|
| `time_range` | 自然月 vs 最近30天 | 自然月 |
| `time_field_scope` | 就诊/入院/出院/结算/费用/处方/诊断时间 | 按指标 |
| `visit_type_scope` | 门诊是否含急诊；住院是否含 5/6/9 | 仅门诊 |
| `metric_scope` | 人次/人数/例数；收入/次均/药品 | 人次/次均 |
| `patient_scope` | 出院/在院/全部 | 全部 |
| `department_entity` | 科室名称精确 vs 包含 | 包含 |
| `aggregation` | 总量/平均/去重/排名 | 按指标 |
| `insurance_scope` | 全部医保/职工/居民 | 全部医保 |

每条字段：

```json
{
  "code": "time_field_scope",
  "category": "time",
  "trigger_terms": ["住院人次", "住院费用", "出院"],
  "question": "住院指标按入院日期、出院日期还是结算日期统计？",
  "options": ["入院日期", "出院日期", "结算日期"],
  "default": "出院日期",
  "blocking": true,
  "priority": 20,
  "related_columns": [
    "visit.admission_date_time",
    "visit.discharge_date_time",
    "medical_expense_rec.settle_date_time"
  ],
  "related_metrics": ["hospitalization_visits", "inpatient_total_cost"]
}
```

现有 3 条要补 `category`；`fee_type` 改成更准确的 `cost_scope` 或合并进 `metric_scope`。

## 8. 第六步：sql_examples_sqlite.json 补到 10–15 条

- 覆盖门急诊/住院/手术；
- 至少包含：
  - 门诊药品处方数
  - 检查项目排名
  - 住院诊断排名
- 单条 ≤20 行；
- SQLite 方言：`IFNULL`、`strftime`、`||`、`LIMIT`；
- 禁止 `NVL/TO_CHAR/ROWNUM`；
- 每条字段：`id`、`source_gold_id`、`question`、`sql`、`tables`、`pattern`、`notes`；
- 在临时 SQLite 库（13 张表）上执行通过；
- 不输出 PII 列。

示例：

```json
{
  "id": "ex_sqlite_outpatient_visits",
  "source_gold_id": "gold_outpatient_visits",
  "question": "统计2025年1月门诊人次",
  "sql": "SELECT COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time) AS cnt FROM visit WHERE activity_type_code = '1' AND modify_flag IN ('1','2') AND treatment_occurrence_time >= '2025-01-01' AND treatment_occurrence_time < '2025-02-01'",
  "tables": ["visit"],
  "pattern": "filter+count_distinct",
  "notes": "日期列为 TEXT；人次去重"
}
```

## 9. 验收

只看 `data/`：

- [ ] `schema.json` 13 表；3 张新表字段完整；关系覆盖 29 条 gold SQL 的 JOIN。
- [ ] `gold_sql.json` 35 条 = 29 SQL + 6 公式；每条有 `tables`；`metric_id` 稳定。
- [ ] `metrics.json` 35 条；29 个有 `gold_sql_ref`；6 个有 `formula`；每条有 `time_field`。
- [ ] `glossary.json` ≥30；每条有 `source`；没有来源的术语不进；target 都存在。
- [ ] `ambiguities.json` ≥6（建议 8）；每条有 `category/blocking/priority`。
- [ ] `sql_examples_sqlite.json` 10–15 条；全部在 SQLite 执行通过。
- [ ] 暂不生成 `gold_sql_sqlite.json` / `sql_examples.json`。
- [ ] 不改 `app/knowledge/`。

## 10. 5 天安排

| 天 | 任务 | 产出 |
|---|---|---|
| D1 | 解析 `补充.docx`，补 3 张表 + 13 表关系图谱 | `schema.json` 13 表 |
| D2 | 29 条 SQL 统一 ID + 6 条公式 + 每条 `tables` | `gold_sql.json` 35 条 |
| D3 | 35 个 metrics，填 `gold_sql_ref`/`formula`/`time_field` | `metrics.json` 35 条 |
| D4 | 从 schema 清洗 glossary ≥30；补 ambiguities 8 条 | `glossary.json`、`ambiguities.json` |
| D5 | 10–15 条 SQLite 示例 + 临时库执行 + 自检 | `sql_examples_sqlite.json` |
