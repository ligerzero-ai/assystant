"""sbatch rendering and idempotent submission. No scheduler is ever touched."""

from __future__ import annotations

from pathlib import Path

import pytest

from assystant.cli import main as cli_main
from assystant.config import StaleConfigError
from assystant.submit import (
    JOB_SCRIPT_NAME,
    RESULT_FILENAME,
    build_vasp_command,
    render_sbatch,
    submit_campaign,
)


class Recorder:
    """Stand-in for sbatch."""

    def __init__(self):
        self.calls = []

    def __call__(self, script_path):
        self.calls.append(str(script_path))


# -- rendering ------------------------------------------------------------
def test_dry_run_renders_a_script_without_submitting(generated_campaign):
    cfg, manifest = generated_campaign
    recorder = Recorder()

    stats = submit_campaign(cfg, dry_run=True, submit_fn=recorder, queued_dirs=set())

    assert recorder.calls == [], "dry run must not submit"
    assert stats["submitted"] == 0
    assert len(stats["rendered"]) == len(manifest) == 2

    run_dir = cfg.root_path / "Fe" / manifest.iloc[0]["dirname"]
    script = (run_dir / JOB_SCRIPT_NAME).read_text()

    assert script.startswith("#!/bin/bash\n")
    assert "#SBATCH --partition=s.cmmg" in script
    assert "#SBATCH --ntasks=64" in script
    assert "#SBATCH --time=48:00:00" in script
    # --chdir replaces the os.chdir the old scripts relied on, and is what makes
    # `squeue -o %Z` report this directory for the queued-skip.
    assert f"#SBATCH --chdir={run_dir}" in script
    assert f"#SBATCH --output={run_dir / 'assystant.out'}" in script
    # The runner is invoked with absolute paths only.
    assert "/opt/conda/envs/assystant/bin/python -m assystant.runner" in script
    assert f"--run-dir {run_dir}" in script
    assert f"--config {cfg.root_path / 'assystant_config.yaml'}" in script
    assert f"--poscar {manifest.iloc[0]['poscar']}" in script
    assert "--system Fe" in script
    assert "os.chdir" not in script
    assert " ." not in script.replace("00", "")  # no relative-path arguments


def test_dry_run_writes_the_config_so_the_rendered_script_is_runnable(generated_campaign):
    """A dry-run script invokes `python -m assystant.runner --config <path> ...`.

    If that config is never written, running the script by hand -- exactly
    what --dry-run exists to let a user do before committing to the queue --
    fails immediately with ConfigError: campaign config not found. Writing the
    resolved config is inert (a YAML file, not a scheduler action), so
    --dry-run can and should do it.
    """
    import re

    from assystant.config import CampaignConfig

    cfg, manifest = generated_campaign
    stats = submit_campaign(cfg, dry_run=True, submit_fn=Recorder(), queued_dirs=set())

    run_dir = cfg.root_path / "Fe" / manifest.iloc[0]["dirname"]
    script = (run_dir / JOB_SCRIPT_NAME).read_text()
    match = re.search(r"--config (\S+)", script)
    assert match, "rendered script has no --config argument"
    config_path = match.group(1)

    assert config_path == str(cfg.root_path / "assystant_config.yaml")
    assert Path(config_path).is_file(), "dry-run left a script whose --config path does not exist"
    # And it is not just present but actually loadable, as `assystant.runner` would.
    reloaded = CampaignConfig.from_yaml(config_path).resolve()
    assert reloaded.incar["ENCUT"] == cfg.incar["ENCUT"]
    assert stats["rendered"]


def test_rendered_paths_are_all_absolute(generated_campaign):
    cfg, manifest = generated_campaign
    submit_campaign(cfg, dry_run=True, submit_fn=Recorder(), queued_dirs=set())
    script = (cfg.root_path / "Fe" / manifest.iloc[0]["dirname"] / JOB_SCRIPT_NAME).read_text()
    for line in script.splitlines():
        for token in line.split():
            if token.startswith("--chdir=") or token.startswith("--output="):
                assert token.split("=", 1)[1].startswith("/")


def test_vasp_command_uses_custodian_when_enabled(cfg):
    command = build_vasp_command(cfg)
    assert "custodian_vasp.py" in command
    assert "module load intel/2024.0" in command
    assert "srun -c 1 -n 64 --hint=nomultithread /opt/vasp/bin/vasp_std" in command
    assert "/opt/conda/envs/pymatgen/bin/python custodian_vasp.py" in command


def test_vasp_command_without_custodian_is_a_bare_srun(cfg):
    command = build_vasp_command(cfg.resolve({"vasp.custodian": False}))
    assert "custodian" not in command
    assert command.strip().endswith("srun -c 1 -n 64 --hint=nomultithread /opt/vasp/bin/vasp_std >> vasp.log")


# -- vasp.launcher ----------------------------------------------------------
def test_launcher_default_is_the_slurm_srun_form(cfg):
    """`vasp.launcher: null` (the dataclass default) means SLURM's own srun."""
    assert cfg.vasp.launcher is None
    command = build_vasp_command(cfg.resolve({"vasp.custodian": False}))
    lines = [line for line in command.splitlines() if "vasp_std" in line]
    assert lines == ["srun -c 1 -n 64 --hint=nomultithread /opt/vasp/bin/vasp_std >> vasp.log"]


def test_launcher_explicit_value_is_used_verbatim(cfg):
    changed = cfg.resolve({"vasp.custodian": False, "vasp.launcher": "mpirun -n 1"})
    command = build_vasp_command(changed)
    lines = [line for line in command.splitlines() if "vasp_std" in line]
    assert lines == ["mpirun -n 1 /opt/vasp/bin/vasp_std >> vasp.log"]
    assert "srun" not in command


def test_launcher_empty_string_yields_a_bare_binary_invocation(cfg):
    changed = cfg.resolve({"vasp.custodian": False, "vasp.launcher": ""})
    command = build_vasp_command(changed)
    lines = [line for line in command.splitlines() if "vasp_std" in line]
    assert lines == ["/opt/vasp/bin/vasp_std >> vasp.log"]
    assert "srun" not in command


def test_launcher_ntasks_placeholder_is_substituted(cfg):
    changed = cfg.resolve({
        "vasp.custodian": False,
        "vasp.launcher": "mpirun -n {ntasks} --bind-to core",
        "slurm.ntasks": 32,
    })
    command = build_vasp_command(changed)
    lines = [line for line in command.splitlines() if "vasp_std" in line]
    assert lines == ["mpirun -n 32 --bind-to core /opt/vasp/bin/vasp_std >> vasp.log"]


def test_render_sbatch_honours_extra_directives_and_account(cfg, tmp_path):
    changed = cfg.resolve({"slurm.account": "pawsey0380",
                           "slurm.extra_directives": ["--reservation=benchmarking"]})
    script = render_sbatch(
        changed, run_dir=tmp_path / "d", poscar=tmp_path / "p.vasp", system="Fe",
        job_name="j", config_path=tmp_path / "c.yaml",
    )
    assert "#SBATCH --account=pawsey0380" in script
    assert "#SBATCH --reservation=benchmarking" in script


# -- submission and idempotency ------------------------------------------
def test_real_run_submits_every_structure_once(generated_campaign):
    cfg, manifest = generated_campaign
    recorder = Recorder()
    stats = submit_campaign(cfg, submit_fn=recorder, queued_dirs=set())
    assert stats["submitted"] == 2
    assert len(recorder.calls) == 2
    assert all(call.endswith(JOB_SCRIPT_NAME) for call in recorder.calls)


def test_already_queued_structures_are_skipped(generated_campaign):
    cfg, manifest = generated_campaign
    queued = {str((cfg.root_path / "Fe" / manifest.iloc[0]["dirname"]).resolve())}
    recorder = Recorder()

    stats = submit_campaign(cfg, submit_fn=recorder, queued_dirs=queued)

    assert stats["skipped_queued"] == 1
    assert stats["submitted"] == 1
    assert len(recorder.calls) == 1
    assert manifest.iloc[0]["dirname"] not in recorder.calls[0]


def test_finished_structures_are_skipped(generated_campaign):
    cfg, manifest = generated_campaign
    done_dir = cfg.root_path / "Fe" / manifest.iloc[1]["dirname"]
    done_dir.mkdir(parents=True, exist_ok=True)
    (done_dir / RESULT_FILENAME).write_text("results")
    recorder = Recorder()

    stats = submit_campaign(cfg, submit_fn=recorder, queued_dirs=set())

    assert stats["skipped_done"] == 1
    assert stats["submitted"] == 1
    assert manifest.iloc[1]["dirname"] not in recorder.calls[0]


def test_limit_caps_the_number_processed(generated_campaign):
    cfg, _ = generated_campaign
    recorder = Recorder()
    stats = submit_campaign(cfg, submit_fn=recorder, queued_dirs=set(), limit=1)
    assert stats["submitted"] == 1
    assert len(recorder.calls) == 1


def test_missing_manifest_is_reported_not_crashed(cfg, capsys):
    stats = submit_campaign(cfg, dry_run=True, submit_fn=Recorder(), queued_dirs=set())
    assert stats["missing"] == 1
    assert stats["rendered"] == []
    assert "gen" in capsys.readouterr().out


# -- staleness / from-scratch --------------------------------------------
def test_changed_incar_blocks_submission(generated_campaign):
    cfg, _ = generated_campaign
    submit_campaign(cfg, submit_fn=Recorder(), queued_dirs=set())

    recorder = Recorder()
    with pytest.raises(StaleConfigError, match="incar.ENCUT"):
        submit_campaign(cfg.resolve({"incar.ENCUT": 500}), submit_fn=recorder, queued_dirs=set())
    assert recorder.calls == []


def test_from_scratch_wipes_the_run_dir_including_the_cache(generated_campaign):
    """--from-scratch must delete run_dir IN FULL.

    executorlib's cache is keyed on node lexical path with no input hashing, so
    leaving executorlib_cache in place after an INCAR change would serve results
    computed under the old INCAR.
    """
    cfg, manifest = generated_campaign
    submit_campaign(cfg, submit_fn=Recorder(), queued_dirs=set())

    run_dir = cfg.root_path / "Fe" / manifest.iloc[0]["dirname"]
    cache = run_dir / "executorlib_cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "stale.h5").write_text("results from the old INCAR")
    (run_dir / "ISIF7").mkdir(exist_ok=True)
    (run_dir / RESULT_FILENAME).write_text("old results")

    recorder = Recorder()
    submit_campaign(
        cfg.resolve({"incar.ENCUT": 500}), submit_fn=recorder, queued_dirs=set(),
        from_scratch=True,
    )

    assert not cache.exists(), "executorlib_cache survived --from-scratch"
    assert not (run_dir / "ISIF7").exists()
    assert not (run_dir / RESULT_FILENAME).exists()
    assert (run_dir / JOB_SCRIPT_NAME).is_file()
    assert len(recorder.calls) == 2, "--from-scratch must not skip finished structures"


def test_from_scratch_records_the_new_config(generated_campaign):
    from assystant.config import load_recorded_config

    cfg, _ = generated_campaign
    submit_campaign(cfg, submit_fn=Recorder(), queued_dirs=set())
    submit_campaign(cfg.resolve({"incar.ENCUT": 500}), submit_fn=Recorder(),
                    queued_dirs=set(), from_scratch=True)
    assert load_recorded_config(cfg.root)["incar"]["ENCUT"] == 500


# -- CLI wiring -----------------------------------------------------------
def test_cli_dry_run_renders_without_submitting(generated_campaign, tmp_path, monkeypatch, capsys):
    import yaml

    import assystant.submit as submit_mod

    cfg, manifest = generated_campaign
    path = tmp_path / "camp.yaml"
    path.write_text(yaml.safe_dump(cfg.dump()))

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the scheduler was contacted during --dry-run")

    monkeypatch.setattr(submit_mod, "_sbatch", explode)
    monkeypatch.setattr(submit_mod, "squeue_workdirs", explode)

    assert cli_main(["run", str(path), "--dry-run"]) == 0
    script = cfg.root_path / "Fe" / manifest.iloc[0]["dirname"] / JOB_SCRIPT_NAME
    assert script.is_file()
    assert "rendered 2" in capsys.readouterr().out


def test_cli_reports_staleness_as_an_error_exit(generated_campaign, tmp_path, monkeypatch):
    import yaml

    import assystant.submit as submit_mod

    cfg, _ = generated_campaign
    recorder = Recorder()
    monkeypatch.setattr(submit_mod, "_sbatch", recorder)
    monkeypatch.setattr(submit_mod, "squeue_workdirs", lambda: set())

    path = tmp_path / "camp.yaml"
    path.write_text(yaml.safe_dump(cfg.dump()))
    assert cli_main(["run", str(path)]) == 0

    stale = dict(cfg.dump())
    stale["incar"] = {**stale["incar"], "ENCUT": 500}
    path.write_text(yaml.safe_dump(stale))
    before = len(recorder.calls)
    assert cli_main(["run", str(path)]) == 2
    assert len(recorder.calls) == before
