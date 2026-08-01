"""collect must read BOTH the hashed layout and the legacy struct_pyxtal_<idx> one."""

from __future__ import annotations

import pandas as pd
import pytest

from assystant.collect import collect_campaign, iter_result_dirs
from assystant.generate import struct_dir_name, write_system
from assystant.submit import RESULT_FILENAME

from conftest import bcc_fe, fcc_ni, record_for


def _write_result(directory, rows, energy_offset=0.0):
    directory.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        {
            "job_name": [f"{directory.name}_row{i}" for i in range(rows)],
            "energy": [float(i) + energy_offset for i in range(rows)],
        }
    )
    frame.to_pickle(directory / RESULT_FILENAME)
    return frame


@pytest.fixture
def mixed_campaign(cfg):
    """A campaign root holding one hashed system and one legacy system."""
    records = [record_for(bcc_fe(2.87), spacegroup=229), record_for(fcc_ni(), spacegroup=225)]
    manifest = write_system(cfg.root, ["Fe"], records)

    hashed_root = cfg.root_path / "Fe"
    _write_result(hashed_root / manifest.iloc[0]["dirname"], 2)
    _write_result(hashed_root / manifest.iloc[1]["dirname"], 3, energy_offset=100.0)

    # A legacy system, as produced by the pre-2026 scripts: struct_pyxtal_<idx>
    # directories and a manifest keyed only by index.
    legacy_root = cfg.root_path / "Ni"
    _write_result(legacy_root / "struct_pyxtal_0", 1)
    _write_result(legacy_root / "struct_pyxtal_17", 2)
    pd.DataFrame(
        {
            "index": [0, 17],
            "system": ["Ni", "Ni"],
            "dirname": ["struct_pyxtal_0", "struct_pyxtal_17"],
            "hash": ["", ""],
            "spacegroup": [1, 62],
            "composition": ["1", "4"],
            "formula": ["Ni1", "Ni4"],
            "n_atoms": [1, 4],
            "volume_per_atom": [10.0, 11.0],
        }
    ).to_csv(legacy_root / "manifest.csv", index=False)
    return cfg, manifest


def test_iter_result_dirs_finds_both_layouts(mixed_campaign):
    cfg, manifest = mixed_campaign
    found = sorted(iter_result_dirs(cfg.root), key=lambda r: (r.system, r.index))
    assert [(r.system, r.index, r.layout) for r in found] == [
        ("Fe", 0, "hashed"),
        ("Fe", 1, "hashed"),
        ("Ni", 0, "legacy"),
        ("Ni", 17, "legacy"),
    ]
    assert found[0].structure_hash == manifest.iloc[0]["hash"]
    assert found[2].structure_hash is None


def test_iter_result_dirs_ignores_dirs_without_results(mixed_campaign):
    cfg, manifest = mixed_campaign
    empty = cfg.root_path / "Fe" / struct_dir_name(99, "abcdef01")
    empty.mkdir(parents=True)
    (empty / "executorlib_cache").mkdir()
    assert 99 not in [r.index for r in iter_result_dirs(cfg.root)]


def test_collect_concatenates_both_layouts(mixed_campaign):
    cfg, _ = mixed_campaign
    frame = collect_campaign(cfg.root)

    assert len(frame) == 2 + 3 + 1 + 2
    assert set(frame["system"]) == {"Fe", "Ni"}
    assert set(frame["layout"]) == {"hashed", "legacy"}
    assert frame["energy"].max() == 102.0  # the second Fe structure's rows survived
    # Every row knows which directory it came from.
    assert frame["run_dir"].str.startswith(str(cfg.root)).all()


def test_collect_joins_hashed_rows_on_the_structure_hash(mixed_campaign):
    cfg, manifest = mixed_campaign
    frame = collect_campaign(cfg.root)

    first = frame[frame["structure_hash"] == manifest.iloc[0]["hash"]]
    assert len(first) == 2
    assert set(first["spacegroup"]) == {229}
    assert set(first["n_atoms"]) == {2}

    second = frame[frame["structure_hash"] == manifest.iloc[1]["hash"]]
    assert set(second["spacegroup"]) == {225}
    assert set(second["n_atoms"]) == {4}


def test_collect_joins_legacy_rows_on_the_index(mixed_campaign):
    cfg, _ = mixed_campaign
    frame = collect_campaign(cfg.root)
    legacy = frame[frame["layout"] == "legacy"]

    assert set(legacy["spacegroup"]) == {1, 62}
    assert set(legacy.loc[legacy["struct_index"] == 0, "spacegroup"]) == {1}
    assert set(legacy.loc[legacy["struct_index"] == 17, "spacegroup"]) == {62}
    assert set(legacy.loc[legacy["struct_index"] == 17, "n_atoms"]) == {4}
    assert set(legacy.loc[legacy["struct_index"] == 0, "n_atoms"]) == {1}


def test_hashed_rows_do_not_inherit_a_regenerated_structures_provenance(cfg):
    """Hashed rows must join on the HASH, not on the index.

    Re-running `gen` with a different seed puts a different structure at index 0
    while the old `struct_00000_<oldhash>/` still holds results. Joining on the
    index would silently label those results with the NEW structure's spacegroup
    and atom count. Joining on the hash leaves them unlabelled, which is the
    honest answer: this result did not come from any structure in the manifest.
    """
    old = record_for(bcc_fe(2.87), spacegroup=229)
    new = record_for(fcc_ni(), spacegroup=225)
    assert old["hash"] != new["hash"]

    # The manifest describes the regenerated structure at index 0 ...
    write_system(cfg.root, ["Fe"], [new])
    # ... but the results on disk belong to the structure that used to be there.
    _write_result(cfg.root_path / "Fe" / struct_dir_name(0, old["hash"]), 2)

    frame = collect_campaign(cfg.root)
    assert len(frame) == 2
    assert set(frame["structure_hash"]) == {old["hash"]}
    assert frame["spacegroup"].isna().all(), (
        "results were joined on the index and picked up the regenerated "
        "structure's provenance"
    )


def test_legacy_layout_reads_without_any_manifest(cfg):
    """Existing campaign data must stay readable with no manifest at all."""
    _write_result(cfg.root_path / "Fe" / "struct_pyxtal_3", 2)
    frame = collect_campaign(cfg.root)
    assert len(frame) == 2
    assert set(frame["struct_index"]) == {3}
    assert set(frame["layout"]) == {"legacy"}


def test_collect_writes_the_output_file(mixed_campaign, tmp_path):
    cfg, _ = mixed_campaign
    out = tmp_path / "out" / "train.pkl"
    frame = collect_campaign(cfg.root, output=out)
    assert out.is_file()
    pd.testing.assert_frame_equal(pd.read_pickle(out), frame)


def test_collect_restricted_to_configured_systems(mixed_campaign):
    cfg, _ = mixed_campaign
    frame = collect_campaign(cfg.root, systems=[["Fe"]])
    assert set(frame["system"]) == {"Fe"}
    assert len(frame) == 5


def test_collect_on_an_empty_campaign_returns_empty(cfg):
    cfg.root_path.mkdir(parents=True, exist_ok=True)
    frame = collect_campaign(cfg.root)
    assert len(frame) == 0
