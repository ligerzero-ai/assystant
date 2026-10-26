"""Config parsing, override precedence, and the staleness guard."""

from __future__ import annotations

import copy

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
    assert (
        CampaignConfig.from_yaml(path).resolve(overrides_from_args(args)).slurm.time == "12:00:00"
    )

    # CLI flag beats YAML.
    args = parser.parse_args(["run", str(path), "--time", "01:00:00", "--ntasks", "8"])
    cfg = CampaignConfig.from_yaml(path).resolve(overrides_from_args(args))
    assert cfg.slurm.time == "01:00:00"
    assert cfg.slurm.ntasks == 8

    # An unspecified flag leaves the YAML value alone.
    args = parser.parse_args(["run", str(path), "--ntasks", "8"])
    assert (
        CampaignConfig.from_yaml(path).resolve(overrides_from_args(args)).slurm.time == "12:00:00"
    )


def test_set_flag_reaches_arbitrary_paths(campaign_dict, tmp_path):
    path = tmp_path / "camp.yaml"
    path.write_text(yaml.safe_dump(campaign_dict))
    args = build_parser().parse_args(
        [
            "run",
            str(path),
            "--set",
            "assyst.rattle_strain=0.02",
            "--set",
            "vasp.custodian=false",
        ]
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


# -- stoichiometry defaults and the staleness key ----------------------------
def test_unset_stoich_bounds_resolve_to_the_full_range(campaign_dict):
    campaign_dict["system"]["species"] = ["Fe", "C"]
    campaign_dict["system"]["max_atoms"] = 20
    campaign_dict["system"].pop("stoich_min", None)
    campaign_dict["system"].pop("stoich_max", None)
    cfg = CampaignConfig.from_dict(campaign_dict).resolve()
    assert cfg.system.stoich_range == (1, 20)
    dumped = cfg.dump()["system"]
    assert (dumped["stoich_min"], dumped["stoich_max"]) == (1, 20)
    # and an override of max_atoms moves the derived bound with it
    assert cfg.resolve({"system.max_atoms": 8}).system.stoich_range == (1, 8)


def test_explicit_stoich_bounds_are_kept_and_validated(campaign_dict):
    campaign_dict["system"]["species"] = ["Fe", "C"]
    campaign_dict["system"]["stoich_max"] = 3
    assert CampaignConfig.from_dict(campaign_dict).resolve().system.stoich_range == (
        1,
        3,
    )
    campaign_dict["system"]["stoich_min"] = 0
    with pytest.raises(ConfigError, match="stoich_min"):
        CampaignConfig.from_dict(campaign_dict).resolve()


def test_staleness_key_compares_compositions_not_stoich_numbers(campaign_dict):
    """A unary campaign recorded by an older release (stoich 1/12 written out
    explicitly) must not read as stale now that the default is derived."""
    campaign_dict["system"]["species"] = [["Fe"]]
    campaign_dict["system"]["max_atoms"] = 4
    campaign_dict["system"].pop("stoich_min", None)
    campaign_dict["system"].pop("stoich_max", None)
    new = CampaignConfig.from_dict(campaign_dict).resolve().dump()
    recorded = copy.deepcopy(new)
    recorded["system"]["stoich_min"], recorded["system"]["stoich_max"] = 1, 12
    assert config_differences(new, recorded) == []


def test_staleness_key_catches_a_widened_binary_scan(campaign_dict):
    campaign_dict["system"]["species"] = ["Fe", "C"]
    campaign_dict["system"]["max_atoms"] = 20
    campaign_dict["system"].pop("stoich_min", None)
    campaign_dict["system"].pop("stoich_max", None)
    new = CampaignConfig.from_dict(campaign_dict).resolve().dump()
    recorded = copy.deepcopy(new)
    recorded["system"]["stoich_max"] = 12  # what 0.1.1 silently scanned
    assert config_differences(new, recorded) == ["system.compositions.Fe_C"]


# -- incar_static -------------------------------------------------------------
def test_incar_static_parses_uppercased_and_round_trips(campaign_dict):
    campaign_dict["incar_static"] = {"encut": 520, "KSPACING": None}
    cfg = CampaignConfig.from_dict(campaign_dict).resolve()
    assert cfg.incar_static == {"ENCUT": 520, "KSPACING": None}
    reloaded = CampaignConfig.from_dict(yaml.safe_load(yaml.safe_dump(cfg.dump()))).resolve()
    assert reloaded.incar_static == cfg.incar_static
    assert cfg.resolve({"incar_static.ENCUT": 600}).incar_static["ENCUT"] == 600


def test_incar_static_rejects_isif_and_nonzero_nsw(campaign_dict):
    campaign_dict["incar_static"] = {"ISIF": 2}
    with pytest.raises(ConfigError, match="incar_static.ISIF"):
        CampaignConfig.from_dict(campaign_dict).resolve()
    campaign_dict["incar_static"] = {"NSW": 10}
    with pytest.raises(ConfigError, match="incar_static.NSW"):
        CampaignConfig.from_dict(campaign_dict).resolve()
    campaign_dict["incar_static"] = {"NSW": 0}
    CampaignConfig.from_dict(campaign_dict).resolve()  # explicit 0 is fine


def test_effective_static_overrides_layering():
    from assystant.config import DEFAULT_INCAR_STATIC, effective_static_overrides

    assert effective_static_overrides(None) == DEFAULT_INCAR_STATIC
    got = effective_static_overrides({"ENCUT": 520, "KSPACING": None, "lreal": "Auto"})
    assert got == {"ENCUT": 520, "EDIFF": 1e-5, "EDIFFG": 1e-4, "LREAL": "Auto"}


def test_staleness_ignores_a_missing_or_spelled_out_default_incar_static(campaign_dict):
    new = CampaignConfig.from_dict(campaign_dict).resolve().dump()
    recorded = copy.deepcopy(new)
    del recorded["incar_static"]  # written by 0.1.1, before the section existed
    assert config_differences(new, recorded) == []
    campaign_dict["incar_static"] = {"KSPACING": 0.25}  # the default, spelled out
    spelled = CampaignConfig.from_dict(campaign_dict).resolve().dump()
    assert config_differences(spelled, recorded) == []


def test_staleness_catches_a_changed_label_incar(campaign_dict):
    recorded = CampaignConfig.from_dict(campaign_dict).resolve().dump()
    campaign_dict["incar_static"] = {"KSPACING": 0.2}
    new = CampaignConfig.from_dict(campaign_dict).resolve().dump()
    assert config_differences(new, recorded) == ["incar_static.KSPACING"]


# -- magmom.moments ------------------------------------------------------------
def test_magmom_moments_parse_and_validate(campaign_dict):
    campaign_dict["magmom"]["moments"] = {"Fe": 2.8, "C": 0}
    cfg = CampaignConfig.from_dict(campaign_dict).resolve()
    assert cfg.magmom.moments == {"Fe": 2.8, "C": 0}
    campaign_dict["magmom"]["moments"] = {"Xx": 1.0}
    with pytest.raises(ConfigError, match="magmom.moments"):
        CampaignConfig.from_dict(campaign_dict).resolve()
    campaign_dict["magmom"]["moments"] = {"Fe": "big"}
    with pytest.raises(ConfigError, match="magmom.moments.Fe"):
        CampaignConfig.from_dict(campaign_dict).resolve()


# -- site files ----------------------------------------------------------------
def test_site_file_merges_over_the_campaign(campaign_dict, tmp_path):
    camp = tmp_path / "camp.yaml"
    camp.write_text(yaml.safe_dump(campaign_dict))
    site = tmp_path / "site.yaml"
    site.write_text(
        yaml.safe_dump(
            {
                "slurm": {"partition": "gpu", "account": "proj1"},
                "vasp": {
                    "binary": "/sw/vasp_std",
                    "modules": ["a/1"],
                    "potcar_config": "/sw/potcars.cfg",
                },
            }
        )
    )
    cfg = CampaignConfig.from_yaml(camp, [site]).resolve()
    assert cfg.slurm.partition == "gpu" and cfg.slurm.account == "proj1"
    assert cfg.slurm.ntasks == 64  # untouched campaign value survives a partial `slurm` section
    assert cfg.vasp.binary == "/sw/vasp_std" and cfg.vasp.modules == ["a/1"]
    assert cfg.vasp.potcar_config == "/sw/potcars.cfg"
    assert cfg.incar["ENCUT"] == 400  # physics untouched
    # a second site file wins over the first; CLI overrides still beat both
    site2 = tmp_path / "site2.yaml"
    site2.write_text(yaml.safe_dump({"slurm": {"partition": "debug"}}))
    assert CampaignConfig.from_yaml(camp, [site, site2]).resolve().slurm.partition == "debug"
    assert (
        CampaignConfig.from_yaml(camp, [site, site2])
        .resolve({"slurm.partition": "x"})
        .slurm.partition
        == "x"
    )


def test_missing_site_file_is_a_config_error(campaign_dict, tmp_path):
    camp = tmp_path / "camp.yaml"
    camp.write_text(yaml.safe_dump(campaign_dict))
    with pytest.raises(ConfigError, match="site config not found"):
        CampaignConfig.from_yaml(camp, [tmp_path / "nope.yaml"])


# -- environment expansion -----------------------------------------------------
def test_environment_variables_expand_in_machine_sections(campaign_dict, monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_STD", "/opt/vasp/6.4/bin/vasp_std")
    monkeypatch.setenv("MYPART", "s.cmmg")
    monkeypatch.setenv("HOME", str(tmp_path))
    campaign_dict["vasp"]["binary"] = "${VASP_STD}"
    campaign_dict["vasp"]["modules"] = ["intel/$MYPART"]
    campaign_dict["vasp"]["potcar_config"] = "~/potcars/config"
    campaign_dict["slurm"]["partition"] = "$MYPART"
    campaign_dict["incar"]["SYSTEM"] = "$NOT_EXPANDED"  # incar is physics, left alone
    cfg = CampaignConfig.from_dict(campaign_dict).resolve()
    assert cfg.vasp.binary == "/opt/vasp/6.4/bin/vasp_std"
    assert cfg.vasp.modules == ["intel/s.cmmg"]
    assert cfg.vasp.potcar_config == str(tmp_path / "potcars/config")
    assert cfg.slurm.partition == "s.cmmg"
    assert cfg.incar["SYSTEM"] == "$NOT_EXPANDED"
    assert cfg.dump()["vasp"]["binary"] == "/opt/vasp/6.4/bin/vasp_std"  # recorded expanded


def test_unset_environment_variable_is_an_error_naming_the_key(campaign_dict, monkeypatch):
    monkeypatch.delenv("DEFINITELY_UNSET_VAR", raising=False)
    campaign_dict["vasp"]["binary"] = "${DEFINITELY_UNSET_VAR}/vasp_std"
    with pytest.raises(ConfigError, match=r"`vasp.binary` references environment variable"):
        CampaignConfig.from_dict(campaign_dict).resolve()


def test_placeholders_are_not_expanded(campaign_dict):
    campaign_dict["vasp"]["binary"] = "<EDIT: $HOME/vasp_std>"
    assert CampaignConfig.from_dict(campaign_dict).resolve().vasp.binary == "<EDIT: $HOME/vasp_std>"


# -- staleness: keys an older recording lacks ----------------------------------
def test_keys_added_after_a_recording_are_not_stale_when_default(campaign_dict):
    new = CampaignConfig.from_dict(campaign_dict).resolve().dump()
    recorded = copy.deepcopy(new)
    del recorded["vasp"]["potcar_config"]
    del recorded["magmom"]["moments"]
    assert config_differences(new, recorded) == []
    campaign_dict["magmom"]["moments"] = {"Fe": 2.5}
    changed = CampaignConfig.from_dict(campaign_dict).resolve().dump()
    assert config_differences(changed, recorded) == ["magmom.moments"]
