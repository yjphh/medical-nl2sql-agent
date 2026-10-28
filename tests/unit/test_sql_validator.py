from app.tools.sql_validator import SQLValidator


def test_allows_select():
    v = SQLValidator()
    assert v.validate("SELECT * FROM patients").valid


def test_rejects_write():
    v = SQLValidator()
    result = v.validate("DELETE FROM patients WHERE id=1")
    assert not result.valid


def test_rejects_unknown_table():
    v = SQLValidator()
    result = v.validate("SELECT * FROM secret_table", allowed_tables=["patients"])
    assert not result.valid
    assert result.needs_clarification
