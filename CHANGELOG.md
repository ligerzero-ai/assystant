# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [0.1.1] - Unreleased

### Added

- `assystant example [NAME] [-o FILE]`: lists the campaign templates bundled
  in the wheel, or writes one out. `Al_tutorial` is a portable copy of
  `examples/Al_tutorial.yaml`, so `pip install assystant` users get the
  tutorial without a repository checkout.
- A real `run` (not `--dry-run`) refuses to submit while any config value is
  still an `"<EDIT ...>"` template placeholder.

### Changed

- `root` is no longer required: when unset it defaults to the current working
  directory. Configs that set `root` behave exactly as before.
- README and installation quick start use the bundled template:
  `assystant example Al_tutorial -o camp.yaml`, then `gen` and
  `run --dry-run`, with no `--root` / `--env` flags.

### Removed

- The installation notes for installing before `pyiron_workflow_vasp` 0.2.0
  and `pyiron_workflow_assyst` 0.2.0 were on PyPI; both are released.

## [0.1.0]

Initial release.

### Added

- `assystant` command-line driver for ASSYST campaigns:
  - `gen`: PyXtal structure generation and INCAR assembly from a campaign config.
  - `run`: per-structure DFT submission to SLURM via `sbatch`, with an in-job
    runner and a `--dry-run` mode.
  - `status`: campaign progress across structures.
  - `collect`: collation of finished calculations into a training dataset.
- `vasp.launcher` config option for the MPI launcher.
- User documentation set under `docs/` and a runnable example campaign
  (`examples/Fe_smoke.yaml`).
- CI: `clean-install` workflow and a `release` workflow that publishes to PyPI
  via trusted publishing.
