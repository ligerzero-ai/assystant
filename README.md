# assystant

Run an ASSYST campaign end to end from a single YAML file: PyXtal structure
generation → one SLURM job per structure running the ISIF7→5→2 relaxation and
permutation statics chain → a single collated training dataset.

It replaces copying `2025_03_16_Unaries.py` per campaign, hardcoding element
lists in notebook cells, and hand-maintaining a `submit_temp.sh` with one
`calculate_assyst.py` invocation per element.

Design spec: [`docs/2026_07_31_assystant_design.md`](docs/2026_07_31_assystant_design.md).

## Install

```bash
/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/python -m pip install -e .
```

Requires `pyiron_workflow>=0.19`, `flowrep`, `pyiron_workflow_assyst>=0.2.0`,
`pyiron_workflow_vasp>=0.2.0`, `pyxtal`, `pymatgen`, `ase`, `pandas`, `pyyaml`.

## Worked example

A unary Fe campaign, 1–12 atoms, all 230 spacegroups. The full config is
[`examples/Fe_unary.yaml`](examples/Fe_unary.yaml).

```bash
# 1. Generate. Writes POSCARs + manifest.csv under <root>/Fe/.
assystant gen examples/Fe_unary.yaml
#   Fe: 2743 structures -> /cmmc/ptmp/hmai/ASSYST_pyiwf/Unary/Fe

# 2. Look at what would be submitted before committing 2743 jobs.
assystant run examples/Fe_unary.yaml --dry-run --limit 1
cat /cmmc/ptmp/hmai/ASSYST_pyiwf/Unary/Fe/struct_00000_3f9c1a20/assystant_job.sh

# 3. Submit. Re-running is safe: already-queued and already-finished
#    structures are skipped, so this doubles as the resume command.
assystant run examples/Fe_unary.yaml

# 4. Watch progress; optionally append to a job_progress.csv-shaped log.
assystant status examples/Fe_unary.yaml --csv job_progress.csv
assystant status examples/Fe_unary.yaml --watch 300

# 5. Collate.
assystant collect examples/Fe_unary.yaml -o Fe_unary_train.pkl
```

Resulting layout:

```
<root>/
  assystant_config.yaml                 # the resolved config `run` was launched with
  Fe/
    manifest.csv                        # index, hash, spacegroup, composition, n_atoms, V/atom
    structures/00000_structure.vasp ...
    struct_00000_3f9c1a20/              # one per structure, content-hashed
      assystant_job.sh
      assystant.out / assystant.err
      executorlib_cache/
      df_ASSYST_jobs.pkl                # present == finished
```

## Commands

### `assystant gen camp.yaml`

Runs the PyXtal spacegroup × stoichiometry scan and writes
`<root>/<system>/structures/<idx:05d>_structure.vasp` plus
`<root>/<system>/manifest.csv`.

* **Compositions.** For a unary system these are `[(n,) for n in
  range(min_atoms, max_atoms+1)]` — `stoich_min`/`stoich_max` do not apply,
  because for one species the stoichiometry *is* the atom count. For a
  multi-component system they are `product(range(stoich_min, stoich_max+1),
  repeat=k)` filtered to `min_atoms <= sum(comp) <= max_atoms`.
* **Volumes.** Each structure is rescaled into
  `[strain_min, strain_max] × eq_volume_per_atom`, the equilibrium volume coming
  from `eq_volume_source` (the bundled Materials Project Voronoi table by
  default, or an explicit `{Fe: 11.8}` map). Structures whose species are absent
  from the table are left unscaled.
* **Perturbation.** `system.perturb` (default 0.1 Å) is applied here, not at
  submit time as `calculate_assyst.py` did, so the POSCAR on disk, the hash
  naming its run directory, and the geometry actually calculated are the same
  object.
* **Parallelism.** `ProcessPoolExecutor` with `OMP/OPENBLAS/MKL/NUMEXPR_NUM_THREADS`
  pinned to 1 — set unconditionally, so an `OMP_NUM_THREADS` inherited from the
  login shell cannot reintroduce BLAS oversubscription.
* **Reproducibility.** `system.seed: null` draws a fresh `secrets.randbits(32)`
  per task (the ported behaviour). An integer makes generation deterministic.

Flags: `--no-progress`, plus the override flags below.

### `assystant run camp.yaml [--limit N] [--dry-run] [--from-scratch]`

Walks each system's manifest and renders one sbatch script per structure into
its run directory, then submits it.

* **Idempotent.** A structure is skipped if its directory is already the working
  directory of a queued/running job (`squeue -u $USER -o %Z`), or already holds
  `df_ASSYST_jobs.pkl`. Re-running `run` is the resume path.
* **`--dry-run`** renders the scripts without submitting and without querying
  the scheduler.
* **`--from-scratch`** deletes each structure's run directory **in full**,
  including `executorlib_cache`, and accepts a changed config (below).

### `assystant status camp.yaml [--csv out.csv] [--watch [SECONDS]]`

Per-system table of structure directories present, finished (those containing
`df_ASSYST_jobs.pkl`), and total from the manifest. `--csv` appends a
timestamped block in the same shape as the existing
`/u/hmai/2024_12_05_DFTPyxtalAssystNodes/job_progress.csv`
(`timestamp,category,run_dir,job_count,total_structures,pct,minutes_completed`,
with `minutes_completed = job_count × 15`).

### `assystant collect camp.yaml -o out.pkl`

Concatenates every `df_ASSYST_jobs.pkl` under the campaign root, stamping each
row with `system`, `struct_index`, `structure_hash`, `layout` and `run_dir`, and
joining the manifest's `spacegroup`, `composition`, `formula`, `n_atoms` and
`volume_per_atom` columns.

Both on-disk layouts are read: the hashed `struct_<idx:05d>_<sha8>/` this
package writes (joined on the structure hash), and the pre-2026
`struct_pyxtal_<idx>/` (joined on index), so existing campaign data stays
readable without migration. `--all-systems` scans every system directory under
`root` rather than only those named in the config. `-o` ending in `.csv` writes
CSV instead of a pickle.

## Content-hashed run directories

Each structure runs in `<root>/<system>/struct_<idx:05d>_<sha8>/`, where `sha8`
is the first eight hex characters of the SHA-256 of the canonical POSCAR string,
recorded in `manifest.csv`.

This is not cosmetic. executorlib's cache is keyed on **node lexical path with
no hashing of inputs**. Re-running `gen` with a different seed produces a
different structure at index 5, while a plain `struct_00005/` would still hold a
populated cache — and the stale cache would be silently reused, returning
another structure's VASP results into your training data. A content-derived
directory name makes that impossible by construction: a changed structure cannot
land in a populated namespace.

Two rules follow, both departures from the pre-2026 scripts:

* **No `os.chdir` anywhere.** Every path is absolute. The job's working
  directory comes from `#SBATCH --chdir`, which also keeps `squeue -o %Z`
  reporting the structure directory that the queued-skip matches on.
* **The executorlib cache lives at `<run_dir>/executorlib_cache`**, scoped to
  exactly one structure.

## Config staleness guard

`run` writes the fully resolved config to `<root>/assystant_config.yaml` and, on
every later invocation, compares against it. A calculation-affecting difference
aborts with `StaleConfigError` naming the changed keys, because the structure
hash cannot catch a changed INCAR and the cache would serve results computed
under the old settings.

`--from-scratch` is the escape hatch: it accepts the new config and wipes each
structure's run directory in full (including `executorlib_cache`) before
resubmitting.

The `slurm` section is **excluded** from the comparison — wall time, task count
and partition change how the work reaches the scheduler, not what is computed,
and forcing the destructive `--from-scratch` path after a `--time` bump would be
worse than useless. Everything else (`system`, `incar`, `magmom`, `assyst`,
`filters`, `vasp`) is compared.

## Overrides

Every scalar is addressable by a dotted path. Precedence is
**CLI flag > YAML > default**.

| flag | config path |
|---|---|
| `--root` | `root` |
| `--ntasks` | `slurm.ntasks` |
| `--time` | `slurm.time` |
| `--partition` | `slurm.partition` |
| `--env` | `slurm.env` |
| `--vasp-binary` | `vasp.binary` |
| `--ionic-steps` | `assyst.ionic_steps` |
| `--n-rattle` | `assyst.n_rattle` |
| `--n-stretch` | `assyst.n_stretch` |
| `--min-dist-backend` | `filters.min_dist_backend` |
| `--seed` | `system.seed` |
| `--n-jobs` | `system.n_jobs` |
| `--set PATH=VALUE` | anything else, e.g. `--set assyst.rattle_strain=0.02` |

## Things that will bite you

* **`n_rattle: 0` together with `n_stretch: 0` is rejected.** A
  "relax and statics only" campaign is not available: `pyiron_workflow` 0.19.0's
  `ForEach` raises an opaque `IndexError` on a length-0 iterable
  ([pyiron/pyiron_workflow#915](https://github.com/pyiron/pyiron_workflow/issues/915)),
  so `run_ASSYST_on_structure` rejects an empty permutation set. `assystant`
  catches this at config-validation time so the message names the YAML keys
  rather than surfacing from deep inside the graph.
* **`ionic_steps` applies to all three ISIF stages** (7, 5 and 2), a deliberate
  and user-approved deviation from pre-2026 campaigns, which applied it to ISIF7
  only and let ISIF5/ISIF2 run at the raw INCAR's `NSW` (in production, 100
  vs 500). New data is therefore **not directly comparable** to existing datasets
  for the ISIF5 and ISIF2 stages.
* **`filters.min_dist` must be 1.0.** `run_ASSYST_on_structure` in
  `pyiron_workflow_assyst` 0.2.0 hardcodes the 1.0 Å floor it passes to
  `get_ASSYST_deformed_structures` and exposes no parameter for it. The key is
  kept for provenance and rejected if changed, rather than silently ignored.
* **`filters.min_dist_backend` changes which structures enter your dataset.**
  `neighbor_list` (the default) sees an atom's distance to its own periodic
  image; `mic` does not, which made the floor completely inert for one-atom
  cells. `mic` is retained only to reproduce pre-2026 campaigns exactly.
* **`MAGMOM` is written only when `ISPIN: 2`.** The pre-2026 scripts wrote it
  regardless, which made a nonmagnetic INCAR read as though it were magnetic.

## Tests

```bash
/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest -q
```

No test calls SLURM or VASP; `submit_campaign` takes `queued_dirs` and
`submit_fn` injection seams for exactly that reason.
