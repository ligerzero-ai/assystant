"""Shared fixtures. Nothing here touches SLURM, VASP or PyXtal."""

from __future__ import annotations

import pytest
from pymatgen.core import Lattice, Structure

from assystant.config import CampaignConfig
from assystant.generate import poscar_hash, write_system


@pytest.fixture
def campaign_dict(tmp_path):
    """A minimal but complete campaign config, rooted in tmp_path."""
    return {
        "root": str(tmp_path / "camp"),
        "system": {
            "species": ["Fe"],
            "min_atoms": 1,
            "max_atoms": 4,
            "spacegroups": [1, 5],
            "seed": 7,
        },
        "incar": {"ENCUT": 400, "ISPIN": 2, "SIGMA": 0.2, "ISIF": 7, "NSW": 500, "IBRION": 2},
        "magmom": {"host": None, "host_moment": 3.0, "solute_moment": -1.0},
        "assyst": {"ionic_steps": 100, "n_rattle": 5, "n_stretch": 5},
        "filters": {"min_dist": 1.0, "min_dist_backend": "neighbor_list"},
        "slurm": {"partition": "s.cmmg", "ntasks": 64, "time": "48:00:00",
                  "env": "/opt/conda/envs/assystant"},
        "vasp": {"binary": "/opt/vasp/bin/vasp_std", "custodian": True,
                 "custodian_python": "/opt/conda/envs/pymatgen/bin/python"},
    }


@pytest.fixture
def cfg(campaign_dict):
    return CampaignConfig.from_dict(campaign_dict).resolve()


def bcc_fe(a: float = 2.87) -> Structure:
    return Structure(Lattice.cubic(a), ["Fe", "Fe"], [[0, 0, 0], [0.5, 0.5, 0.5]])


def fcc_ni(a: float = 3.52) -> Structure:
    return Structure(
        Lattice.cubic(a),
        ["Ni"] * 4,
        [[0, 0, 0], [0, 0.5, 0.5], [0.5, 0, 0.5], [0.5, 0.5, 0]],
    )


def record_for(structure, spacegroup: int = 229) -> dict:
    """A generate.py record dict for a hand-built structure (no PyXtal needed)."""
    poscar = structure.to(fmt="poscar")
    return {
        "poscar": poscar,
        "hash": poscar_hash(poscar),
        "spacegroup": spacegroup,
        "composition": "-".join(str(int(v)) for v in
                                structure.composition.get_el_amt_dict().values()),
        "formula": structure.composition.formula.replace(" ", ""),
        "n_atoms": structure.num_sites,
        "volume_per_atom": structure.volume / structure.num_sites,
    }


@pytest.fixture
def generated_campaign(cfg):
    """A campaign whose Fe system has two structures generated on disk."""
    records = [record_for(bcc_fe(2.87)), record_for(bcc_fe(3.10))]
    manifest = write_system(cfg.root, ["Fe"], records)
    return cfg, manifest
