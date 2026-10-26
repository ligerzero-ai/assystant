# assystant

Run an ASSYST campaign end to end from a single YAML file: PyXtal structure
generation → one SLURM job per structure running the ISIF7 → ISIF5 → ISIF2
relaxation and permutation-statics chain → a single collated training dataset.

ASSYST generates DFT training data for interatomic potentials: random crystals
across all 230 spacegroups, relaxed through a three-stage ISIF chain, then
high-accuracy statics on the relaxed images *and* on rattled, triaxially strained
and sheared permutations of them — the off-equilibrium configurations a potential
needs and that ordinary MD never visits.

`assystant` drives that per campaign, replacing hand-copied
`2025_03_16_Unaries.py` scripts, element lists hardcoded in notebook cells, and a
`submit_temp.sh` maintained by hand with one line per element.

```bash
assystant gen     camp.yaml                        # PyXtal scan -> POSCARs + manifest
assystant run     camp.yaml --dry-run --limit 1    # render one job; read it
assystant run     camp.yaml                        # submit; also the resume command
assystant status  camp.yaml --watch 300            # progress
assystant collect camp.yaml -o train.pkl           # one dataframe
```

## Documentation

| | |
|---|---|
| [**Overview**](docs/overview.md) | what ASSYST does, what `assystant` adds, how the three repos fit together |
| [**Installation**](docs/installation.md) | dependencies, install order, the POTCAR config |
| [**Worked example**](docs/worked-example.md) | a complete campaign, start to finish, that you can copy |
| [**Several elements**](docs/multi-element.md) | many unaries, binaries and ternaries in one campaign: `species`, stoichiometry, `magmom.host`, adding elements later |
| [**Configuration**](docs/configuration.md) | every YAML key: meaning, default, when to change it |
| [**Commands**](docs/commands.md) | `example` / `gen` / `run` / `status` / `collect` reference |
| [**Operations**](docs/operations.md) | content-hashed directories, the staleness guard, `--from-scratch`, smoke testing — the part that saves days |
| [**Behaviour changes**](docs/behaviour-changes.md) | read before comparing new data against pre-2026 campaigns |
| [**Troubleshooting**](docs/troubleshooting.md) | real failure modes and their actual error text |

Background: [design spec](docs/2026_07_31_assystant_design.md) ·
[implementation report](docs/implementation-report.txt)

## Install

```bash
python -m pip install assystant
```

Python 3.11–3.14. No `--no-build-isolation`, no pre-installed scientific stack:
`pip`, `uv pip install assystant`, a `venv`, a conda/mamba env or a pixi project
all work the same way. See [installation](docs/installation.md) for each variant,
the POTCAR configuration and what a real run additionally needs (SLURM, a VASP
binary).

## Quick start

```bash
python -m pip install assystant
assystant example Al_tutorial -o camp.yaml       # bundled tutorial campaign
assystant gen camp.yaml --no-progress            # PyXtal scan -> ./Al_campaign/Al/
assystant run camp.yaml --dry-run --limit 1      # render one job script; read it
```

This works from any directory on any machine: `gen` and `run --dry-run` need
neither SLURM nor VASP, and the bundled template carries no site-specific paths.
`gen` writes 43 POSCARs and a `manifest.csv` under `./Al_campaign/Al/`; the
rendered job script runs the Python environment `assystant` is installed in.
`assystant example` with no name lists the bundled templates: `AlCu_tutorial`
is the same campaign over several elements at once — two unaries and the Al–Cu
binary, from `species: [[Al], [Cu], [Al, Cu]]` — and is walked through in
[`docs/multi-element.md`](docs/multi-element.md).

To go further — a real `run`, then `status` and `collect` — fill in the
`"<EDIT ...>"` placeholders in `camp.yaml` (SLURM partition, VASP binary; a real
`run` refuses to submit while any is left) and set up the
[POTCAR config](docs/installation.md#the-potcar-library). The campaign is walked
through in [`docs/worked-example.md`](docs/worked-example.md).

The repository's [`examples/`](examples/) directory holds the configs of the
real MPIE cluster setup, with its absolute paths:
[`examples/Al_tutorial.yaml`](examples/Al_tutorial.yaml) is the same tutorial
campaign as run there, [`examples/Fe_unary.yaml`](examples/Fe_unary.yaml) shows
production settings, and [`examples/Fe_smoke.yaml`](examples/Fe_smoke.yaml)
proves an install actually runs DFT, on a login node, in under a minute.

## Layout on disk

```
<root>/
  assystant_config.yaml                 the resolved config `run` was launched with
  Fe/
    manifest.csv                        index, hash, spacegroup, composition, n_atoms, V/atom
    structures/00000_structure.vasp ...
    struct_00000_3f9c1a20/              one per structure, content-hashed
      assystant_job.sh
      assystant.out / assystant.err
      ISIF7.tar.gz  ISIF5.tar.gz  ISIF2.tar.gz  ...
      df_ASSYST_jobs.pkl                present == finished
```

The `_3f9c1a20` is the first eight hex digits of the SHA-256 of the POSCAR. That
is a correctness mechanism, not a naming convention — the caching layer
underneath keys on directory path and never hashes its inputs, so a
content-derived name is what stops one structure's results from being served for
another. See
[content-hashed run directories](docs/operations.md#content-hashed-run-directories).

## Three things to know before your first campaign

1. **Start with `--limit 1`.** A dry run proves the script renders; it cannot
   prove the VASP binary, the modules, the POTCAR library or your INCAR work on a
   compute node. Submit one, check it, then submit the rest.
2. **`IBRION` is required** whenever `assyst.ionic_steps > 0`. A missing one makes
   VASP default to molecular dynamics and refuse the job. Use `IBRION: 2`.
3. **`ionic_steps` now applies to all three ISIF stages**, where pre-2026
   campaigns applied it to ISIF7 only. New ISIF5/ISIF2 data is not directly
   comparable with old datasets — see
   [behaviour changes](docs/behaviour-changes.md).
4. **The relaxation and the labels have separate INCARs.** `incar` is the
   relaxation; the accurate statics that produce the training data run on
   `incar` plus [`incar_static`](docs/configuration.md#incar_static--the-incar-of-the-training-labels)
   (by default `KSPACING 0.25`, `EDIFF 1e-5`, `EDIFFG 1e-4`, `LREAL false`), so
   a production-grade `ENCUT` or k-mesh for the labels costs nothing in the
   relaxation.

## Tests

```bash
python -m pip install "assystant[test]"
pytest tests/ -q
```

126 tests. None call SLURM or VASP; `submit_campaign` takes `queued_dirs` and
`submit_fn` injection seams for exactly that reason. For an end-to-end check that
runs real DFT, use [`examples/Fe_smoke.yaml`](examples/Fe_smoke.yaml).

## Releasing

Releases go to PyPI from GitHub Actions via
[trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token
is stored anywhere.

1. Bump `__version__` in `assystant/__init__.py` (the only place the version lives) and add
   an entry to [`CHANGELOG.md`](CHANGELOG.md).
2. Open a PR and merge it to `main`. The
   [`release`](.github/workflows/release.yml) workflow builds the sdist and
   wheel and runs `twine check --strict` on every PR as a dry run.
3. Create a GitHub release with tag `v<version>` (e.g. `v0.1.0`). Publishing
   the release builds again, checks that the tag matches the package version
   (a `v` or `assystant-` prefix is stripped), and uploads to
   [PyPI](https://pypi.org/p/assystant).

One-time setup: on PyPI, add a trusted publisher for `assystant` with owner
`ligerzero-ai`, repository `assystant`, workflow `release.yml` and environment
`pypi`, and create a `pypi` environment in the GitHub repository settings
(optionally with required reviewers).
