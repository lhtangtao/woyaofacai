from decimal import Decimal
from pathlib import Path

import pytest

from portfolio.db.schema_validator import expected_schema
from portfolio.domain.models import from_e8
from portfolio.excel_reference import extract_reference, write_snapshot

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def snapshot():
    return extract_reference(ROOT / "references/个人投资总览.xlsx")


def test_schema_inventory():
    schema = expected_schema()
    assert sum(obj["type"] == "table" for obj in schema.values()) == 14
    assert sum(obj["type"] == "view" for obj in schema.values()) == 7
    assert (ROOT / "references/sqlite_schema.sql").read_bytes() == (ROOT / "src/portfolio/resources/schema.sql").read_bytes()


def test_workbook_inventory(snapshot):
    assert len(snapshot["sheets"]) == 13
    assert len(snapshot["charts"]) == 27
    assert snapshot["recalculated"] is False
    for sheet in snapshot["sheets"]:
        assert sheet["cell_count"] == len(sheet["cells"])
    assert snapshot["charts"][23]["title"] == "全时期盈亏（USD）"
    assert snapshot["charts"][23]["series"][0]["references"][-2:] == ["'图表趋势数据'!$AB$2:$AB$9", "'图表趋势数据'!$AC$2:$AC$9"]


@pytest.mark.parametrize("sheet,count", [("当前持仓", 14), ("股票汇总", 18), ("每日持仓收益", 23),
                                       ("每月微操收益", 14), ("每月持仓收益", 24),
                                       ("月度净投入明细", 10), ("清仓收益", 13), ("交易明细", 16)])
def test_workbook_headers(snapshot, sheet, count):
    found = next(s for s in snapshot["sheets"] if s["name"] == sheet)
    assert len(found["headers"]) == count


def test_golden_arithmetic_is_not_a_database_reconciliation(snapshot):
    cells = next(s for s in snapshot["sheets"] if s["name"] == "当前持仓")["cells"]
    assert cells["C2"]["value"] == "TQQQ"
    def number(key):
        return Decimal(cells[key]["value"])
    assert number("D2") * number("E2") == Decimal("16132.20")
    assert number("H2") - number("I2") == Decimal("9809.23")
    assert number("F2") - number("J2") == Decimal("6322.97")


def test_snapshot_refuses_to_overwrite(tmp_path):
    source = ROOT / "references/个人投资总览.xlsx"
    target = tmp_path / "existing.json"
    target.write_text("keep")
    with pytest.raises(FileExistsError):
        write_snapshot(source, target)
    assert target.read_text() == "keep"


@pytest.mark.parametrize("raw,expected", [(123456789, "1.23456789"), (1, "0.00000001"),
                                         (-52000000000, "-520"), (9223372036854775807, "92233720368.54775807")])
def test_exact_e8(raw, expected):
    assert from_e8(raw) == Decimal(expected)


def test_missing_and_floats_not_silently_coerced():
    assert from_e8(None) is None
    with pytest.raises(ValueError):
        from_e8(1.0)
