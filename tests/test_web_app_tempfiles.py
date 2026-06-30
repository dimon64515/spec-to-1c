from pathlib import Path
from unittest.mock import patch

from api import load_tables_from_pdf, extract_equipment_from_bytes


def test_load_tables_from_pdf_uses_temp_file_and_cleans_up():
    fake_bytes = b"%PDF-1.4 fake"
    captured_paths = []

    def fake_extract_tables_from_pdf(pdf_path, pages=None):
        captured_paths.append(Path(pdf_path))
        return {0: []}

    with patch("api.extract_tables_from_pdf", side_effect=fake_extract_tables_from_pdf):
        with patch("api.extract_text_lines_from_pdf", return_value={0: []}):
            load_tables_from_pdf(fake_bytes)

    assert len(captured_paths) == 1
    tmp_path = captured_paths[0]
    assert tmp_path.name.endswith(".pdf")
    assert not tmp_path.exists()
    assert tmp_path.parent != Path.cwd()


def test_extract_equipment_from_bytes_uses_temp_file_and_cleans_up():
    fake_bytes = b"%PDF-1.4 fake"
    captured_paths = []

    def fake_extract_equipment_from_pdf(pdf_path, pages=None):
        captured_paths.append(Path(pdf_path))
        return []

    with patch("api.extract_equipment_from_pdf", side_effect=fake_extract_equipment_from_pdf):
        extract_equipment_from_bytes(fake_bytes)

    assert len(captured_paths) == 1
    tmp_path = captured_paths[0]
    assert tmp_path.name.endswith(".pdf")
    assert not tmp_path.exists()
    assert tmp_path.parent != Path.cwd()
