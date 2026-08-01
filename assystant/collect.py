"""Walk a campaign root and concatenate every ``df_ASSYST_jobs.pkl`` into one dataset.

Two on-disk layouts are read:

``struct_<idx:05d>_<sha8>/``
    written by this package; the hash column of ``manifest.csv`` ties each
    result back to the structure that generated it.

``struct_pyxtal_<idx>/``
    the pre-2026 layout.  Existing campaign data is readable without migration,
    which is why ``collect`` matches on layout rather than assuming one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import CampaignConfig, system_name
from .generate import MANIFEST_DTYPES, MANIFEST_NAME, parse_struct_dir
from .submit import RESULT_FILENAME

__all__ = ["ResultDir", "iter_result_dirs", "collect_campaign", "load_manifests"]


@dataclass(frozen=True)
class ResultDir:
    path: Path
    system: str
    index: int
    structure_hash: str | None
    layout: str


def _scan_system_dir(sys_dir: Path, system: str):
    if not sys_dir.is_dir():
        return
    for entry in sorted(sys_dir.iterdir()):
        if not entry.is_dir():
            continue
        parsed = parse_struct_dir(entry.name)
        if parsed is None:
            continue
        if not (entry / RESULT_FILENAME).is_file():
            continue
        index, sha8, layout = parsed
        yield ResultDir(entry, system, index, sha8, layout)


def iter_result_dirs(root, systems=None):
    """Yield every finished structure directory under ``root``.

    Scans one level below ``root`` (``<root>/<system>/struct_*``) plus ``root``
    itself (``<root>/struct_*``, for a campaign root that *is* one system).
    Bounded on purpose: a full ``rglob`` over a campaign with tens of thousands
    of structure directories, each with calculation subdirectories, is far
    slower for no additional coverage.
    """
    root = Path(root)
    if not root.is_dir():
        return
    yield from _scan_system_dir(root, root.name)
    names = (
        [system_name(list(s)) for s in systems]
        if systems
        else sorted(e.name for e in root.iterdir() if e.is_dir())
    )
    for name in names:
        yield from _scan_system_dir(root / name, name)


def load_manifests(root, systems=None) -> pd.DataFrame:
    """Concatenate every available ``manifest.csv`` under ``root``."""
    root = Path(root)
    frames = []
    candidates = []
    if (root / MANIFEST_NAME).is_file():
        candidates.append((root.name, root / MANIFEST_NAME))
    names = (
        [system_name(list(s)) for s in systems]
        if systems
        else sorted(e.name for e in root.iterdir() if e.is_dir())
        if root.is_dir()
        else []
    )
    for name in names:
        path = root / name / MANIFEST_NAME
        if path.is_file():
            candidates.append((name, path))
    for name, path in candidates:
        frame = pd.read_csv(path, dtype=MANIFEST_DTYPES)
        if "system" not in frame.columns:
            frame["system"] = name
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["index", "system", "dirname", "hash"])
    return pd.concat(frames, ignore_index=True)


def collect_campaign(root, systems=None, output=None) -> pd.DataFrame:
    """Concatenate all results under ``root``, joined to the manifest.

    Hashed directories join on ``(system, structure_hash)``; legacy
    ``struct_pyxtal_<idx>`` directories have no hash on disk, so they join on
    ``(system, struct_index)`` when a manifest exists and simply carry no
    provenance columns when it does not.
    """
    root = Path(root)
    results = list(iter_result_dirs(root, systems))
    frames = []
    for result in results:
        try:
            frame = pd.read_pickle(result.path / RESULT_FILENAME)
        except Exception as exc:
            print(f"Warning: could not read {result.path / RESULT_FILENAME}: {exc}")
            continue
        frame = frame.copy()
        frame["system"] = result.system
        frame["struct_index"] = result.index
        frame["structure_hash"] = result.structure_hash
        frame["layout"] = result.layout
        frame["run_dir"] = str(result.path)
        frames.append(frame)

    if not frames:
        combined = pd.DataFrame(
            columns=["system", "struct_index", "structure_hash", "layout", "run_dir"]
        )
    else:
        combined = pd.concat(frames, ignore_index=True)

    manifest = load_manifests(root, systems)
    if len(manifest) and len(combined):
        combined = _join_manifest(combined, manifest)

    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.suffix in (".csv",):
            combined.to_csv(output, index=False)
        else:
            combined.to_pickle(output)
    return combined


_MANIFEST_COLUMNS = ["spacegroup", "composition", "formula", "n_atoms", "volume_per_atom"]


def _join_manifest(combined: pd.DataFrame, manifest: pd.DataFrame) -> pd.DataFrame:
    keep = [c for c in _MANIFEST_COLUMNS if c in manifest.columns]
    hashed = combined["layout"] == "hashed"

    out = combined.copy()
    for column in keep:
        out[column] = None

    if "hash" in manifest.columns:
        by_hash = manifest.drop_duplicates(subset=["system", "hash"]).set_index(
            ["system", "hash"]
        )[keep]
        keys = list(zip(out.loc[hashed, "system"], out.loc[hashed, "structure_hash"]))
        if keys:
            joined = by_hash.reindex(keys)
            for column in keep:
                out.loc[hashed, column] = joined[column].to_numpy()

    if "index" in manifest.columns and (~hashed).any():
        by_index = manifest.drop_duplicates(subset=["system", "index"]).set_index(
            ["system", "index"]
        )[keep]
        keys = list(zip(out.loc[~hashed, "system"], out.loc[~hashed, "struct_index"]))
        if keys:
            joined = by_index.reindex(keys)
            for column in keep:
                out.loc[~hashed, column] = joined[column].to_numpy()
    return out


def collect_from_config(cfg: CampaignConfig, output=None) -> pd.DataFrame:
    return collect_campaign(cfg.root, cfg.system.systems, output)
