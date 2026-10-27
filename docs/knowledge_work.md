# 组员 A 开发计划（简化版）：data/ + knowledge/

## 1. 一句话目标

把真实库的 **DDL** 和 Excel 里的 **35 个指标/SQL**，整理成 `data/` 下 7 个 JSON（含 SQLite 方言示例）；再写 `app/knowledge/` 下的脚本，让 B 拿着用户问题，就能拿到：

- 可能用到哪些表、哪些字段、怎么 join；
- 用户说的词对应哪个指标/字段/枚举值；
- 问题里哪里说不清，应该问什么；
- 最相似的 SQL 示例；
- 最后拼成一段可以直接塞给大模型的上下文。

本轮只做“生成 SQL”所需的知识，不连库执行，不做测试体系。

## 1.1 组员 A 要完成的目录结构

```text
data/
├── schema.json
├── metrics.json
├── gold_sql.json
├── glossary.json
├── ambiguities.json
├── sql_examples.json
└── sql_examples_sqlite.json

app/knowledge/
├── __init__.py
├── loader.py
├── ddl_importer.py
├── schema_catalog.py
├── metric_catalog.py
├── glossary.py
├── ambiguity_catalog.py
├── example_catalog.py
├── context_builder.py
├── provider.py
└── inspect.py

docs/
└── KNOWLEDGE.md
```

A 只负责以上文件；`data/synthetic/medical.db` 由 C 生成，A 不需要维护。

## 2. data/ 放什么

| 文件 | 大白话 | 关键内容 |
|---|---|---|
| `schema.json` | 数据库长什么样 + 业务说明 | 表、字段、类型、主外键、枚举值、哪些是 PII、表之间怎么关联 |
| `metrics.json` | 每个指标到底怎么算 | 指标名、别名、定义、公式、时间字段、相关表、默认口径 |
| `gold_sql.json` | Excel 里的“标准答案” | 29 条 SQL + 6 条公式，原文保存，用来对照 |
| `glossary.json` | 用户黑话翻译表 | “门诊”对应哪个字段的哪个值，“医保”对应哪一列 |
| `ambiguities.json` | 哪些问题必须先问清楚 | 触发词、候选解释、追问话术、默认值 |
| `sql_examples.json` | 给模型看的简短范例 | 10–15 条 20 行以内的 SQL，不要放 Excel 里的长 SQL |
| `sql_examples_sqlite.json` | 给 C 用的 SQLite 方言范例 | 从上面的 SQL 重写，10–15 条，能在 SQLite 执行 |

### 2.1 `schema.json` 示例

```json
{
  "version": "1.0.0",
  "dialect": "oracle",
  "tables": [
    {
      "name": "visit",
      "description": "就诊记录表，一行一次就诊",
      "grain": "一次就诊",
      "primary_key": ["visit_id"],
      "columns": [
        {
          "name": "treatment_occurrence_time",
          "type": "DATE",
          "nullable": false,
          "description": "就诊发生时间",
          "aliases": ["就诊时间", "发生时间"],
          "time_role": "event_time",
          "is_pii": false
        },
        {
          "name": "activity_type_code",
          "type": "VARCHAR(8)",
          "nullable": false,
          "description": "业务类型代码",
          "aliases": ["就诊类型"],
          "enum_values": [
            { "value": "1", "label": "门诊" },
            { "value": "2", "label": "急诊" },
            { "value": "5", "label": "住院" }
          ],
          "is_pii": false
        },
        {
          "name": "id_card",
          "type": "VARCHAR(32)",
          "nullable": true,
          "description": "身份证号，只用于去重，不对外输出",
          "is_pii": true,
          "pii_policy": "禁止出现在 SELECT 输出"
        }
      ],
      "relationships": [
        {
          "to_table": "patient",
          "type": "many_to_one",
          "on": "visit.patient_id = patient.patient_id",
          "note": "患者维度"
        }
      ]
    }
  ]
}
```

要求：类型、主外键、枚举值从 DDL/数据字典来；`description`、`aliases`、`time_role`、`pii_policy` 由 A 补。不要猜 DDL 里没有的字段。

### 2.2 `metrics.json` 示例

```json
{
  "version": "1.0.0",
  "metrics": [
    {
      "id": "outpatient_visits",
      "name": "门诊人次",
      "category": "门急诊情况",
      "aliases": ["门诊量", "门诊就诊人次"],
      "definition": "统计周期内 activity_type_code='1' 的就诊记录去重人次",
      "formula": "COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time)",
      "gold_sql_ref": "gold_001",
      "related_tables": ["visit"],
      "time_field": "visit.treatment_occurrence_time",
      "default_interpretation": "按就诊发生日期、自然月",
      "ambiguity_notes": ["人次 vs 人数", "门诊是否包含急诊"],
      "unit": "人次"
    }
  ]
}
```

要求：35 个指标全部录入；29 条 SQL 指标填 `gold_sql_ref`；6 条公式指标填 `formula`；每个指标必须有 `time_field` 和默认口径。

### 2.3 `gold_sql.json` 示例

```json
{
  "version": "1.0.0",
  "dialect": "oracle",
  "items": [
    {
      "id": "gold_001",
      "metric_id": "outpatient_visits",
      "metric_name": "门诊人次",
      "kind": "sql",
      "sql": "select sum(indicatorValue) from ( ... Excel 原文 ... )",
      "tables": ["visit"],
      "notes": "原样保留，不直接塞给模型"
    }
  ]
}
```

### 2.4 `glossary.json` 示例

```json
{
  "version": "1.0.0",
  "terms": [
    {
      "term": "门诊",
      "aliases": ["门诊患者", "门诊量"],
      "target_type": "enum",
      "target": { "table": "visit", "column": "activity_type_code", "value": "1" }
    },
    {
      "term": "医保",
      "aliases": ["医保患者"],
      "target_type": "column",
      "target": { "table": "visit", "column": "insurance_type_code" }
    }
  ]
}
```

### 2.5 `ambiguities.json` 示例

```json
{
  "version": "1.0.0",
  "rules": [
    {
      "code": "time_range",
      "trigger_terms": ["上个月", "本月", "最近", "近"],
      "question": "您说的『{term}』是指自然月还是最近30天？",
      "options": ["自然月", "最近30天"],
      "default": "自然月",
      "blocking": true
    },
    {
      "code": "visit_type_scope",
      "trigger_terms": ["门诊人次", "门急诊"],
      "question": "门诊人次是只统计门诊，还是包含急诊？",
      "options": ["仅门诊", "门诊+急诊"],
      "default": "仅门诊",
      "blocking": true
    }
  ]
}
```

至少 6 条，覆盖：指标口径、时间口径、科室/实体、患者范围、业务类型、聚合方式。

### 2.6 `sql_examples.json` 示例

```json
{
  "version": "1.0.0",
  "schema_version": "1.0.0",
  "dialect": "oracle",
  "examples": [
    {
      "id": "ex_outpatient_count",
      "question": "统计2025年1月门诊人次",
      "sql": "SELECT COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time) AS cnt FROM visit WHERE activity_type_code = '1' AND modify_flag IN ('1','2') AND treatment_occurrence_time >= DATE '2025-01-01' AND treatment_occurrence_time < DATE '2025-02-01'",
      "tables": ["visit"],
      "pattern": "filter+count_distinct",
      "notes": "按就诊发生日期；人次去重"
    }
  ]
}
```

要求：10–15 条，单条 ≤20 行，覆盖门急诊/住院/手术；不输出 PII 列。

### 2.7 `sql_examples_sqlite.json`：把示例 SQL 重写成 SQLite 方言

这是 A 新增的任务。C 的 SQLite 自修正生成器需要 SQLite 方言的 few-shot，不能直接拿 Oracle 原文。

至少完成 10–15 条，优先覆盖 5 个核心指标（门诊人次、急诊人次、住院人次、总费用、手术例数）。

改写规则：

| Oracle 写法 | SQLite 写法 |
|---|---|
| `NVL(x, 0)` | `IFNULL(x, 0)` 或 `COALESCE(x, 0)` |
| `TO_CHAR(d, 'YYYY-MM-DD')` | `strftime('%Y-%m-%d', d)`，或日期列已是 TEXT 时直接用 |
| `TO_DATE('2025-01-01','YYYY-MM-DD')` | `DATE('2025-01-01')` |
| `DATE '2025-01-01'` | `'2025-01-01'` |
| `CONCAT(a,b,c)` | `a \|\| b \|\| c` |
| `ROWNUM <= n` / `FETCH FIRST n ROWS ONLY` | `LIMIT n` |
| `FROM DUAL` | 去掉，直接 `SELECT ...` |
| `(+)` 外连接 | `LEFT JOIN` |
| `NUMBER` / `VARCHAR2` | `REAL` / `INTEGER` / `TEXT`（按列语义） |
| 反引号标识符 | SQLite 双引号，或直接小写列名 |

要求：

- `question` 与 `sql_examples.json` 保持一致；新增 `source_oracle_id` 指回原条目。
- 每条 SQL 必须在 SQLite 上执行通过。C 的镜像 `data/synthetic/medical.db` 还没生成时，A 可以先用 `sqlite3` 建一个临时空库，只验证语法和可执行性；C 生成镜像后再做一次结果验证。
- 单条 ≤20 行；不输出 PII 列。
- 改写失败的条目单独列 `needs_manual_review`，不要硬塞进 few-shot。
- 保留 `tables`、`pattern`、`notes` 字段，方便 C 检索。

示例：

```json
{
  "version": "1.0.0",
  "dialect": "sqlite",
  "schema_version": "1.0.0",
  "examples": [
    {
      "id": "ex_sqlite_outpatient_count",
      "source_oracle_id": "ex_outpatient_count",
      "question": "统计2025年1月门诊人次",
      "sql": "SELECT COUNT(DISTINCT visit_id || id_card || treatment_occurrence_time) AS cnt FROM visit WHERE activity_type_code = '1' AND modify_flag IN ('1','2') AND treatment_occurrence_time >= '2025-01-01' AND treatment_occurrence_time < '2025-02-01'",
      "tables": ["visit"],
      "pattern": "filter+count_distinct",
      "notes": "日期列为 TEXT；人次去重"
    }
  ]
}
```

## 3. knowledge/ 下的脚本都是干嘛的

先用一句大白话说，再看调用关系。

| 脚本 | 大白话 |
|---|---|
| `loader.py` | **读数据**：把 `data/` 里 7 个 JSON 读进来，变成一个 Python 对象。别的脚本都从这里拿数据。只做最基本的检查：文件在不在、版本一致不一致、引用的表存不存在。 |
| `ddl_importer.py` | **省手抄**：把 DDL 里的 `CREATE TABLE` 自动转成 `schema.json` 的骨架（表、字段、类型、主外键），A 再补业务说明。 |
| `schema_catalog.py` | **找表找字段**：给一个问题，找出可能用到的表、字段、join 路径；比如“门诊人次”会找到 `visit` 和 `activity_type_code`。 |
| `metric_catalog.py` | **找指标**：用户说“门诊人次”，找到它的定义、公式、时间字段、相关表、默认口径。 |
| `glossary.py` | **翻译黑话**：把“门诊”“医保”“住院”翻译成库里的字段和枚举值；找不到就标低置信度。 |
| `ambiguity_catalog.py` | **找说不清的地方**：命中“上个月”“费用”“门诊人次”等触发词，就生成候选解释和追问话术。 |
| `example_catalog.py` | **找相似 SQL**：从 10–15 条示例里挑最像的 2–3 条，给生成器参考。 |
| `context_builder.py` | **拼上下文**：把上面找到的表、字段、指标、术语、歧义、示例，拼成一段给大模型看的文字。 |
| `provider.py` | **统一出口**：B 只调这里的 5 个方法，不关心底下怎么查、数据从哪来。 |
| `inspect.py` | **演示/排障**：命令行输入一个问题，打印“识别到了哪些词、哪些指标、哪些歧义、哪些表、哪些示例”。 |

调用关系：

```text
用户问题
  ↓
loader.py            读 data/ 下的 JSON
  ↓
glossary.py          翻译术语（门诊、医保、住院……）
  ↓
metric_catalog.py    找指标（门诊人次、平均住院天数……）
  ↓
schema_catalog.py    找表、字段、join 路径
  ↓
ambiguity_catalog.py 找歧义，生成追问
  ↓
example_catalog.py   找相似 SQL
  ↓
context_builder.py   拼成 prompt 上下文
  ↓
provider.py          交给 B
```

`inspect.py` 就是把上面这条链路跑一遍，打印中间结果。

### provider.py 对 B 暴露的 5 个方法

```python
provider.get_schema_context(question, intent)   # 相关表/字段/join/枚举/PII 提醒
provider.search_metrics(question, top_k=5)      # 相关指标
provider.resolve_terms(question)                # 术语翻译结果
provider.detect_ambiguities(question, terms)    # 歧义 + 追问
provider.search_sql_examples(question, top_k=3) # 相似 SQL 示例
```

B 不直接读 JSON，也不 import 具体 catalog 类。

## 4. A 的工作顺序

| 天 | 做什么 | 做完有什么 |
|---|---|---|
| D1 | 读 DDL/数据字典，写 `ddl_importer.py`，生成 `schema.json`；用 Excel SQL 检查表/字段有没有漏 | `schema.json` + 一份“缺失表/字段”清单 |
| D2 | 把 Excel 的 35 个指标转成 `metrics.json` 和 `gold_sql.json` | 35 个指标、29 条 SQL、6 条公式 |
| D3 | 写 `glossary.json`、`ambiguities.json`；实现 `loader.py`、`provider.py` 骨架 | 术语表、歧义规则、能加载数据 |
| D4 | 写 `sql_examples.json`（Oracle）并重写成 `sql_examples_sqlite.json`；实现 `schema_catalog.py`、`metric_catalog.py`、`example_catalog.py`、`context_builder.py` | 10–15 条 Oracle + SQLite 示例 |
| D5 | 在 SQLite 上跑通 `sql_examples_sqlite.json`；写 `inspect.py`、`docs/KNOWLEDGE.md`，用 3 个问题演示，交接给 B/C | 可演示、可交接 |

## 5. 做成什么样（验收）

不要求写测试，也不要求做完整校验。A 完成时，能用下面这些结果证明：

### 5.1 数据齐全

- `schema.json`：DDL 里的表、字段都在；每列有类型、中文说明、是否 PII；关键列有枚举值和关联关系。
- Excel 29 条 SQL 用到的表和字段，都能在 `schema.json` 找到；找不到的列一张清单，说明原因。
- `metrics.json`：35 个指标齐全（29 SQL + 6 公式）；每个有定义、公式、时间字段、默认口径。
- `gold_sql.json`：29 条 SQL + 6 条公式原样保存。
- `glossary.json`：至少 30 个术语/同义词。
- `ambiguities.json`：至少 6 条 blocking 规则。
- `sql_examples.json`：10–15 条，单条 ≤20 行。
- `sql_examples_sqlite.json`：10–15 条；每条能在 SQLite 上执行通过；没有 `NVL`/`TO_CHAR`/`ROWNUM` 等 Oracle 专有写法；`question` 与 Oracle 版一致。

### 5.2 能演示

运行：

```bash
python -m app.knowledge.inspect "上个月门诊人次是多少"
```

能打印出：

- 术语：上个月 → 时间；门诊人次 → 指标 `outpatient_visits`
- 歧义：`time_range`（自然月 vs 最近30天）
- 相关表：`visit`
- 指标：门诊人次
- 示例：`ex_outpatient_count`
- 拼好的上下文预览

再跑两个问题：

1. “心内科医保患者比例” → 能识别科室、医保、比例分母。
2. “今年手术排名前 10” → 能识别手术指标、时间口径、排名/截断方式。

### 5.3 B 能接上

B 写：

```python
from app.knowledge.loader import load_knowledge
from app.knowledge.provider import FileKnowledgeProvider

bundle = load_knowledge("data")
provider = FileKnowledgeProvider(bundle)
```

然后只调 5 个方法即可，不需要改 A 的代码。

### 5.4 交接文档

`docs/KNOWLEDGE.md` 写清楚：

- 7 个 JSON 每个字段什么意思；
- 怎么新增一个指标、一个术语、一条歧义规则；
- `inspect` 怎么用；
- B 怎么接入。

## 6. A 不做什么

- 不写 `graph/`、`api/`、`prompts/`、`tools/`。
- 不连数据库执行 SQL。
- 不写测试体系，不做复杂校验脚本。
- 不做 embedding、向量库、RAG。
- 不猜 DDL 里没有的表、字段、枚举；拿不到就标 `unknown`，并在歧义规则里追问。
- 不把 Excel 里的长 SQL 直接塞给模型；它们只做“标准答案”对照。
