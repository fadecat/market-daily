import json
from pathlib import Path

from src.research.style_seesaw_930955_vs_399326 import main


def test_main_writes_analysis_first_json(tmp_path):
    archive_root = Path(__file__).resolve().parents[1] / "data" / "archive"
    output = tmp_path / "style.json"

    code = main(
        [
            "--archive-root",
            str(archive_root),
            "--output",
            str(output),
            "--as-of-date",
            "2026-08-07",
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["meta"]["pair_id"] == "930955_vs_399326"
    assert payload["meta"]["as_of_date"] == "2026-08-07"
