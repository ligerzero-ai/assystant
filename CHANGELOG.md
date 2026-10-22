# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [0.1.0] - Unreleased

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
