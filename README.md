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
| [**Configuration**](docs/configuration.md) | every YAML key: meaning, default, when to change it |
| [**Commands**](docs/commands.md) | `gen` / `run` / `status` / `collect` reference |
| [**Operations**](docs/operations.md) | content-hashed directories, the staleness guard, `--from-scratch`, smoke testing — the part that saves days |
| [**Behaviour changes**](docs/behaviour-changes.md) | read before comparing new data against pre-2026 campaigns |
| [**Troubleshooting**](docs/troubleshooting.md) | real failure modes and their actual error text |

Background: [design spec](docs/2026_07_31_assystant_design.md) ·
[implementation report](docs/implementation-report.txt)

## Quick start

```bash
/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/assystant gen examples/Al_tutorial.yaml
```

[`examples/Al_tutorial.yaml`](examples/Al_tutorial.yaml) is a small, complete,
minutes-long campaign walked through in
[`docs/worked-example.md`](docs/worked-example.md).
[`examples/Fe_unary.yaml`](examples/Fe_unary.yaml) shows production settings.
[`examples/Fe_smoke.yaml`](examples/Fe_smoke.yaml) proves an install actually runs
DFT, on a login node, in under a minute.

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

## Tests

```bash
/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/ -q
```

108 tests. None call SLURM or VASP; `submit_campaign` takes `queued_dirs` and
`submit_fn` injection seams for exactly that reason. For an end-to-end check that
runs real DFT, use [`examples/Fe_smoke.yaml`](examples/Fe_smoke.yaml).
