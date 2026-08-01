"""Progress counting and the job_progress-shaped CSV."""

from __future__ import annotations

import csv

from assystant.status import (
    CSV_FIELDS,
    MINUTES_PER_JOB,
    append_csv,
    campaign_status,
    category_for,
    system_status,
)
from assystant.submit import RESULT_FILENAME


def test_category_names_follow_component_count():
    assert category_for(["Fe"]) == "Unary"
    assert category_for(["Fe", "C"]) == "Binary"
    assert category_for(["Fe", "B", "C"]) == "Ternary"
    assert category_for(["Fe", "B", "C", "N", "O", "H"]) == "6-component"


def test_counts_finished_against_manifest_total(generated_campaign):
    cfg, manifest = generated_campaign
    sys_dir = cfg.root_path / "Fe"

    # Both structures started, one finished.
    for dirname in manifest["dirname"]:
        (sys_dir / dirname).mkdir(parents=True, exist_ok=True)
    (sys_dir / manifest.iloc[0]["dirname"] / RESULT_FILENAME).write_text("done")

    row = system_status(cfg.root, ["Fe"])
    assert row["total_structures"] == 2
    assert row["started"] == 2
    assert row["job_count"] == 1
    assert row["pct"] == 50.0
    assert row["minutes_completed"] == 15  # one finished job x 15 minutes
    assert row["category"] == "Unary"
    assert row["run_dir"] == "Fe"


def test_non_structure_directories_are_not_counted(generated_campaign):
    cfg, manifest = generated_campaign
    sys_dir = cfg.root_path / "Fe"
    (sys_dir / manifest.iloc[0]["dirname"]).mkdir(parents=True, exist_ok=True)
    (sys_dir / "scratch").mkdir(exist_ok=True)  # `structures/` already exists too

    assert system_status(cfg.root, ["Fe"])["started"] == 1


def test_legacy_directories_are_counted(cfg):
    legacy = cfg.root_path / "Fe" / "struct_pyxtal_4"
    legacy.mkdir(parents=True)
    (legacy / RESULT_FILENAME).write_text("done")

    row = system_status(cfg.root, ["Fe"])
    assert row["started"] == 1
    assert row["job_count"] == 1
    # No manifest -> the directory count is the fallback total.
    assert row["total_structures"] == 1
    assert row["pct"] == 100.0


def test_missing_system_reports_zeroes(cfg):
    row = system_status(cfg.root, ["Ni"])
    assert row == {
        "category": "Unary", "run_dir": "Ni", "job_count": 0, "started": 0,
        "total_structures": 0, "pct": 0.0, "minutes_completed": 0,
    }


def test_csv_has_the_job_progress_shape(generated_campaign, tmp_path):
    cfg, manifest = generated_campaign
    (cfg.root_path / "Fe" / manifest.iloc[0]["dirname"]).mkdir(parents=True, exist_ok=True)
    (cfg.root_path / "Fe" / manifest.iloc[0]["dirname"] / RESULT_FILENAME).write_text("done")

    path = tmp_path / "job_progress.csv"
    append_csv(campaign_status(cfg), path)
    append_csv(campaign_status(cfg), path)

    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == CSV_FIELDS
    assert len(rows) == 2, "append must not rewrite the header"
    assert rows[0]["category"] == "Unary"
    assert rows[0]["run_dir"] == "Fe"
    assert rows[0]["job_count"] == "1"
    assert rows[0]["total_structures"] == "2"
    # Literal 15, not str(MINUTES_PER_JOB): asserting against the constant the
    # code uses would make this pass for any value of it.
    assert MINUTES_PER_JOB == 15
    assert rows[0]["minutes_completed"] == "15"


def test_format_table_totals(generated_campaign):
    from assystant.status import format_table

    cfg, manifest = generated_campaign
    (cfg.root_path / "Fe" / manifest.iloc[0]["dirname"]).mkdir(parents=True, exist_ok=True)
    (cfg.root_path / "Fe" / manifest.iloc[0]["dirname"] / RESULT_FILENAME).write_text("done")
    table = format_table(campaign_status(cfg))
    assert "Fe" in table and "TOTAL" in table and "50.00%" in table
