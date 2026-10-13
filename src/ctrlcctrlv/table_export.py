"""Cross-platform, atomic export of literal OCR table cells."""
import csv
import io
import os
from pathlib import Path
import tempfile

import xlsxwriter


def export_table(path, text, kind):
    path = Path(path)
    rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("Не удалось прочитать таблицу")
    if kind not in {"csv", "xlsx"}:
        raise ValueError("Неизвестный формат экспорта")
    if kind == "xlsx":
        if len(rows) > 1048576 or len(rows[0]) > 16384:
            raise ValueError("Таблица превышает размер листа Excel")
        if any(len(value) > 32767 for row in rows for value in row):
            raise ValueError("Ячейка содержит больше 32767 символов. Сохраните таблицу в CSV.")
    # Write beside the destination so replacement is atomic on the same volume.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".ctrlc-export-", suffix="." + kind, delete=False) as staged:
        temporary = Path(staged.name)
    try:
        if kind == "csv":
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                csv.writer(stream, lineterminator="\r\n").writerows(rows)
        else:
            with xlsxwriter.Workbook(temporary) as workbook:
                sheet = workbook.add_worksheet("Таблица")
                cell_format = workbook.add_format({"num_format": "@", "text_wrap": True, "valign": "top"})
                for r, row in enumerate(rows):
                    for c, value in enumerate(row):
                        # Preserve leading zeroes, decimal punctuation and formula-like OCR literally.
                        if sheet.write_string(r, c, value, cell_format) != 0:
                            raise ValueError("Не удалось сохранить ячейку без потери данных")
                for c in range(len(rows[0])):
                    width = max(max((len(line) for line in row[c].splitlines()), default=0) for row in rows)
                    sheet.set_column(c, c, min(60, max(12, width + 2)))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
