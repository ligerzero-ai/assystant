"""Per-system campaign progress, and an optional CSV log.

Ported from ``2025_04_03_CheckPyxtalJobStatus.ipynb``: count structure
directories against those containing ``df_ASSYST_jobs.pkl``.  The CSV has the
same shape as ``/u/hmai/2024_12_05_DFTPyxtalAssystNodes/job_progress.csv``
(``timestamp,category,run_dir,job_count,total_structures,pct,minutes_completed``)
so an existing log can be appended to.
"""

from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path

from .config import CampaignConfig, system_name
from .generate import parse_struct_dir, read_manifest
from .submit import RESULT_FILENAME

__all__ = [
    "CSV_FIELDS",
    "MINUTES_PER_JOB",
    "category_for",
    "system_status",
    "campaign_status",
    "format_table",
    "append_csv",
    "watch",
]

CSV_FIELDS = [
    "timestamp",
    "category",
    "run_dir",
    "job_count",
    "total_structures",
    "pct",
    "minutes_completed",
]

#: Nominal wall-clock minutes charged per completed structure. Matches the
#: constant baked into the existing job_progress.csv (job_count * 15).
MINUTES_PER_JOB = 15

_CATEGORIES = {1: "Unary", 2: "Binary", 3: "Ternary", 4: "Quaternary", 5: "Quinary"}


def category_for(species) -> str:
    return _CATEGORIES.get(len(species), f"{len(species)}-component")


def system_status(root, species) -> dict:
    """Progress for one system: directories present vs. directories finished."""
    name = system_name(list(species))
    sys_dir = Path(root) / name
    dirs = []
    if sys_dir.is_dir():
        dirs = [
            entry
            for entry in sys_dir.iterdir()
            if entry.is_dir() and parse_struct_dir(entry.name) is not None
        ]
    done = sum(1 for entry in dirs if (entry / RESULT_FILENAME).is_file())

    manifest = read_manifest(root, species)
    # The manifest is the authority on how many structures the campaign has;
    # fall back to the directory count for legacy campaigns without one.
    total = len(manifest) if manifest is not None else len(dirs)
    pct = 100.0 * done / total if total else 0.0
    return {
        "category": category_for(list(species)),
        "run_dir": name,
        "job_count": done,
        "started": len(dirs),
        "total_structures": total,
        "pct": pct,
        "minutes_completed": done * MINUTES_PER_JOB,
    }


def campaign_status(cfg: CampaignConfig) -> list[dict]:
    return [system_status(cfg.root, species) for species in cfg.system.systems]


def format_table(rows) -> str:
    header = f"{'system':<16}{'category':<12}{'done':>8}{'started':>9}{'total':>8}{'pct':>8}"
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row['run_dir']:<16}{row['category']:<12}{row['job_count']:>8}"
            f"{row['started']:>9}{row['total_structures']:>8}{row['pct']:>7.2f}%"
        )
    total_done = sum(r["job_count"] for r in rows)
    total_all = sum(r["total_structures"] for r in rows)
    pct = 100.0 * total_done / total_all if total_all else 0.0
    lines.append("-" * len(header))
    lines.append(f"{'TOTAL':<16}{'':<12}{total_done:>8}{'':>9}{total_all:>8}{pct:>7.2f}%")
    return "\n".join(lines)


def append_csv(rows, path) -> Path:
    """Append one timestamped block of rows to a job_progress-shaped CSV."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.is_file() or path.stat().st_size == 0
    stamp = datetime.now().isoformat(sep=" ")
    with path.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if new_file:
            writer.writeheader()
        for row in rows:
            writer.writerow({"timestamp": stamp, **{k: row[k] for k in CSV_FIELDS[1:]}})
    return path


def watch(cfg: CampaignConfig, interval: int = 60, csv_path=None) -> None:  # pragma: no cover
    """Print the progress table every ``interval`` seconds until interrupted."""
    while True:
        rows = campaign_status(cfg)
        print(f"\n{datetime.now().isoformat(sep=' ', timespec='seconds')}")
        print(format_table(rows))
        if csv_path:
            append_csv(rows, csv_path)
        time.sleep(interval)
