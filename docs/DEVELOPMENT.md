# 开发指南

## 环境

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## 启动

```bash
uvicorn app.main:app --reload
```

## 常用脚本

```bash
python import_schema.py            # 从数据库导出 schema
```

## 测试

```bash
pytest tests/unit
pytest tests/integration
```

## 代码风格

- Ruff 配置见 `pyproject.toml`
- 类型注解使用 Python 3.10+ 语法（`X | None`）
