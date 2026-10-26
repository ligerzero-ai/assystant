"""Bundled templates (``assystant example``) and the portable config defaults.

These run against the installed package, so the template must be found through
importlib.resources, never relative to a repository checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from pymatgen.core import Lattice, Structure

from assystant.cli import main
from assystant.config import CampaignConfig, ConfigError, StaleConfigError
from assystant.examples import (
    list_templates,
    placeholder_fields,
    template_description,
    template_text,
    write_template,
)
from assystant.generate import write_system
from assystant.submit import submit_campaign

from conftest import record_for


def fcc_al(a: float = 4.04) -> Structure:
    return Structure(
        Lattice.cubic(a),
        ["Al"] * 4,
        [[0, 0, 0], [0, 0.5, 0.5], [0.5, 0, 0.5], [0.5, 0.5, 0]],
    )


# -- template lookup ------------------------------------------------------
def test_al_tutorial_is_bundled_with_the_package():
    assert "Al_tutorial" in list_templates()
    assert template_text("Al_tutorial") == template_text("Al_tutorial.yaml")
    assert template_description("Al_tutorial")


def test_alcu_tutorial_is_three_systems_sharing_the_al_scan(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    alcu = CampaignConfig.from_dict(
        yaml.safe_load(template_text("AlCu_tutorial"))
    ).resolve()
    al = CampaignConfig.from_dict(
        yaml.safe_load(template_text("Al_tutorial"))
    ).resolve()
    assert alcu.system.systems == [["Al"], ["Cu"], ["Al", "Cu"]]
    assert alcu.system_names() == ["Al", "Cu", "Al_Cu"]
    # Same scan parameters and seed as Al_tutorial, so the Al system reproduces
    # the worked example's hashes byte for byte.
    for key in (
        "min_atoms",
        "max_atoms",
        "spacegroups",
        "strain_min",
        "strain_max",
        "seed",
    ):
        assert getattr(alcu.system, key) == getattr(al.system, key), key
    assert alcu.incar == al.incar
    assert placeholder_fields(alcu) == ["slurm.partition", "vasp.binary"]


def test_unknown_template_names_the_available_ones():
    with pytest.raises(KeyError, match="Al_tutorial"):
        template_text("no_such_template")


def test_templates_carry_no_site_specific_paths():
    for name in list_templates():
        data = yaml.safe_load(template_text(name))
        assert "/cmmc" not in template_text(name)
        assert "env" not in (data.get("slurm") or {})
        root = data.get("root")
        assert root is None or not Path(root).is_absolute()


# -- defaults that make the template portable -----------------------------
def test_template_resolves_relative_to_cwd_with_running_interpreter(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    cfg = CampaignConfig.from_dict(
        yaml.safe_load(template_text("Al_tutorial"))
    ).resolve()
    assert Path(cfg.root) == (tmp_path / "Al_campaign").resolve()
    assert cfg.slurm.env is None
    assert cfg.slurm.python_executable() == sys.executable
    assert cfg.system.systems == [["Al"]]


def test_missing_root_defaults_to_cwd(campaign_dict, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    del campaign_dict["root"]
    cfg = CampaignConfig.from_dict(campaign_dict).resolve()
    assert Path(cfg.root) == tmp_path.resolve()
    assert cfg.system_dir(["Fe"]) == tmp_path.resolve() / "Fe"


def test_explicit_root_is_unchanged(campaign_dict, tmp_path):
    cfg = CampaignConfig.from_dict(campaign_dict).resolve()
    assert cfg.root == str((tmp_path / "camp").resolve())
    assert cfg.slurm.python_executable() == "/opt/conda/envs/assystant/bin/python"


def test_placeholder_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = CampaignConfig.from_dict(
        yaml.safe_load(template_text("Al_tutorial"))
    ).resolve()
    assert placeholder_fields(cfg) == ["slurm.partition", "vasp.binary"]
    filled = cfg.resolve({"slurm.partition": "debug", "vasp.binary": "/opt/vasp_std"})
    assert placeholder_fields(filled) == []


# -- dry run vs real run on a template ------------------------------------
@pytest.fixture
def template_campaign(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = CampaignConfig.from_dict(
        yaml.safe_load(template_text("Al_tutorial"))
    ).resolve()
    write_system(cfg.root, ["Al"], [record_for(fcc_al(), spacegroup=225)])
    return cfg


def test_template_dry_run_renders_with_running_interpreter(template_campaign):
    stats = submit_campaign(template_campaign, dry_run=True)
    assert len(stats["rendered"]) == 1
    script = Path(stats["rendered"][0]).read_text()
    assert f"{sys.executable} -m assystant.runner" in script
    assert "/cmmc" not in script


def test_real_run_refuses_placeholders(template_campaign):
    submitted = []
    with pytest.raises(ConfigError, match="slurm.partition, vasp.binary"):
        submit_campaign(
            template_campaign, queued_dirs=set(), submit_fn=submitted.append
        )
    assert submitted == []


def test_real_run_proceeds_once_placeholders_are_filled(template_campaign):
    cfg = template_campaign.resolve(
        {"slurm.partition": "debug", "vasp.binary": "/opt/vasp/bin/vasp_std"}
    )
    submitted = []
    stats = submit_campaign(cfg, queued_dirs=set(), submit_fn=submitted.append)
    assert stats["submitted"] == 1 and len(submitted) == 1


# -- the CLI --------------------------------------------------------------
def test_cli_example_lists_templates(capsys):
    assert main(["example"]) == 0
    assert "Al_tutorial" in capsys.readouterr().out


def test_cli_example_prints_to_stdout(capsys):
    assert main(["example", "Al_tutorial"]) == 0
    assert capsys.readouterr().out == template_text("Al_tutorial")


def test_cli_example_writes_file_and_refuses_overwrite(tmp_path, capsys):
    dest = tmp_path / "camp.yaml"
    assert main(["example", "Al_tutorial", "-o", str(dest)]) == 0
    assert dest.read_text() == template_text("Al_tutorial")
    assert main(["example", "Al_tutorial", "-o", str(dest)]) == 2
    assert "already exists" in capsys.readouterr().err
    dest.write_text("changed")
    assert main(["example", "Al_tutorial", "-o", str(dest), "--force"]) == 0
    assert dest.read_text() == template_text("Al_tutorial")


def test_cli_example_unknown_name(capsys):
    assert main(["example", "nope"]) == 2
    assert "Al_tutorial" in capsys.readouterr().err


def test_write_template_creates_parent_dirs(tmp_path):
    dest = write_template("Al_tutorial", tmp_path / "a" / "b.yaml")
    assert dest.is_file()


def test_cli_run_dry_run_from_template_needs_no_flags(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["example", "Al_tutorial", "-o", "camp.yaml"]) == 0
    cfg = CampaignConfig.from_yaml("camp.yaml").resolve()
    write_system(cfg.root, ["Al"], [record_for(fcc_al(), spacegroup=225)])
    assert main(["run", "camp.yaml", "--dry-run", "--limit", "1"]) == 0
    assert "rendered 1" in capsys.readouterr().out
    assert main(["run", "camp.yaml", "--limit", "1"]) == 2
    assert "placeholder" in capsys.readouterr().err


def test_filling_placeholders_after_dry_run_is_not_stale(template_campaign):
    submit_campaign(template_campaign, dry_run=True)
    cfg = template_campaign.resolve(
        {"slurm.partition": "debug", "vasp.binary": "/opt/vasp/bin/vasp_std"}
    )
    submitted = []
    stats = submit_campaign(cfg, queued_dirs=set(), submit_fn=submitted.append)
    assert stats["submitted"] == 1 and len(submitted) == 1
    # ...but a real value, once recorded, is guarded as before.
    with pytest.raises(StaleConfigError, match="vasp.binary"):
        submit_campaign(
            cfg.resolve({"vasp.binary": "/other/vasp_std"}),
            queued_dirs=set(),
            submit_fn=submitted.append,
        )
