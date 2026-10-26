"""In-job entry point: run the ASSYST graph for exactly one structure.

This is what the rendered sbatch script invokes.  It is deliberately tiny --
read the recorded campaign config, read one POSCAR, build the INCAR, and hand
everything to ``run_ASSYST_on_structure``.

Nothing here calls ``os.chdir``.  ``RunConfig(run_dir=...)`` and ``job_name``
are absolute paths derived from the content-hashed structure directory, so
executorlib's cache lands at ``<run_dir>/executorlib_cache``, scoped to exactly
one structure.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import CampaignConfig, ConfigError
from .incar import build_incar, build_static_incar
from .submit import RESULT_FILENAME, build_vasp_command

__all__ = [
    "assyst_kwargs",
    "run_structure",
    "main",
    "STATIC_INCAR_MIN_VERSION",
    "apply_potcar_config",
]


def apply_potcar_config(cfg: CampaignConfig, environ=os.environ) -> str | None:
    """Point ``pyiron_workflow_vasp`` at ``vasp.potcar_config``, if set.

    The library reads ``PYIRON_VASP_CONFIG`` into a module constant at import
    time, so this must run before anything imports it -- which is why it is
    called first thing in :func:`main`, and why the rendered job script exports
    the same variable (for running the script by hand).
    """
    path = cfg.vasp.potcar_config
    if not path:
        return None
    environ["PYIRON_VASP_CONFIG"] = str(path)
    return str(path)


#: First pyiron_workflow_assyst release whose ``run_ASSYST_on_structure`` takes
#: ``static_incar``.  Older releases derive the statics INCAR themselves, from
#: ``incar`` plus the same defaults ``incar_static`` layers over, so a config
#: that leaves ``incar_static`` empty runs identically on either.
STATIC_INCAR_MIN_VERSION = "0.2.2"


def supports_static_incar() -> bool:
    """Whether the installed ``run_ASSYST_on_structure`` accepts ``static_incar``."""
    import inspect

    from pyiron_workflow_assyst import run_ASSYST_on_structure

    return "static_incar" in inspect.signature(run_ASSYST_on_structure).parameters


def assyst_kwargs(
    cfg: CampaignConfig,
    structure,
    run_dir: Path,
    system: str,
    *,
    static_incar_supported: bool = True,
) -> dict:
    """Map the campaign config onto ``run_ASSYST_on_structure``'s parameters.

    Split out from :func:`run_structure` so the mapping can be asserted against
    ``inspect.signature(run_ASSYST_on_structure)`` without importing a VASP
    stack or running anything.

    ``static_incar`` (the INCAR of the accurate statics, from ``incar`` plus
    ``incar_static``) is passed only when ``static_incar_supported``.  On an
    older pyiron_workflow_assyst that is harmless as long as ``incar_static``
    is empty -- the graph then derives the identical INCAR itself -- and a
    :class:`ConfigError` otherwise, because the customisation would be
    silently ignored.

    Note what is deliberately absent: ``files_to_be_deleted``.
    ``vasp_job`` treats ``None`` as "delete CHG/CHGCAR/WAVECAR" (the documented
    default) and ``[]`` as "delete nothing" -- passing ``[]`` would retain those
    files across the ~64,000 calculations in a campaign, tens of terabytes, with
    no error.  ``run_ASSYST_on_structure`` passes ``None`` internally; nothing
    here may override it.
    """
    run_dir = Path(run_dir).resolve()
    host = cfg.magmom.host_for(system.split("_"))
    static_incar = build_static_incar(
        cfg.incar, cfg.incar_static, structure, cfg.magmom, run_dir.name, host
    )
    if static_incar_supported:
        static = dict(static_incar=static_incar)
    elif cfg.incar_static:
        raise ConfigError(
            "`incar_static` is set, but the installed pyiron_workflow_assyst's "
            "run_ASSYST_on_structure takes no `static_incar` parameter and would "
            "run the statics with its built-in defaults instead. Install "
            f"pyiron_workflow_assyst >= {STATIC_INCAR_MIN_VERSION} in the job "
            "environment (slurm.env), or remove `incar_static`."
        )
    else:
        static = {}
    return dict(
        structure=structure,
        incar=build_incar(cfg.incar, structure, cfg.magmom, run_dir.name, host),
        **static,
        potcar_paths=None,
        job_name=str(run_dir),
        vasp_command=build_vasp_command(cfg),
        ionic_steps=int(cfg.assyst.ionic_steps),
        n_stretch_permutations=int(cfg.assyst.n_stretch),
        n_rattle_permutations=int(cfg.assyst.n_rattle),
        image_selection_eVatom_threshold=cfg.assyst.image_selection_eVatom_threshold,
        shear_strain=float(cfg.assyst.shear_strain),
        triaxial_strain=float(cfg.assyst.triaxial_strain),
        rattle_displacement=float(cfg.assyst.rattle_displacement),
        rattle_strain=float(cfg.assyst.rattle_strain),
        core_overlap_tolerance=float(cfg.assyst.core_overlap_tolerance),
        min_dist_backend=cfg.filters.min_dist_backend,
        compress_dirs=bool(cfg.assyst.compress_dirs),
        compressed_file_in_dir=bool(cfg.assyst.compressed_file_in_dir),
        remove_calc_dirs=bool(cfg.assyst.remove_calc_dirs),
        # Absolute, job-specific: run_ASSYST_on_structure's default is
        # CWD-relative and would collide between concurrent structures.
        train_df_filename=str(run_dir / RESULT_FILENAME),
        seed=cfg.assyst.seed,
    )


def run_structure(cfg: CampaignConfig, poscar: Path, run_dir: Path, system: str):
    """Build and run ``run_ASSYST_on_structure`` for one structure."""
    import pyiron_workflow as pwf
    from pymatgen.core import Structure
    from pyiron_workflow import RunConfig
    from pyiron_workflow_assyst import run_ASSYST_on_structure

    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    structure = Structure.from_file(str(poscar))

    node = pwf.node(run_ASSYST_on_structure)
    return node.run(
        RunConfig(run_dir=run_dir),
        **assyst_kwargs(
            cfg,
            structure,
            run_dir,
            system,
            static_incar_supported=supports_static_incar(),
        ),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="assystant.runner",
        description="Run the ASSYST graph for one structure (invoked by the sbatch script).",
    )
    parser.add_argument("--config", required=True, help="Recorded resolved campaign config")
    parser.add_argument("--poscar", required=True, help="POSCAR for this structure")
    parser.add_argument(
        "--run-dir", required=True, help="Absolute run directory for this structure"
    )
    parser.add_argument("--system", required=True, help="System name, e.g. Fe or Fe_C")
    args = parser.parse_args(argv)

    cfg = CampaignConfig.from_yaml(args.config)
    apply_potcar_config(cfg)
    run = run_structure(cfg, Path(args.poscar), Path(args.run_dir), args.system)
    frame = run.outputs.train_df
    print(f"ASSYST finished: {len(frame)} rows -> {Path(args.run_dir) / RESULT_FILENAME}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
