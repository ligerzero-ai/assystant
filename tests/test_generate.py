"""Composition enumeration, content hashing, and the manifest."""

from __future__ import annotations

import os
from itertools import product

import pandas as pd
import pytest

from assystant.generate import (
    adjust_volume_per_atom,
    enumerate_compositions,
    limit_blas_threads,
    parse_struct_dir,
    poscar_hash,
    read_manifest,
    struct_dir_name,
    write_system,
)

from conftest import bcc_fe, fcc_ni, record_for


# -- composition enumeration ---------------------------------------------
def test_unary_compositions_are_cell_sizes():
    assert enumerate_compositions(["Fe"], min_atoms=1, max_atoms=12) == [(n,) for n in range(1, 13)]
    assert enumerate_compositions(["Fe"], min_atoms=3, max_atoms=5) == [
        (3,),
        (4,),
        (5,),
    ]


def test_unary_ignores_stoich_bounds():
    """For one species the stoichiometry IS the atom count, so stoich_* must not apply."""
    assert enumerate_compositions(["Fe"], min_atoms=1, max_atoms=4, stoich_min=2, stoich_max=3) == [
        (1,),
        (2,),
        (3,),
        (4,),
    ]


def test_binary_compositions_filtered_by_total_atom_count():
    got = enumerate_compositions(["Fe", "C"], min_atoms=2, max_atoms=4, stoich_min=1, stoich_max=3)
    expected = [c for c in product(range(1, 4), repeat=2) if 2 <= sum(c) <= 4]
    assert got == expected
    assert all(2 <= sum(c) <= 4 for c in got)
    assert (1, 1) in got and (2, 2) in got
    assert (1, 4) not in got  # above stoich_max
    assert (3, 3) not in got  # sum 6 > max_atoms
    assert (0, 2) not in got  # below stoich_min


def test_ternary_respects_both_bounds():
    got = enumerate_compositions(
        ["Fe", "B", "C"], min_atoms=4, max_atoms=5, stoich_min=1, stoich_max=3
    )
    assert got, "expected at least one composition"
    assert all(4 <= sum(c) <= 5 for c in got)
    assert all(all(1 <= n <= 3 for n in c) for c in got)
    assert len(got) == len([c for c in product(range(1, 4), repeat=3) if 4 <= sum(c) <= 5])


def test_min_atoms_bound_excludes_small_totals():
    got = enumerate_compositions(["Fe", "C"], min_atoms=5, max_atoms=6, stoich_min=1, stoich_max=6)
    assert (1, 1) not in got
    assert all(sum(c) >= 5 for c in got)


# -- content hashing ------------------------------------------------------
def test_same_structure_gets_the_same_directory():
    one, two = bcc_fe(2.87), bcc_fe(2.87)
    assert poscar_hash(one.to(fmt="poscar")) == poscar_hash(two.to(fmt="poscar"))
    assert struct_dir_name(5, poscar_hash(one.to(fmt="poscar"))) == struct_dir_name(
        5, poscar_hash(two.to(fmt="poscar"))
    )


def test_different_structures_get_different_directories():
    """The whole point: a changed structure cannot land in a populated namespace."""
    same_index = 5
    a = struct_dir_name(same_index, poscar_hash(bcc_fe(2.87).to(fmt="poscar")))
    b = struct_dir_name(same_index, poscar_hash(bcc_fe(3.10).to(fmt="poscar")))
    c = struct_dir_name(same_index, poscar_hash(fcc_ni().to(fmt="poscar")))
    assert a != b != c and a != c


def test_hash_is_eight_hex_chars_of_sha256():
    import hashlib

    poscar = bcc_fe().to(fmt="poscar")
    digest = poscar_hash(poscar)
    assert len(digest) == 8
    assert all(ch in "0123456789abcdef" for ch in digest)
    assert digest == hashlib.sha256(poscar.encode("utf-8")).hexdigest()[:8]


def test_dir_name_is_zero_padded_to_five():
    assert struct_dir_name(7, "deadbeef") == "struct_00007_deadbeef"
    assert struct_dir_name(12345, "deadbeef") == "struct_12345_deadbeef"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("struct_00007_deadbeef", (7, "deadbeef", "hashed")),
        ("struct_pyxtal_42", (42, None, "legacy")),
        ("structures", None),
        ("struct_00007", None),
        ("struct_00007_DEADBEEF", None),
        ("executorlib_cache", None),
    ],
)
def test_parse_struct_dir(name, expected):
    assert parse_struct_dir(name) == expected


# -- volume rescaling -----------------------------------------------------
def test_volume_inside_band_is_untouched():
    structure = bcc_fe(2.87)
    vpa = structure.volume / structure.num_sites
    out = adjust_volume_per_atom(structure, vpa * 0.8, vpa * 1.2)
    assert out is structure


def test_volume_below_band_is_scaled_up_to_the_lower_bound():
    structure = bcc_fe(2.0)
    out = adjust_volume_per_atom(structure, 11.0, 15.0)
    assert out.volume / out.num_sites == pytest.approx(11.0)


def test_volume_above_band_is_scaled_down_to_the_upper_bound():
    structure = bcc_fe(5.0)
    out = adjust_volume_per_atom(structure, 11.0, 15.0)
    assert out.volume / out.num_sites == pytest.approx(15.0)


# -- BLAS thread pinning --------------------------------------------------
def test_blas_threads_pinned_even_when_already_set(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "8")
    monkeypatch.delenv("MKL_NUM_THREADS", raising=False)
    limit_blas_threads()
    for var in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        assert os.environ[var] == "1", var


# -- manifest -------------------------------------------------------------
def test_write_system_writes_poscars_and_manifest(cfg):
    records = [
        record_for(bcc_fe(2.87), spacegroup=229),
        record_for(fcc_ni(), spacegroup=225),
    ]
    manifest = write_system(cfg.root, ["Fe"], records)

    sys_dir = cfg.root_path / "Fe"
    assert (sys_dir / "manifest.csv").is_file()
    assert sorted(p.name for p in (sys_dir / "structures").iterdir()) == [
        "00000_structure.vasp",
        "00001_structure.vasp",
    ]
    assert list(manifest["index"]) == [0, 1]
    assert list(manifest["hash"]) == [records[0]["hash"], records[1]["hash"]]
    assert list(manifest["dirname"]) == [
        f"struct_00000_{records[0]['hash']}",
        f"struct_00001_{records[1]['hash']}",
    ]
    assert list(manifest["spacegroup"]) == [229, 225]
    assert list(manifest["n_atoms"]) == [2, 4]
    # The POSCAR on disk is byte-identical to the string that was hashed.
    written = (sys_dir / "structures" / "00000_structure.vasp").read_text()
    assert poscar_hash(written) == records[0]["hash"]


def test_manifest_round_trips(cfg):
    records = [record_for(bcc_fe(2.87))]
    written = write_system(cfg.root, ["Fe"], records)
    reread = read_manifest(cfg.root, ["Fe"])
    assert reread is not None
    pd.testing.assert_frame_equal(
        written.reset_index(drop=True), reread.reset_index(drop=True), check_dtype=False
    )


def test_read_manifest_returns_none_when_not_generated(cfg):
    assert read_manifest(cfg.root, ["Ni"]) is None


# -- PyXtal scan (real generation, one spacegroup) -------------------------
def test_seeded_generation_is_reproducible():
    """Neither pyxtal nor Structure.perturb reads numpy's GLOBAL RNG.

    The source notebook seeded only np.random, which did not in fact make
    generation reproducible; the seed has to be threaded into
    `pyxtal.from_random(random_state=...)` and `Structure.perturb(seed=...)`.
    """
    from assystant.generate import _gen_one

    # Spacegroup 1 (P1) with 4 atoms on purpose: every fractional coordinate is
    # free, so the structure is genuinely determined by the RNG. A high-symmetry
    # group such as 229 would NOT do -- there the Wyckoff positions are fixed and
    # adjust_volume_per_atom clamps the cell to a band edge, so two unseeded runs
    # can coincide by construction and the test would pass without the seed
    # being threaded at all.
    kwargs = dict(
        sg=1,
        species=["Fe"],
        comp=(4,),
        eq_volume_map={"Fe": 11.8},
        strain_min=0.8,
        strain_max=1.2,
        perturb=0.1,
    )
    first = _gen_one(seed=12345, **kwargs)
    second = _gen_one(seed=12345, **kwargs)
    other = _gen_one(seed=999, **kwargs)

    assert "__error__" not in first, first
    assert first["poscar"] == second["poscar"]
    assert first["hash"] == second["hash"]
    assert other["hash"] != first["hash"]


def test_generation_reports_the_reason_structures_were_rejected(capsys):
    """A silent "0 structures" is undiagnosable; the tally names the exception."""
    from assystant.generate import generate_structures

    # Spacegroup 229 (bcc) cannot accommodate a single atom, so every task fails
    # with pyxtal's Comp_CompatibilityError.
    records = generate_structures(
        ["Fe"],
        {"Fe": 11.8},
        min_atoms=1,
        max_atoms=1,
        sg_min=229,
        sg_max=229,
        n_jobs=1,
        progress=False,
    )
    assert records == []
    assert "Comp_CompatibilityError" in capsys.readouterr().out


def test_generation_produces_structures_for_a_feasible_pair():
    from assystant.generate import generate_structures

    records = generate_structures(
        ["Fe"],
        {"Fe": 11.8},
        min_atoms=2,
        max_atoms=2,
        sg_min=229,
        sg_max=229,
        seed=1,
        n_jobs=1,
        progress=False,
    )
    assert len(records) == 1
    assert records[0]["n_atoms"] == 2
    assert records[0]["spacegroup"] == 229
    # Volume rescaled into [0.8, 1.2] x 11.8.
    assert 0.8 * 11.8 <= records[0]["volume_per_atom"] <= 1.2 * 11.8


# -- default stoichiometry = the full composition range ---------------------
def test_default_stoich_bounds_scan_the_full_composition_range():
    """With no stoich bounds, every composition with each species >= 1 and
    min_atoms <= total <= max_atoms is scanned: for a binary at total n that is
    1..n-1 of each species, whatever max_atoms is."""
    got = enumerate_compositions(["Fe", "C"], min_atoms=1, max_atoms=20)
    assert (19, 1) in got and (1, 19) in got
    assert (20, 0) not in got and (0, 20) not in got
    assert min(sum(c) for c in got) == 2  # each species present -> binary starts at 2 atoms
    assert len(got) == sum(n - 1 for n in range(2, 21))
    assert got == sorted(got)  # lexicographic, the order the per-task seeds are keyed on


def test_default_ternary_starts_at_three_atoms_and_covers_every_split():
    got = enumerate_compositions(["Fe", "B", "C"], min_atoms=1, max_atoms=6)
    assert min(sum(c) for c in got) == 3
    assert (4, 1, 1) in got and (1, 1, 4) in got
    assert len(got) == len([c for c in product(range(1, 7), repeat=3) if sum(c) <= 6])


def test_explicit_stoich_max_still_narrows_the_scan():
    got = enumerate_compositions(["Fe", "C"], min_atoms=1, max_atoms=20, stoich_max=3)
    assert max(max(c) for c in got) == 3
    assert (19, 1) not in got
