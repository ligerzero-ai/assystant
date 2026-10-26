# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- `docs/new-machine.md`: setting up on a new cluster step by step — POTCAR
  library, VASP binary / modules / launcher / custodian, SLURM, one site file
  per machine, moments per element, smoke test, first campaign.
- `--site FILE` on every campaign command: a machine-specific YAML (`slurm`,
  `vasp`, perhaps `root`) deep-merged over the campaign YAML before overrides,
  repeatable, later files winning. Precedence is CLI flag > site file(s) >
  campaign YAML > defaults. `assystant example site` writes a starting site
  file with `"<EDIT ...>"` placeholders.
- `${VAR}` / `$VAR` / leading `~` expansion in `root`, `slurm` and `vasp`
  values. An unset variable is a `ConfigError` naming the key, raised by every
  command including `gen`. The recorded config holds the expanded values.
- `vasp.potcar_config`: pins the `pyiron_workflow_vasp` POTCAR configuration
  file. The rendered job script exports `PYIRON_VASP_CONFIG` and the in-job
  runner sets it before the library is imported; `run` warns when the file
  does not exist on the login node.
- `magmom.moments`: per-element initial moments (`{Mn: -2.0, C: 0.0}`), applied
  in every system and taking precedence over the host / solute rule, so a
  multi-element campaign can give each solute its own moment.
- The staleness guard treats a key the recorded config predates
  (`vasp.potcar_config`, `magmom.moments`, `incar_static`) as its empty
  default, so upgrading never by itself makes a campaign stale.
- Job-script and VASP-invocation hooks for machines that are not the
  reference cluster: `slurm.prologue` (shell lines run once per job, after
  `module purge`), `slurm.module_purge` (`false` without environment modules),
  `slurm.submit_command` (what a rendered script is handed to; `sbatch`
  default, the path appended), and `vasp.prologue` (shell lines run in every
  VASP directory after the `module load`s, before the launcher). The
  new-machine guide shows the rendered script and VASP block line by line
  with the key that produced each, plus GPU, OpenMPI and flux recipes, and
  spells out how `pyiron_workflow_vasp` resolves the POTCAR library
  (`<pyiron_vasp_resources>/potpaw_<NN>/<GGA|LDA>/<variant>/POTCAR`, the
  per-element default-variant table, and that `vasp_POTCAR_path_*` values are
  not read).

- `docs/multi-element.md`: a guide to campaigns over several elements — the
  two shapes of `system.species` (several systems vs. one multi-component
  system), the stoichiometry scan and its size, per-system `magmom.host`
  resolution, composition-weighted equilibrium volumes and the silent fallback
  when an element is missing from the volume table, `--limit` across systems,
  `status` / `collect` per system, and why an element is added as a new `root`
  once a campaign has run. Cross-linked from the README, configuration,
  commands, worked-example, overview and troubleshooting pages.
- `AlCu_tutorial` bundled template (`assystant example AlCu_tutorial`): the Al
  tutorial over `species: [[Al], [Cu], [Al, Cu]]` — two unaries and a binary —
  with the same portability guarantees as `Al_tutorial`.
- `incar_static`: a config section for the INCAR of the accurate statics that
  produce the training labels, layered on `incar` (`KSPACING 0.25`,
  `EDIFF 1e-5`, `EDIFFG 1e-4`, `LREAL false` by default, exactly what every
  campaign ran before; `null` drops a default and inherits the relaxation
  value). `ISIF` and a non-zero `NSW` are refused. The relaxation INCAR and the
  label INCAR are now independently adjustable; `--set incar_static.ENCUT=520`
  works like any other override. Participates in the staleness guard through
  its effective tags, so campaigns recorded before the section existed are not
  stale. Passing it to the graph needs `pyiron_workflow_assyst >= 0.2.2`
  (`static_incar`); on an older release an empty `incar_static` runs
  identically and a customised one is refused in-job with a clear error, and
  `run` warns when the login environment is too old.

### Changed

- `system.stoich_min` / `system.stoich_max` now default to `null`, meaning the
  full composition range: `1` and `max_atoms` respectively, so a binary scans
  every `1..n-1` split of every cell size `n` and a ternary every three-way
  split from `n = 3`, whatever `max_atoms` is. Previously the defaults were the
  fixed `1` / `12`, which silently truncated any multi-component scan with
  `max_atoms > 12`. Unary systems are unaffected. The recorded
  `assystant_config.yaml` carries the effective bounds.
- The staleness guard compares `system.stoich_*` through the composition sets
  they imply (reported as `system.compositions.<system>`) instead of the raw
  numbers, so a campaign recorded by 0.1.1 with its written-out `stoich_max: 12`
  is not stale after upgrading unless its scan actually changed.
- `enumerate_compositions` lives in `assystant.config` (still re-exported from
  `assystant.generate`) and enumerates with pruning rather than filtering the
  full Cartesian product; the order is unchanged, so seeded generation is
  byte-identical.

## [0.1.1] - 2026-10-22

### Added

- `assystant example [NAME] [-o FILE]`: lists the campaign templates bundled
  in the wheel, or writes one out. `Al_tutorial` is a portable copy of
  `examples/Al_tutorial.yaml`, so `pip install assystant` users get the
  tutorial without a repository checkout.
- A real `run` (not `--dry-run`) refuses to submit while any config value is
  still an `"<EDIT ...>"` template placeholder. The staleness guard ignores a
  placeholder recorded by an earlier `run --dry-run`, so filling one in
  afterwards does not demand `--from-scratch`.
- Python 3.14 support: `requires-python` is now `>=3.11,<3.15`, with a
  3.14 classifier and a 3.14 leg in the clean-install CI matrix.

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
