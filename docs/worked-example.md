# A complete campaign, start to finish

This is a real campaign, executed exactly as written. Unary aluminium, 1–4 atoms
per cell, the cubic spacegroups only — small enough that the whole thing finishes
in minutes, and structurally identical to a production campaign, which is this
file with bigger numbers.

The config is [`examples/Al_tutorial.yaml`](../examples/Al_tutorial.yaml), as
run on the MPIE cluster, so its paths are absolute and site-specific. To follow
along anywhere else, start from the portable copy bundled with the package —
`assystant example Al_tutorial -o Al_tutorial.yaml` — which differs only in
`root` (relative), `slurm.env` (unset: the interpreter running `assystant`) and
`"<EDIT ...>"` placeholders for the partition and VASP binary, plus
`vasp.custodian: false` and `vasp.modules: []`.

## 0. The config

```yaml
root: /cmmc/ptmp/hmai/assystant_tutorial/campaign

system:
  species: [Al]
  min_atoms: 1
  max_atoms: 4
  spacegroups: [195, 230]     # cubic only, to keep the scan small
  strain_min: 0.9
  strain_max: 1.1
  seed: 20260802              # makes `gen` reproducible

incar:
  ALGO: Fast
  EDIFF: 1.0e-4
  ENCUT: 300
  IBRION: 2                   # REQUIRED whenever ionic_steps > 0
  ISIF: 7
  ISMEAR: 1
  ISPIN: 1
  KSPACING: 0.5               # relaxation stages only; statics force 0.25
  LREAL: false
  NELM: 60
  NSW: 50                     # never used -- ionic_steps overrides it
  PREC: Normal
  SIGMA: 0.2

assyst:
  ionic_steps: 20
  n_rattle: 1                 # production uses 5
  n_stretch: 1                # production uses 5

slurm:
  partition: s.cmmg
  ntasks: 8
  time: "01:00:00"
  env: /cmmc/ptmp/hmai/mambaforge/envs/assystant

vasp:
  binary: /cmmc/ptmp/hmai/vasp_compiled/intel_suite/vasp.6.4.3_intelsuite_march_znver4/bin/vasp_std
  custodian: true
  custodian_python: /cmmc/ptmp/hmai/mambaforge/envs/pymatgen/bin/python
```

`ENCUT: 300` and `PREC: Normal` are fine for a tutorial and too loose for a
production potential; [`examples/Fe_unary.yaml`](../examples/Fe_unary.yaml) shows
realistic settings.

Throughout, `assystant` means
`/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/assystant`.

## 1. Generate the structures

```
$ assystant gen Al_tutorial.yaml --no-progress
Al: 43/144 (spacegroup, composition) pairs produced a structure; rejected: Comp_CompatibilityError x101
Al: 43 structures -> /cmmc/ptmp/hmai/assystant_tutorial/campaign/Al
```

36 spacegroups × 4 compositions = 144 attempts, of which 101 were geometrically
impossible. That ratio is normal — see
[`commands.md`](commands.md#gen) on why the rejection tally is printed.

```
$ ls campaign/
Al

$ ls campaign/Al/
manifest.csv
structures
```

Nothing has been submitted and no run directories exist yet. `gen` writes POSCARs
and the manifest, and that is all — even `assystant_config.yaml` does not appear
until `run`, which is what records the resolved config and enforces the staleness
guard against it.

## 2. Look before you leap

**Always render one job and read it before committing thousands.**

```
$ assystant run Al_tutorial.yaml --dry-run --limit 1
rendered 1, skipped 0 queued / 0 finished

$ cat campaign/Al/struct_00000_36c5d68a/assystant_job.sh
#!/bin/bash
#SBATCH --job-name=assyst_Al_00000
#SBATCH --partition=s.cmmg
#SBATCH --ntasks=8
#SBATCH --cpus-per-task=1
#SBATCH --time=01:00:00
#SBATCH --chdir=/cmmc/ptmp/hmai/assystant_tutorial/campaign/Al/struct_00000_36c5d68a
#SBATCH --output=/cmmc/ptmp/hmai/assystant_tutorial/campaign/Al/struct_00000_36c5d68a/assystant.out
#SBATCH --error=/cmmc/ptmp/hmai/assystant_tutorial/campaign/Al/struct_00000_36c5d68a/assystant.err
#SBATCH --get-user-env
#SBATCH --mem-per-cpu=2000MB
#SBATCH --hint=nomultithread

module purge

/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/python -m assystant.runner --config ... --poscar ... --run-dir ... --system Al
```

Check the partition, the wall time, the task count, and that `--config` points at
`<root>/assystant_config.yaml`. Because `--dry-run` writes that config file, you
can also just execute the script by hand to test the whole chain outside the
scheduler — that is what the smoke test in
[`operations.md`](operations.md#smoke-testing-without-a-scheduler) does.

The directory name `struct_00000_36c5d68a` is the structure index plus the first
eight hex digits of the SHA-256 of its POSCAR. That is
[not cosmetic](operations.md#content-hashed-run-directories).

## 3. Submit a handful, then the rest

Start small even after the dry run, because a dry run cannot tell you that
`vasp.binary` is wrong or that `custodian_python` has no `custodian`:

```
$ assystant run Al_tutorial.yaml --limit 4
Submitted batch job 21622728
Submitted batch job 21622729
Submitted batch job 21622730
Submitted batch job 21622731
submitted 4, skipped 0 queued / 0 finished
```

Wait for one to finish, confirm it produced a `df_ASSYST_jobs.pkl`, and then run
the same command without `--limit` to submit everything else. Re-running is safe:
the four already in the queue are skipped.

```
$ assystant run Al_tutorial.yaml --limit 2
Submitted batch job 21622732
Submitted batch job 21622733
submitted 2, skipped 4 queued / 0 finished
```

## 4. Watch it

```
$ assystant status Al_tutorial.yaml
system          category        done  started   total     pct
-------------------------------------------------------------
Al              Unary              6        6      43  13.95%
-------------------------------------------------------------
TOTAL                              6               43  13.95%
```

`assystant status Al_tutorial.yaml --watch 300` reprints this every five minutes;
`--csv job_progress.csv` appends a timestamped block to a log.

A finished structure looks like this — the calculation directories have been
tarred and removed, which at campaign scale is the difference between a usable
filesystem and an unusable one:

```
$ ls campaign/Al/struct_00000_36c5d68a/
assystant.err
assystant_job.sh
assystant.out
df_ASSYST_jobs.pkl
ISIF2_accur_relaxstep0_rattle_1.tar.gz
ISIF2_accur_relaxstep0_shear_1.tar.gz
ISIF2_accur_relaxstep0.tar.gz
ISIF2_accur_relaxstep0_triax_1.tar.gz
ISIF2.tar.gz
ISIF5.tar.gz
ISIF7.tar.gz
pyiron.log

$ tail -1 campaign/Al/struct_00000_36c5d68a/assystant.out
ASSYST finished: 4 rows -> .../struct_00000_36c5d68a/df_ASSYST_jobs.pkl
```

Three relaxation archives, one static on the final relaxed image
(`ISIF2_accur_relaxstep0`), and its three permutations —
`n_rattle + 2 × n_stretch = 1 + 2 = 3`.

## 5. Collect

```
$ assystant collect Al_tutorial.yaml -o Al_tutorial_train.pkl
collected 24 rows -> Al_tutorial_train.pkl
```

Six finished structures × four rows each.

```python
>>> import pandas as pd
>>> df = pd.read_pickle("Al_tutorial_train.pkl")
>>> df.shape
(24, 17)
>>> list(df.columns)
['structures', 'energy', 'scf_convergence', 'calc_start_time',
 'isif7_converged', 'isif5_converged', 'isif2_converged',
 'system', 'struct_index', 'structure_hash', 'layout', 'run_dir',
 'spacegroup', 'composition', 'formula', 'n_atoms', 'volume_per_atom']
```

```
  system  struct_index structure_hash spacegroup formula n_atoms  isif7_converged  isif2_converged
0     Al             0       36c5d68a        195     Al1       1             True             True
1     Al             0       36c5d68a        195     Al1       1             True             True
2     Al             0       36c5d68a        195     Al1       1             True             True
3     Al             0       36c5d68a        195     Al1       1             True             True
4     Al             1       bd3b4596        195     Al2       2             True             True
```

The first four energies from structure 0 show the shape of what ASSYST produces —
one relaxed image near the ground state, and three deformed configurations well
above it, which is exactly the off-equilibrium data a potential needs:

```
-3.368 eV   ISIF2_accur_relaxstep0          (relaxed)
-3.353 eV   ISIF2_accur_relaxstep0_rattle_1 (atoms displaced)
-2.184 eV   ISIF2_accur_relaxstep0_triax_1  (triaxially strained)
+9.458 eV   ISIF2_accur_relaxstep0_shear_1  (heavily sheared)
```

Before training, filter on the relaxation-convergence flags:

```python
clean = df[df.isif7_converged & df.isif5_converged & df.isif2_converged]
```

which here keeps 20 of the 24 rows — one structure's relaxation chain did not
converge within `ionic_steps: 20`, and every row derived from its geometry is
flagged accordingly.

## Scaling this up

A production campaign differs only in magnitude:

```yaml
system:
  species: [[La], [Ce], [Pr], [Nd]]   # four unaries in one file
  min_atoms: 1
  max_atoms: 12
  spacegroups: [1, 230]
assyst:
  ionic_steps: 100
  n_rattle: 5
  n_stretch: 5
slurm:
  ntasks: 64
  time: "48:00:00"
```

which is roughly 2,700 structures per unary system, each with 16 statics. Before
launching that, read [`operations.md`](operations.md) — particularly the sections
on content-hashed directories and the staleness guard, which are what make a
campaign of that size safe to resume and re-launch.
