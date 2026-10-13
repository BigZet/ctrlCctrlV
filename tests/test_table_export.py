import csv
import io
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest

from ctrlcctrlv.table_export import export_table

ROWS = [["Товар", "Код", "Цена", "Примечание"],
        ["Папка, картонная", "00123", "89,00", 'Кавычки "здесь"\nстрока 2'],
        ["Ручка «Синяя»", "=1+2", "", "12345678901234567890"]]


def source():
    stream = io.StringIO(newline="")
    csv.writer(stream, lineterminator="\n").writerows(ROWS)
    return stream.getvalue()


def test_csv_export_preserves_cells_and_unicode(tmp_path):
    target = tmp_path / "Таблица.csv"
    export_table(target, source(), "csv")
    assert target.read_bytes().startswith(b"\xef\xbb\xbf")
    with target.open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.reader(stream)) == ROWS


def test_xlsx_export_preserves_literal_strings_and_never_emits_formulas(tmp_path):
    target = tmp_path / "Таблица.xlsx"
    export_table(target, source(), "xlsx")
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(target) as package:
        strings = ET.fromstring(package.read("xl/sharedStrings.xml"))
        values = ["".join(item.itertext()) for item in strings]
        sheet = ET.fromstring(package.read("xl/worksheets/sheet1.xml"))
        assert not sheet.findall(".//s:f", ns)
        actual = [[values[int(cell.find("s:v", ns).text)] for cell in row]
                  for row in sheet.find("s:sheetData", ns)]
        assert actual == ROWS
        assert all(cell.get("t") == "s" for cell in sheet.findall(".//s:c", ns))


@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_export_failure_keeps_existing_file_and_cleans_temporary_file(tmp_path, monkeypatch, kind):
    import ctrlcctrlv.table_export as module
    target = tmp_path / ("existing." + kind)
    target.write_bytes(b"existing data")
    def fail(*args):
        raise PermissionError("locked")
    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(PermissionError):
        export_table(target, source(), kind)
    assert target.read_bytes() == b"existing data"
    assert list(tmp_path.iterdir()) == [target]


def test_xlsx_limits_fail_without_truncating_or_overwriting(tmp_path):
    target = tmp_path / "existing.xlsx"
    target.write_bytes(b"existing data")
    with pytest.raises(ValueError):
        export_table(target, "x" * 32768 + ",value\n", "xlsx")
    assert target.read_bytes() == b"existing data"
