"""Checks for the explicitly synthetic acceptance fixture generator."""
import csv
import io
import json

import pytest

from scripts import generate_acceptance_fixtures as generator
from scripts.benchmark_acceptance import load_manifest, run_benchmark


def test_generator_builds_separate_6_10_8_6_synthetic_groups(tmp_path):
    generator.generate(tmp_path)
    examples = load_manifest(tmp_path / "manifest.json")
    assert len(examples) == 30
    assert all(example.source == "synthetic" for example in examples)
    assert sum(example.challenging for example in examples) == 6
    assert [sum(example.kind == kind and not example.challenging for example in examples)
            for kind in ("text", "code", "table")] == [6, 10, 8]
    report = run_benchmark(examples)
    assert report["real_examples"] == 0 and report["real_corpus_complete"] == 0
    assert report["synthetic_examples"] == 30


def test_generator_uses_csv_literals_and_preserves_unicode_tabs_and_newlines(tmp_path):
    generator.generate(tmp_path)
    examples = load_manifest(tmp_path / "manifest.json")
    tables = [list(csv.reader(io.StringIO(example.expected, newline=""), strict=True))
              for example in examples if example.kind == "table"]
    values = [value for table in tables for row in table for value in row]
    assert "00123" in values
    assert "1,25" in values
    assert '=SUM(A1:A3)' in values
    assert 'He said "hello"' in values
    assert "first line\nsecond line" in values
    assert any("\t" in example.expected for example in examples)
    assert any("😀" in example.expected for example in examples if example.challenging)
    assert any("Привет" in example.expected for example in examples)
    assert all(example.reference.read_bytes().decode("utf-8") == example.expected for example in examples)


def test_generator_refuses_existing_targets_without_explicit_overwrite(tmp_path):
    generator.generate(tmp_path)
    reference = tmp_path / "text-01.txt"
    reference.write_text("keep local edits", encoding="utf-8")
    with pytest.raises(FileExistsError):
        generator.generate(tmp_path)
    assert reference.read_text(encoding="utf-8") == "keep local edits"


def test_generator_reports_actual_font_and_dimensions_in_separate_metadata(tmp_path):
    from PySide6.QtGui import QImage
    generator.generate(tmp_path)
    metadata = json.loads((tmp_path / "generation.json").read_text(encoding="utf-8"))
    assert metadata["synthetic_examples"] == 30
    assert metadata["font_family"]
    assert metadata["fixed_pitch"] is True
    for row in metadata["images"]:
        image = QImage(str(tmp_path / (row["id"] + ".png")))
        assert not image.isNull()
        assert [row["width"], row["height"]] == [image.width(), image.height()]
        assert row["font_pixels"] > 0
        assert row["dots_per_meter_x"] == image.dotsPerMeterX()
        assert row["dots_per_meter_y"] == image.dotsPerMeterY()
        assert row["device_pixel_ratio"] == image.devicePixelRatio()
