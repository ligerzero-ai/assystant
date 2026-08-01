"""sbatch rendering, squeue introspection, idempotent submission.

One SLURM job per structure, as in the pre-2026 campaigns: a single N-task
allocation that runs the whole ASSYST chain internally.  Two departures from
``calculate_assyst.py``:

* **No ``os.chdir`` anywhere.**  Every path this module writes or renders is
  absolute.  The job's working directory is set by ``#SBATCH --chdir``, which
  also keeps ``squeue -o %Z`` reporting the structure directory -- that is what
  the "already queued" skip matches on.
* **Config staleness guard.**  ``run`` records the resolved config in the
  campaign root and refuses to proceed if a later invocation disagrees with it,
  because executorlib's cache is keyed on node lexical path with no input
  hashing (see :mod:`assystant.config`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import config as config_mod
from .config import (
    CampaignConfig,
    StaleConfigError,
    load_recorded_config,
    recorded_config_path,
    system_name,
)
from .generate import read_manifest

__all__ = [
    "RESULT_FILENAME",
    "JOB_SCRIPT_NAME",
    "build_vasp_command",
    "render_sbatch",
    "squeue_workdirs",
    "guard_config",
    "submit_campaign",
]

#: Written by ``run_ASSYST_on_structure``; its presence marks a finished structure.
RESULT_FILENAME = "df_ASSYST_jobs.pkl"
JOB_SCRIPT_NAME = "assystant_job.sh"

_CUSTODIAN_WRAPPER = '''cat > custodian_vasp.py << 'PYEOF'
import sys
from custodian.custodian import Custodian
from custodian.vasp.handlers import (
    VaspErrorHandler,
    NonConvergingErrorHandler,
    FrozenJobErrorHandler,
)
from custodian.vasp.jobs import VaspJob

output_filename = "vasp.log"
handlers = [
    VaspErrorHandler(output_filename=output_filename),
    NonConvergingErrorHandler(),
    FrozenJobErrorHandler(output_filename=output_filename),
]
jobs = [VaspJob(sys.argv[1:], output_file=output_filename, suffix="")]
Custodian(handlers, jobs, max_errors={max_errors}, polling_time_step=3, monitor_freq=1).run()
PYEOF
'''


def build_vasp_command(cfg: CampaignConfig, ntasks: int | None = None) -> str:
    """The shell snippet each VASP node runs, with ``cwd`` set to its work directory.

    With ``vasp.custodian: true`` the snippet writes a custodian wrapper into
    the work directory and drives VASP through it (matching
    ``calculate_assyst.py``); otherwise it invokes ``srun`` directly.
    """
    ntasks = int(cfg.slurm.ntasks if ntasks is None else ntasks)
    lines = [f"module load {module}" for module in (cfg.vasp.modules or [])]
    if cfg.vasp.launcher is None:
        launcher = f"srun -c 1 -n {ntasks} --hint=nomultithread"
    else:
        launcher = str(cfg.vasp.launcher).format(ntasks=ntasks)
    invocation = f"{launcher} {cfg.vasp.binary}".strip()
    if cfg.vasp.custodian:
        python = cfg.vasp.custodian_python or cfg.slurm.python_executable()
        lines.append(_CUSTODIAN_WRAPPER.format(max_errors=int(cfg.vasp.max_errors)))
        lines.append(f"{python} custodian_vasp.py {invocation} >> vasp.log")
    else:
        lines.append(f"{invocation} >> vasp.log")
    return "\n".join(lines)


def render_sbatch(
    cfg: CampaignConfig,
    *,
    run_dir: Path,
    poscar: Path,
    system: str,
    job_name: str,
    config_path: Path,
) -> str:
    """Render the sbatch script for one structure. All paths absolute."""
    run_dir = Path(run_dir).resolve()
    directives = [
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={cfg.slurm.partition}",
        f"#SBATCH --ntasks={cfg.slurm.ntasks}",
        f"#SBATCH --cpus-per-task={cfg.slurm.cpus_per_task}",
        f"#SBATCH --time={cfg.slurm.time}",
        # --chdir keeps `squeue -o %Z` reporting this structure's directory,
        # which is what the already-queued skip matches on -- and it replaces
        # the os.chdir that calculate_assyst.py relied on.
        f"#SBATCH --chdir={run_dir}",
        f"#SBATCH --output={run_dir / 'assystant.out'}",
        f"#SBATCH --error={run_dir / 'assystant.err'}",
        "#SBATCH --get-user-env=L",
        f"#SBATCH --mem-per-cpu={cfg.slurm.mem_per_cpu}",
        "#SBATCH --hint=nomultithread",
    ]
    if cfg.slurm.account:
        directives.append(f"#SBATCH --account={cfg.slurm.account}")
    directives.extend(f"#SBATCH {extra}" for extra in (cfg.slurm.extra_directives or []))

    runner = " ".join(
        [
            cfg.slurm.python_executable(),
            "-m",
            "assystant.runner",
            f"--config {Path(config_path).resolve()}",
            f"--poscar {Path(poscar).resolve()}",
            f"--run-dir {run_dir}",
            f"--system {system}",
        ]
    )
    body = "\n".join(
        [
            "#!/bin/bash",
            *directives,
            "",
            "module purge",
            "",
            "# Run the ASSYST graph for exactly this structure. run_dir is absolute;",
            "# executorlib's cache lives at <run_dir>/executorlib_cache so it is",
            "# scoped to one structure and cannot be shared between two.",
            runner,
            "",
        ]
    )
    return body


def squeue_workdirs() -> set[str]:
    """Work directories of the current user's queued/running jobs (``squeue -o %Z``)."""
    user = os.environ.get("USER") or ""
    try:
        out = subprocess.check_output(
            ["squeue", "-u", user, "--noheader", "-o", "%Z"], text=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        print(f"Warning: unable to query squeue ({exc}); no jobs will be skipped as queued")
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def guard_config(cfg: CampaignConfig, *, from_scratch: bool = False, write: bool = True):
    """Compare the resolved config against the campaign's recorded one.

    Returns the path the resolved config was (or would be) written to.  Raises
    :class:`StaleConfigError` on a calculation-affecting mismatch unless
    ``from_scratch`` is set, because the executorlib cache under an existing
    ``run_dir`` would otherwise serve results computed with the old settings.
    """
    root = cfg.root_path
    resolved = cfg.dump()
    recorded = load_recorded_config(root)
    path = recorded_config_path(root)
    if recorded is not None:
        differences = config_mod.config_differences(resolved, recorded)
        if differences and not from_scratch:
            raise StaleConfigError(
                "resolved campaign config differs from the one recorded at "
                f"{path} at: {', '.join(differences)}.\n"
                "executorlib's cache is keyed on node lexical path with no input "
                "hashing, so re-running an existing run_dir under changed settings "
                "would silently return results computed with the OLD ones. Either "
                "revert the change, point `root` at a fresh campaign directory, or "
                "pass --from-scratch (which DELETES each structure's run_dir in "
                "full, including executorlib_cache)."
            )
    if write:
        cfg.dump_to(path)
    return path


def _sbatch(script_path: Path) -> None:
    subprocess.run(["sbatch", str(script_path)], check=True)


def submit_campaign(
    cfg: CampaignConfig,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    from_scratch: bool = False,
    queued_dirs=None,
    submit_fn=None,
) -> dict:
    """Render (and unless ``dry_run``, submit) one sbatch job per structure.

    Idempotent: a structure is skipped when its directory is already the working
    directory of a queued/running job, or already holds ``df_ASSYST_jobs.pkl``.

    ``queued_dirs`` and ``submit_fn`` are injection seams -- the defaults query
    ``squeue`` and call ``sbatch``; tests pass their own so no scheduler is
    touched.  ``--dry-run`` skips the ``squeue`` query, but still writes the
    resolved provenance config (:func:`guard_config` with ``write=True``): it
    is inert -- a YAML file, not a scheduler action -- and without it a
    rendered script's ``--config`` argument points at a file that does not
    exist, so a user cannot run it by hand to test before committing to the
    queue (which is exactly what ``--dry-run`` is for). The staleness check
    itself already runs unconditionally, dry run or not, so writing afterwards
    cannot mask a real conflict.

    A dry run must never destroy data: ``--dry-run --from-scratch`` does NOT
    delete anything (real ``--from-scratch`` normally wipes each run_dir in
    full, including ``executorlib_cache`` and any finished result, to clear
    stale cached results after a config change -- see below). Instead it
    counts, in ``stats["would_delete"]``, the run_dirs that exist and would
    have been wiped, so the summary can say so without touching them.
    """
    config_path = guard_config(cfg, from_scratch=from_scratch, write=True)
    if queued_dirs is None:
        queued_dirs = set() if dry_run else squeue_workdirs()
    submit_fn = submit_fn or _sbatch

    stats = {
        "submitted": 0, "skipped_queued": 0, "skipped_done": 0, "missing": 0,
        "would_delete": 0, "rendered": [],
    }
    submitted_total = 0

    for species in cfg.system.systems:
        name = system_name(species)
        manifest = read_manifest(cfg.root, species)
        if manifest is None:
            print(f"No manifest for {name}; run `assystant gen` first")
            stats["missing"] += 1
            continue

        for _, row in manifest.iterrows():
            if limit is not None and submitted_total >= limit:
                break
            run_dir = (cfg.root_path / name / str(row["dirname"])).resolve()
            if str(run_dir) in queued_dirs:
                stats["skipped_queued"] += 1
                continue
            if from_scratch:
                if run_dir.is_dir():
                    if dry_run:
                        # Report, don't destroy: a dry run must never delete
                        # data, and finished DFT results can represent weeks
                        # of compute.
                        stats["would_delete"] += 1
                    else:
                        # Wipe the run_dir IN FULL, including executorlib_cache
                        # -- the cache is keyed on lexical path only, so
                        # leaving it would serve stale results after an INCAR
                        # change.
                        shutil.rmtree(run_dir)
            elif (run_dir / RESULT_FILENAME).is_file():
                stats["skipped_done"] += 1
                continue
            # Harmless even when the dir already exists (dry-run --from-scratch
            # above, or a re-rendered but not yet finished structure): this
            # only ever creates an empty directory to hold the rendered
            # script, never removes or overwrites prior contents.
            run_dir.mkdir(parents=True, exist_ok=True)

            script = render_sbatch(
                cfg,
                run_dir=run_dir,
                poscar=Path(row["poscar"]),
                system=name,
                job_name=f"assyst_{name}_{int(row['index']):05d}",
                config_path=config_path,
            )
            script_path = run_dir / JOB_SCRIPT_NAME
            script_path.write_text(script)
            script_path.chmod(0o755)
            stats["rendered"].append(str(script_path))
            if not dry_run:
                submit_fn(script_path)
                stats["submitted"] += 1
            submitted_total += 1

    return stats
