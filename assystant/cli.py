"""``assystant`` command line: ``example`` | ``gen`` | ``run`` | ``status`` | ``collect``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .config import CampaignConfig, ConfigError, StaleConfigError

__all__ = ["build_parser", "main", "OVERRIDE_FLAGS"]

#: CLI flag -> dotted config path.  Any scalar in the YAML is reachable this
#: way; ``--set a.b=c`` covers the ones without a dedicated flag.
OVERRIDE_FLAGS = {
    "root": "root",
    "ntasks": "slurm.ntasks",
    "time": "slurm.time",
    "partition": "slurm.partition",
    "env": "slurm.env",
    "vasp_binary": "vasp.binary",
    "ionic_steps": "assyst.ionic_steps",
    "n_rattle": "assyst.n_rattle",
    "n_stretch": "assyst.n_stretch",
    "min_dist_backend": "filters.min_dist_backend",
    "seed": "system.seed",
    "n_jobs": "system.n_jobs",
}


def _add_override_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", help="Override `root` (campaign directory)")
    parser.add_argument("--ntasks", type=int, help="Override `slurm.ntasks`")
    parser.add_argument("--time", help="Override `slurm.time` (HH:MM:SS)")
    parser.add_argument("--partition", help="Override `slurm.partition`")
    parser.add_argument("--env", help="Override `slurm.env` (conda prefix for the runner)")
    parser.add_argument("--vasp-binary", dest="vasp_binary", help="Override `vasp.binary`")
    parser.add_argument(
        "--ionic-steps",
        dest="ionic_steps",
        type=int,
        help="Override `assyst.ionic_steps`",
    )
    parser.add_argument("--n-rattle", dest="n_rattle", type=int, help="Override `assyst.n_rattle`")
    parser.add_argument(
        "--n-stretch", dest="n_stretch", type=int, help="Override `assyst.n_stretch`"
    )
    parser.add_argument(
        "--min-dist-backend",
        dest="min_dist_backend",
        choices=["neighbor_list", "mic"],
        help="Override `filters.min_dist_backend`",
    )
    parser.add_argument("--seed", type=int, help="Override `system.seed`")
    parser.add_argument("--n-jobs", dest="n_jobs", type=int, help="Override `system.n_jobs`")
    parser.add_argument(
        "--set",
        dest="set_",
        action="append",
        default=[],
        metavar="PATH=VALUE",
        help="Override any config scalar by dotted path, e.g. --set assyst.rattle_strain=0.02",
    )


def overrides_from_args(args) -> dict:
    """Collect dotted-path overrides from parsed CLI arguments.

    Only flags the user actually passed appear, so precedence is
    CLI flag > YAML > dataclass default.
    """
    overrides = {}
    for dest, dotted in OVERRIDE_FLAGS.items():
        value = getattr(args, dest, None)
        if value is not None:
            overrides[dotted] = value
    for item in getattr(args, "set_", []) or []:
        if "=" not in item:
            raise ConfigError(f"--set expects PATH=VALUE, got {item!r}")
        path, _, raw = item.partition("=")
        overrides[path.strip()] = yaml.safe_load(raw)
    return overrides


def load_config(args) -> CampaignConfig:
    return CampaignConfig.from_yaml(args.config).resolve(overrides_from_args(args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assystant",
        description=(
            "Run an ASSYST campaign end to end: PyXtal structure generation -> "
            "per-structure DFT via SLURM -> collated training dataset."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    example = sub.add_parser(
        "example",
        help="List the bundled campaign templates, or write one out to start from",
        description=(
            "With no NAME, list the bundled campaign templates. With NAME, print "
            "that template to stdout, or write it to FILE with -o."
        ),
    )
    example.add_argument("name", nargs="?", help="Template name, e.g. Al_tutorial")
    example.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="Write the template to FILE instead of stdout",
    )
    example.add_argument("--force", action="store_true", help="Overwrite FILE if it already exists")

    gen = sub.add_parser("gen", help="PyXtal spacegroup x stoichiometry scan -> POSCARs + manifest")
    gen.add_argument("config", help="Campaign YAML")
    gen.add_argument("--no-progress", action="store_true", help="Suppress the progress bar")
    _add_override_flags(gen)

    run = sub.add_parser("run", help="Render and submit one SLURM job per structure")
    run.add_argument("config", help="Campaign YAML")
    run.add_argument("--limit", type=int, help="Process at most N structures")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Render sbatch scripts without submitting or querying the scheduler",
    )
    run.add_argument(
        "--from-scratch",
        action="store_true",
        help="Accept a changed config and DELETE each structure's run_dir in full, "
        "including executorlib_cache",
    )
    _add_override_flags(run)

    status = sub.add_parser("status", help="Per-system progress table")
    status.add_argument("config", help="Campaign YAML")
    status.add_argument("--csv", help="Append the table to a job_progress-shaped CSV")
    status.add_argument(
        "--watch",
        nargs="?",
        type=int,
        const=60,
        default=None,
        metavar="SECONDS",
        help="Refresh every SECONDS (default 60)",
    )
    _add_override_flags(status)

    collect = sub.add_parser(
        "collect", help="Concatenate every df_ASSYST_jobs.pkl into one dataset"
    )
    collect.add_argument("config", help="Campaign YAML")
    collect.add_argument("-o", "--output", required=True, help="Output .pkl (or .csv)")
    collect.add_argument(
        "--all-systems",
        action="store_true",
        help="Scan every system directory under root, not only those in the config",
    )
    _add_override_flags(collect)

    return parser


def _cmd_example(args) -> int:
    from .examples import (
        list_templates,
        template_description,
        template_text,
        write_template,
    )

    if not args.name:
        names = list_templates()
        width = max(len(n) for n in names)
        for name in names:
            print(f"{name:<{width}}  {template_description(name)}")
        return 0
    try:
        if args.output:
            path = write_template(args.name, args.output, force=args.force)
            print(f"wrote {path}")
        else:
            sys.stdout.write(template_text(args.name))
    except (KeyError, FileExistsError) as exc:
        message = exc.args[0] if isinstance(exc, KeyError) else exc
        print(f"error: {message}", file=sys.stderr)
        return 2
    return 0


def _cmd_gen(args) -> int:
    from .generate import run_generation

    cfg = load_config(args)
    manifests = run_generation(cfg, progress=not args.no_progress)
    for name, manifest in manifests.items():
        print(f"{name}: {len(manifest)} structures -> {Path(cfg.root) / name}")
    return 0


def _cmd_run(args) -> int:
    from .submit import submit_campaign

    cfg = load_config(args)
    _warn_if_static_incar_unsupported(cfg)
    stats = submit_campaign(
        cfg, dry_run=args.dry_run, limit=args.limit, from_scratch=args.from_scratch
    )
    verb = "rendered" if args.dry_run else "submitted"
    message = (
        f"{verb} {len(stats['rendered']) if args.dry_run else stats['submitted']}, "
        f"skipped {stats['skipped_queued']} queued / {stats['skipped_done']} finished"
    )
    if args.dry_run and args.from_scratch:
        # A dry run never deletes: report what --from-scratch would wipe
        # instead of silently omitting the most destructive part of what the
        # real run would do.
        message += (
            f", would delete {stats['would_delete']} existing run_dir(s) "
            "(not deleted -- dry run)"
        )
    print(message)
    return 0


def _warn_if_static_incar_unsupported(cfg) -> None:
    """Warn now, on the login node, about what would otherwise fail in the job.

    Only a heuristic: the jobs run in ``slurm.env``, which may not be this
    environment, so this neither blocks nor stays silent.  The in-job runner
    enforces it for real.
    """
    if not cfg.incar_static:
        return
    try:
        from .runner import STATIC_INCAR_MIN_VERSION, supports_static_incar

        if supports_static_incar():
            return
        import pyiron_workflow_assyst

        installed = getattr(pyiron_workflow_assyst, "__version__", "?")
    except Exception:  # pragma: no cover - no pyiron_workflow_assyst here at all
        return
    print(
        f"Warning: `incar_static` is set but pyiron_workflow_assyst {installed} in "
        f"this environment lacks `static_incar` (needs >= {STATIC_INCAR_MIN_VERSION}); "
        "jobs will refuse to run unless slurm.env has a newer one",
        file=sys.stderr,
    )


def _cmd_status(args) -> int:
    from .status import append_csv, campaign_status, format_table, watch

    cfg = load_config(args)
    if args.watch:
        watch(cfg, interval=args.watch, csv_path=args.csv)
        return 0
    rows = campaign_status(cfg)
    print(format_table(rows))
    if args.csv:
        append_csv(rows, args.csv)
        print(f"appended to {args.csv}")
    return 0


def _cmd_collect(args) -> int:
    from .collect import collect_campaign

    cfg = load_config(args)
    systems = None if args.all_systems else cfg.system.systems
    frame = collect_campaign(cfg.root, systems, args.output)
    print(f"collected {len(frame)} rows -> {args.output}")
    return 0


_COMMANDS = {
    "example": _cmd_example,
    "gen": _cmd_gen,
    "run": _cmd_run,
    "status": _cmd_status,
    "collect": _cmd_collect,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except (ConfigError, StaleConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
