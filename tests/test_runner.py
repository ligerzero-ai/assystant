"""The config -> run_ASSYST_on_structure mapping. Builds no graph and runs no VASP."""

from __future__ import annotations

import inspect

import pytest

from assystant.runner import assyst_kwargs
from assystant.submit import RESULT_FILENAME

from conftest import bcc_fe

assyst_workflow = pytest.importorskip("pyiron_workflow_assyst")


def _signature():
    return inspect.signature(assyst_workflow.run_ASSYST_on_structure)


def _supported():
    return "static_incar" in _signature().parameters


def _kwargs(cfg, tmp_path):
    return assyst_kwargs(
        cfg,
        bcc_fe(),
        tmp_path / "struct_00000_deadbeef",
        "Fe",
        static_incar_supported=_supported(),
    )


def test_every_kwarg_is_a_real_parameter(cfg, tmp_path):
    """Checked against inspect.signature rather than eyeballed."""
    parameters = _signature().parameters
    unknown = sorted(set(_kwargs(cfg, tmp_path)) - set(parameters))
    assert unknown == [], f"not parameters of run_ASSYST_on_structure: {unknown}"


def test_supports_static_incar_matches_the_installed_signature():
    from assystant.runner import supports_static_incar

    assert supports_static_incar() == _supported()


def test_static_incar_is_passed_when_supported(cfg, tmp_path):
    changed = cfg.resolve({"incar_static.ENCUT": 520})
    kwargs = assyst_kwargs(changed, bcc_fe(), tmp_path / "d", "Fe", static_incar_supported=True)
    static = kwargs["static_incar"]
    assert static["ENCUT"] == 520 and static["KSPACING"] == 0.25 and static["NSW"] == 0
    assert "ISIF" not in static and static["MAGMOM"] == [3.0, 3.0]
    assert kwargs["incar"]["ENCUT"] == 400 and kwargs["incar"]["ISIF"] == 7  # relaxation untouched


def test_static_incar_is_omitted_on_an_old_graph_when_left_default(cfg, tmp_path):
    kwargs = assyst_kwargs(cfg, bcc_fe(), tmp_path / "d", "Fe", static_incar_supported=False)
    assert "static_incar" not in kwargs  # the old graph derives the identical INCAR itself


def test_customised_static_incar_refuses_an_old_graph(cfg, tmp_path):
    from assystant.config import ConfigError

    changed = cfg.resolve({"incar_static.ENCUT": 520})
    with pytest.raises(ConfigError, match="static_incar"):
        assyst_kwargs(changed, bcc_fe(), tmp_path / "d", "Fe", static_incar_supported=False)


def test_every_required_parameter_is_supplied(cfg, tmp_path):
    required = {
        name
        for name, parameter in _signature().parameters.items()
        if parameter.default is inspect.Parameter.empty
    }
    assert required <= set(_kwargs(cfg, tmp_path))


def test_files_to_be_deleted_is_never_passed(cfg, tmp_path):
    """vasp_job: None deletes CHG/CHGCAR/WAVECAR, [] deletes NOTHING.

    Passing [] would retain those files across ~64,000 calculations per
    campaign -- tens of terabytes, with no error. Nothing here may set it.
    """
    assert "files_to_be_deleted" not in _kwargs(cfg, tmp_path)


def test_paths_are_absolute_and_job_specific(cfg, tmp_path):
    run_dir = (tmp_path / "struct_00000_deadbeef").resolve()
    kwargs = _kwargs(cfg, tmp_path)
    assert kwargs["job_name"] == str(run_dir)
    # The workflow's own default is CWD-relative and would collide between
    # concurrent structures.
    assert kwargs["train_df_filename"] == str(run_dir / RESULT_FILENAME)
    assert kwargs["train_df_filename"].startswith("/")


def test_config_values_reach_the_graph(cfg, tmp_path):
    changed = cfg.resolve(
        {
            "assyst.ionic_steps": 37,
            "assyst.n_rattle": 3,
            "assyst.n_stretch": 4,
            "assyst.core_overlap_tolerance": 0.25,
            "filters.min_dist_backend": "mic",
        }
    )
    kwargs = assyst_kwargs(changed, bcc_fe(), tmp_path / "d", "Fe")
    assert kwargs["ionic_steps"] == 37
    assert kwargs["n_rattle_permutations"] == 3
    assert kwargs["n_stretch_permutations"] == 4
    assert kwargs["core_overlap_tolerance"] == 0.25
    assert kwargs["min_dist_backend"] == "mic"
    assert kwargs["potcar_paths"] is None


def test_incar_carries_the_magmom_and_system_tag(cfg, tmp_path):
    kwargs = _kwargs(cfg, tmp_path)
    assert kwargs["incar"]["SYSTEM"] == "struct_00000_deadbeef"
    assert kwargs["incar"]["MAGMOM"] == [3.0, 3.0]
    assert kwargs["incar"]["ENCUT"] == 400


def test_vasp_command_is_rendered_into_the_kwargs(cfg, tmp_path):
    assert "/opt/vasp/bin/vasp_std" in _kwargs(cfg, tmp_path)["vasp_command"]
