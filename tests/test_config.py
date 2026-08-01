"""Config parsing, override precedence, and the staleness guard."""

from __future__ import annotations

import pytest
import yaml

from assystant.cli import build_parser, overrides_from_args
from assystant.config import (
    CampaignConfig,
    ConfigError,
    StaleConfigError,
    _validate_ibrion,
    config_differences,
    load_recorded_config,
    recorded_config_path,
)
from assystant.submit import guard_config


# -- parsing --------------------------------------------------------------
def test_yaml_parses_into_typed_sections(campaign_dict, tmp_path):
    path = tmp_path / "camp.yaml"
    path.write_text(yaml.safe_dump(campaign_dict))
    cfg = CampaignConfig.from_yaml(path).resolve()

    assert cfg.system.systems == [["Fe"]]
    assert cfg.system.sg_min == 1 and cfg.system.sg_max == 5
    assert cfg.slurm.ntasks == 64
    assert cfg.assyst.ionic_steps == 100
    assert cfg.incar["ENCUT"] == 400
    # Defaults fill in sections the YAML did not mention.
    assert cfg.assyst.shear_strain == 0.8
    assert cfg.vasp.max_errors == 10


def test_species_accepts_flat_list_and_list_of_lists(campaign_dict):
    campaign_dict["system"]["species"] = ["Fe", "C"]
    assert CampaignConfig.from_dict(campaign_dict).resolve().system.systems == [["Fe", "C"]]

    campaign_dict["system"]["species"] = [["Fe"], ["Ni"], ["Fe", "C"]]
    cfg = CampaignConfig.from_dict(campaign_dict).resolve()
    assert cfg.system.systems == [["Fe"], ["Ni"], ["Fe", "C"]]
    assert cfg.system_names() == ["Fe", "Ni", "Fe_C"]


def test_unknown_key_is_rejected_not_ignored(campaign_dict):
    campaign_dict["assyst"]["n_rattles"] = 5  # typo for n_rattle
    with pytest.raises(ConfigError, match="n_rattles"):
        CampaignConfig.from_dict(campaign_dict)


def test_zero_permutations_rejected_with_actionable_message(campaign_dict):
    """Both permutation counts at 0 cannot work -- reject in config, not in the graph.

    pyiron_workflow 0.19.0's ForEach raises an opaque IndexError on a length-0
    iterable (issue 915), so run_ASSYST_on_structure raises
    NoPermutationsGeneratedError instead. Catching it here means the message
    names the YAML keys.
    """
    campaign_dict["assyst"]["n_rattle"] = 0
    campaign_dict["assyst"]["n_stretch"] = 0
    with pytest.raises(ConfigError) as excinfo:
        CampaignConfig.from_dict(campaign_dict).resolve()
    assert "n_rattle" in str(excinfo.value) and "n_stretch" in str(excinfo.value)

    # One of the two nonzero is fine.
    campaign_dict["assyst"]["n_rattle"] = 1
    CampaignConfig.from_dict(campaign_dict).resolve()


def test_min_dist_other_than_one_is_rejected(campaign_dict):
    """run_ASSYST_on_structure hardcodes min_dist=1.0; accepting 0.7 would be a lie."""
    campaign_dict["filters"]["min_dist"] = 0.7
    with pytest.raises(ConfigError, match="min_dist"):
        CampaignConfig.from_dict(campaign_dict).resolve()


# -- IBRION / relaxation validation ---------------------------------------
def test_missing_ibrion_is_rejected_when_a_relaxation_will_run(campaign_dict):
    """No IBRION + NSW>0 (from ionic_steps) is always wrong -- VASP defaults to
    IBRION=0 (MD), which then refuses the job outright over a missing POTIM.
    Catch it at config-load time instead of after the job reaches the queue.
    """
    del campaign_dict["incar"]["IBRION"]
    with pytest.raises(ConfigError, match="IBRION"):
        CampaignConfig.from_dict(campaign_dict).resolve()


def test_ibrion_zero_without_potim_is_rejected(campaign_dict):
    campaign_dict["incar"]["IBRION"] = 0
    with pytest.raises(ConfigError, match="POTIM"):
        CampaignConfig.from_dict(campaign_dict).resolve()


def test_ibrion_zero_with_potim_is_accepted(campaign_dict):
    campaign_dict["incar"]["IBRION"] = 0
    campaign_dict["incar"]["POTIM"] = 0.5
    cfg = CampaignConfig.from_dict(campaign_dict).resolve()
    assert cfg.incar["IBRION"] == 0
    assert cfg.incar["POTIM"] == 0.5


def test_ibrion_two_is_accepted(campaign_dict):
    campaign_dict["incar"]["IBRION"] = 2
    assert "POTIM" not in campaign_dict["incar"]
    cfg = CampaignConfig.from_dict(campaign_dict).resolve()
    assert cfg.incar["IBRION"] == 2


def test_ibrion_check_is_skipped_when_ionic_steps_is_zero():
    """No relaxation stage runs at ionic_steps == 0, so an absent IBRION is fine.

    (``assyst.ionic_steps`` itself is separately floored at >= 1 by
    ``CampaignConfig.validate``, so this exercises the helper directly rather
    than through a full campaign config.)
    """
    _validate_ibrion({}, 0)  # must not raise


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("system", "min_atoms", 0),
        ("system", "max_atoms", 0),
        ("system", "spacegroups", [0, 230]),
        ("system", "spacegroups", [1, 231]),
        ("system", "strain_min", 1.5),
        ("assyst", "ionic_steps", 0),
        ("filters", "min_dist_backend", "nearest"),
        ("slurm", "ntasks", 0),
    ],
)
def test_validation_rejects_out_of_range_values(campaign_dict, section, key, value):
    campaign_dict[section][key] = value
    with pytest.raises(ConfigError):
        CampaignConfig.from_dict(campaign_dict).resolve()


def test_dump_round_trips(cfg):
    """The recorded config is itself a loadable campaign config (the runner loads it)."""
    reloaded = CampaignConfig.from_dict(yaml.safe_load(yaml.safe_dump(cfg.dump()))).resolve()
    assert reloaded.dump() == cfg.dump()


# -- override precedence --------------------------------------------------
def test_cli_flag_beats_yaml_which_beats_default(campaign_dict, tmp_path):
    path = tmp_path / "camp.yaml"
    path.write_text(yaml.safe_dump(campaign_dict))
    parser = build_parser()

    # YAML beats the dataclass default: ntasks defaults to 64 in both, so use
    # `time`, whose default is 48:00:00 and whose YAML value we change.
    campaign_dict["slurm"]["time"] = "12:00:00"
    path.write_text(yaml.safe_dump(campaign_dict))
    args = parser.parse_args(["run", str(path)])
    assert CampaignConfig.from_yaml(path).resolve(overrides_from_args(args)).slurm.time == "12:00:00"

    # CLI flag beats YAML.
    args = parser.parse_args(["run", str(path), "--time", "01:00:00", "--ntasks", "8"])
    cfg = CampaignConfig.from_yaml(path).resolve(overrides_from_args(args))
    assert cfg.slurm.time == "01:00:00"
    assert cfg.slurm.ntasks == 8

    # An unspecified flag leaves the YAML value alone.
    args = parser.parse_args(["run", str(path), "--ntasks", "8"])
    assert CampaignConfig.from_yaml(path).resolve(overrides_from_args(args)).slurm.time == "12:00:00"


def test_set_flag_reaches_arbitrary_paths(campaign_dict, tmp_path):
    path = tmp_path / "camp.yaml"
    path.write_text(yaml.safe_dump(campaign_dict))
    args = build_parser().parse_args(
        ["run", str(path), "--set", "assyst.rattle_strain=0.02", "--set", "vasp.custodian=false"]
    )
    cfg = CampaignConfig.from_yaml(path).resolve(overrides_from_args(args))
    assert cfg.assyst.rattle_strain == 0.02
    assert cfg.vasp.custodian is False


def test_override_of_unknown_path_is_rejected(cfg):
    with pytest.raises(ConfigError, match="unknown config path"):
        cfg.resolve({"assyst.no_such_key": 1})


def test_species_override_renormalises(cfg):
    """Overriding a field whose section has a __post_init__ must re-run it."""
    assert cfg.resolve({"system.species": ["Fe", "C"]}).system.systems == [["Fe", "C"]]


# -- staleness guard ------------------------------------------------------
def test_config_differences_names_the_changed_keys(cfg):
    changed = cfg.resolve({"assyst.ionic_steps": 50})
    assert config_differences(changed.dump(), cfg.dump()) == ["assyst.ionic_steps"]


def test_slurm_changes_are_not_staleness(cfg):
    """Wall time and task count change how work reaches the scheduler, not the physics."""
    changed = cfg.resolve({"slurm.time": "01:00:00", "slurm.ntasks": 8})
    assert config_differences(changed.dump(), cfg.dump()) == []


def test_guard_records_config_then_accepts_identical_rerun(cfg):
    guard_config(cfg)
    recorded = load_recorded_config(cfg.root)
    assert recorded is not None
    assert recorded_config_path(cfg.root).is_file()
    guard_config(cfg)  # identical -> no error


def test_guard_refuses_changed_incar_unless_from_scratch(cfg):
    guard_config(cfg)
    changed = cfg.resolve({"incar.ENCUT": 500})

    with pytest.raises(StaleConfigError) as excinfo:
        guard_config(changed)
    assert "incar.ENCUT" in str(excinfo.value)
    # The recorded config must be untouched by a refused run.
    assert load_recorded_config(cfg.root)["incar"]["ENCUT"] == 400

    guard_config(changed, from_scratch=True)
    assert load_recorded_config(cfg.root)["incar"]["ENCUT"] == 500


def test_guard_tolerates_slurm_change_without_from_scratch(cfg):
    guard_config(cfg)
    guard_config(cfg.resolve({"slurm.ntasks": 8, "slurm.time": "02:00:00"}))
