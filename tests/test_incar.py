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
