"""INCAR assembly and the MAGMOM string."""

from __future__ import annotations

from pymatgen.core import Lattice, Structure

from assystant.config import MagmomConfig
from assystant.incar import build_incar, get_incar_magmom_string, stack_element_string

from conftest import bcc_fe


def _fe_c_fe() -> Structure:
    """Fe Fe C Fe -- deliberately NOT species-sorted, to pin run-length encoding."""
    return Structure(
        Lattice.cubic(4.0),
        ["Fe", "Fe", "C", "Fe"],
        [[0, 0, 0], [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5]],
    )


def test_stack_element_string_groups_consecutive_runs():
    elements, counts = stack_element_string(_fe_c_fe())
    assert elements == ["Fe", "C", "Fe"]
    assert counts == [2, 1, 1]
    assert sum(counts) == 4


def test_stack_element_string_on_a_pure_structure():
    assert stack_element_string(bcc_fe()) == (["Fe"], [2])


def test_magmom_string_is_positional():
    """MAGMOM is positional: it must follow site order, not aggregate by species."""
    got = get_incar_magmom_string(_fe_c_fe(), host_ele="Fe", host_magmom=3, solute_magmom=-1)
    assert got == "2*3 1*-1 1*3"


def test_magmom_string_marks_every_site_as_solute_when_host_absent():
    got = get_incar_magmom_string(bcc_fe(), host_ele="Ni", host_magmom=3, solute_magmom=-1)
    assert got == "2*-1"


def test_build_incar_stamps_system_and_magmom_when_spin_polarised():
    incar = build_incar(
        {"ENCUT": 400, "ISPIN": 2},
        _fe_c_fe(),
        MagmomConfig(host=None, host_moment=3.0, solute_moment=-1.0),
        "struct_00007_deadbeef",
        host="Fe",
    )
    assert incar["SYSTEM"] == "struct_00007_deadbeef"
    assert incar["MAGMOM"] == [3.0, 3.0, -1.0, 3.0]
    assert incar["ENCUT"] == 400


def test_build_incar_omits_magmom_when_not_spin_polarised():
    incar = build_incar(
        {"ENCUT": 400, "ISPIN": 1, "MAGMOM": "4*3"},
        _fe_c_fe(),
        MagmomConfig(host_moment=3.0, solute_moment=-1.0),
        "tag",
        host="Fe",
    )
    assert "MAGMOM" not in incar


def test_host_defaults_to_the_first_species():
    assert MagmomConfig(host=None).host_for(["Ni", "C"]) == "Ni"
    assert MagmomConfig(host="Fe").host_for(["Ni", "C"]) == "Fe"


# -- the statics INCAR (training labels) -------------------------------------
def _static(base, overrides, **kw):
    from assystant.incar import build_static_incar

    return build_static_incar(
        base,
        overrides,
        _fe_c_fe(),
        MagmomConfig(host_moment=3.0, solute_moment=-1.0),
        "tag",
        host="Fe",
        **kw,
    )


def test_static_incar_defaults_match_the_historical_overrides():
    base = {
        "ENCUT": 400,
        "ISPIN": 2,
        "KSPACING": 0.5,
        "EDIFF": 1e-4,
        "ISIF": 7,
        "NSW": 500,
        "IBRION": 2,
    }
    incar = _static(base, {})
    assert incar["KSPACING"] == 0.25 and incar["EDIFF"] == 1e-5 and incar["EDIFFG"] == 1e-4
    assert incar["LREAL"] is False
    assert incar["NSW"] == 0 and "ISIF" not in incar
    assert incar["ENCUT"] == 400 and incar["IBRION"] == 2  # inherited from the relaxation INCAR
    assert incar["SYSTEM"] == "tag" and incar["MAGMOM"] == [3.0, 3.0, -1.0, 3.0]


def test_static_incar_overrides_win_and_null_inherits():
    base = {"ENCUT": 400, "ISPIN": 1, "KSPACING": 0.5, "ALGO": "Fast", "IBRION": 2}
    incar = _static(base, {"ENCUT": 520, "kspacing": None, "ALGO": "Normal", "NELM": 200})
    assert incar["ENCUT"] == 520  # label calculations at higher cutoff than the relaxation
    assert (
        incar["KSPACING"] == 0.5
    )  # null -> the 0.25 default is dropped, relaxation value inherited
    assert incar["ALGO"] == "Normal" and incar["NELM"] == 200
    assert incar["EDIFF"] == 1e-5  # untouched defaults still apply
    assert "MAGMOM" not in incar


def test_static_incar_can_switch_spin_polarisation_for_the_labels():
    incar = _static({"ENCUT": 400, "ISPIN": 1, "IBRION": 2}, {"ISPIN": 2})
    assert incar["MAGMOM"] == [3.0, 3.0, -1.0, 3.0]


# -- per-element moments -------------------------------------------------------
def test_moments_mapping_wins_over_the_host_solute_rule():
    got = get_incar_magmom_string(
        _fe_c_fe(), host_ele="Fe", host_magmom=3.0, solute_magmom=-1.0, moments={"C": 0.0}
    )
    assert got == "2*3.0 1*0.0 1*3.0"
    # an element not in the mapping still follows the rule
    got = get_incar_magmom_string(
        _fe_c_fe(), host_ele="Fe", host_magmom=3.0, solute_magmom=-1.0, moments={"Mn": -2.0}
    )
    assert got == "2*3.0 1*-1.0 1*3.0"


def test_build_incar_uses_magmom_moments():
    cfg = MagmomConfig(
        host=None, host_moment=3.0, solute_moment=-1.0, moments={"C": 0.0, "Fe": 2.5}
    )
    incar = build_incar({"ENCUT": 400, "ISPIN": 2}, _fe_c_fe(), cfg, "tag", host="Fe")
    assert incar["MAGMOM"] == [2.5, 2.5, 0.0, 2.5]
    assert cfg.moment_for("Ni", host="Fe") == -1.0
    assert cfg.moment_for("Fe", host="Fe") == 2.5
