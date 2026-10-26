# Command reference

Four campaign commands, all taking the campaign YAML as their first argument and
all accepting the [override flags](configuration.md#overrides-and-precedence)
and `--site FILE` (a machine-specific YAML merged over the campaign — see
[`new-machine.md`](new-machine.md)), plus `example`, which writes out a
starting YAML.

Every transcript below was executed against the campaign in
[`worked-example.md`](worked-example.md) and is pasted verbatim.

---

## `example`

```
assystant example [NAME] [-o FILE] [--force]
```

Campaign templates bundled with the package, so a `pip install` is enough to
get a working config. With no `NAME`, lists them:

```
$ assystant example
AlCu_tutorial  Multi-element tutorial campaign: unary Al, unary Cu and the Al-Cu binary in one file, cubic spacegroups, minutes of DFT.
Al_tutorial    Unary Al tutorial campaign: 1-4 atoms, cubic spacegroups, minutes of DFT.
```

`Al_tutorial` is the campaign in [`worked-example.md`](worked-example.md);
`AlCu_tutorial` is the same campaign over three systems and is walked through in
[`multi-element.md`](multi-element.md); `site` is not a campaign but a
machine file for `--site`, walked through in [`new-machine.md`](new-machine.md).

With `NAME`, prints that template to stdout, or with `-o FILE` writes it there
(refusing to overwrite an existing file unless `--force`):

```
$ assystant example Al_tutorial -o camp.yaml
wrote camp.yaml
```

The templates carry no site-specific paths: `root` is relative, `slurm.env` is
unset (jobs use the interpreter running `assystant`), and the values only a real
`run` needs are `"<EDIT ...>"` placeholders — see
[configuration](configuration.md#vasp--how-vasp-is-invoked).

---

## `gen`

```
assystant gen camp.yaml [--no-progress]
```

Runs the PyXtal spacegroup × composition scan for every system in the config and
writes, per system, `<root>/<system>/structures/<idx:05d>_structure.vasp` plus
`<root>/<system>/manifest.csv`.

```
$ assystant gen Al_tutorial.yaml --no-progress
Al: 43/144 (spacegroup, composition) pairs produced a structure; rejected: Comp_CompatibilityError x101
Al: 43 structures -> /cmmc/ptmp/hmai/assystant_tutorial/campaign/Al
```

**Read the rejection tally.** 101 of 144 attempts failing is not a problem — it
is the normal outcome. `Comp_CompatibilityError` is PyXtal saying a composition
cannot be placed in that spacegroup's Wyckoff positions, and most
(spacegroup, composition) pairs are genuinely impossible. The tally exists so
that a real bug does not look like ordinary infeasibility: if you see
`0/144 ... rejected: ValueError x144`, something is broken, and without the
breakdown by exception class that would be indistinguishable from a legitimately
empty scan. The pre-2026 code swallowed every exception identically.

The scan fans out over `ProcessPoolExecutor` with `system.n_jobs` workers.
`--no-progress` suppresses the tqdm bar, which is what you want in a log file.

`manifest.csv` is the campaign's index, and is what `run`, `status` and `collect`
work from:

```
index,system,dirname,hash,spacegroup,composition,formula,n_atoms,volume_per_atom,poscar
0,Al,struct_00000_36c5d68a,36c5d68a,195,1,Al1,1,17.092637069928337,/cmmc/ptmp/hmai/assystant_tutorial/campaign/Al/structures/00000_structure.vasp
1,Al,struct_00001_bd3b4596,bd3b4596,195,2,Al2,2,17.273670766973517,/cmmc/ptmp/hmai/assystant_tutorial/campaign/Al/structures/00001_structure.vasp
```

Re-running `gen` regenerates the POSCARs and the manifest. With `system.seed` set
this is idempotent — identical structures, identical hashes, identical directory
names. With `seed: null` it produces a *different* structure set, whose hashes
differ, and therefore whose run directories differ; the old directories are left
on disk untouched. That is the content-hashing design working as intended, not a
bug, but it does mean an unseeded re-`gen` orphans the previous campaign's
directories rather than resuming them.

---

## `run`

```
assystant run camp.yaml [--limit N] [--dry-run] [--from-scratch]
```

Walks each system's manifest, renders one sbatch script per structure into that
structure's run directory, and submits it.

```
$ assystant run Al_tutorial.yaml --limit 4
Submitted batch job 21622728
Submitted batch job 21622729
Submitted batch job 21622730
Submitted batch job 21622731
submitted 4, skipped 0 queued / 0 finished
```

**`run` is idempotent, and is therefore also the resume command.** A structure is
skipped when either

* its directory is already the working directory of one of your queued or running
  jobs, matched against `squeue -u $USER -o %Z` — which is why
  `#SBATCH --chdir` is load-bearing rather than cosmetic; or
* its directory already contains `df_ASSYST_jobs.pkl`, the file
  `run_ASSYST_on_structure` writes on success.

Running it again while the first batch is in the queue picks up where it left off:

```
$ assystant run Al_tutorial.yaml --limit 2
Submitted batch job 21622732
Submitted batch job 21622733
submitted 2, skipped 4 queued / 0 finished
```

and once structures have finished, they are skipped as done:

```
$ assystant run Fe_smoke.yaml --dry-run
rendered 1, skipped 0 queued / 1 finished
```

**`--limit N`** caps the number of structures *acted on*, not the number
inspected — skipped structures do not count against it. It is how you start a
campaign safely; see
[`operations.md`](operations.md#start-every-campaign-with---limit-1).

**`--dry-run`** renders the sbatch scripts without submitting them and without
querying the scheduler. It still writes `<root>/assystant_config.yaml`, and
deliberately so: that file is what the rendered script's `--config` argument
points at, so without it you could not run the rendered script by hand — which is
the main thing `--dry-run` is for. The staleness check runs either way.

**`--from-scratch`** accepts a changed config and **deletes each structure's run
directory in full**, including `executorlib_cache` and any finished
`df_ASSYST_jobs.pkl`, before resubmitting. Combined with `--dry-run` it destroys
nothing and reports what it would have destroyed:

```
$ assystant run Fe_smoke.yaml --dry-run --from-scratch --limit 2
rendered 2, skipped 0 queued / 0 finished, would delete 2 existing run_dir(s) (not deleted -- dry run)
```

If `squeue` cannot be reached, `run` warns and submits everything rather than
silently skipping:

```
Warning: unable to query squeue (...); no jobs will be skipped as queued
```

The rendered script is plain and worth reading once:

```bash
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

# Run the ASSYST graph for exactly this structure. run_dir is absolute;
# executorlib's cache lives at <run_dir>/executorlib_cache so it is
# scoped to one structure and cannot be shared between two.
/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/python -m assystant.runner \
    --config /cmmc/ptmp/hmai/assystant_tutorial/campaign/assystant_config.yaml \
    --poscar /cmmc/ptmp/hmai/assystant_tutorial/campaign/Al/structures/00000_structure.vasp \
    --run-dir /cmmc/ptmp/hmai/assystant_tutorial/campaign/Al/struct_00000_36c5d68a \
    --system Al
```

(The runner invocation is one line in the real file; it is wrapped here for
legibility.) Every path is absolute. There is no `os.chdir` anywhere in the
package — the job's working directory comes from `--chdir`, which is also what
keeps the queued-skip working.

---

## `status`

```
assystant status camp.yaml [--csv out.csv] [--watch [SECONDS]]
```

Per-system progress: directories present, directories finished, and the manifest
total.

```
$ assystant status Al_tutorial.yaml
system          category        done  started   total     pct
-------------------------------------------------------------
Al              Unary              6        6      43  13.95%
-------------------------------------------------------------
TOTAL                              6               43  13.95%
```

`done` counts directories containing `df_ASSYST_jobs.pkl`; `started` counts
structure directories that exist at all, so `started - done` is roughly "queued,
running, or failed". `total` comes from the manifest — the authority on how many
structures the campaign has — falling back to the directory count for a legacy
campaign that has none.

`--csv` appends a timestamped block in the same shape as the existing
`/u/hmai/2024_12_05_DFTPyxtalAssystNodes/job_progress.csv`, so an existing log can
be continued:

```
$ assystant status Fe_smoke.yaml --csv job_progress.csv
...
appended to job_progress.csv

$ cat job_progress.csv
timestamp,category,run_dir,job_count,total_structures,pct,minutes_completed
2026-08-02 11:00:50.439870,Unary,Fe,1,2,50.0,15
```

`minutes_completed` is `job_count × 15`. The 15 is inherited verbatim from the
existing log; it is a nominal figure, not a measurement of anything.

`--watch [SECONDS]` reprints the table on a loop, defaulting to 60 seconds.

---

## `collect`

```
assystant collect camp.yaml -o out.pkl [--all-systems]
```

Concatenates every `df_ASSYST_jobs.pkl` under the campaign root into one
dataframe.

```
$ assystant collect Al_tutorial.yaml -o Al_tutorial_train.pkl
collected 24 rows -> Al_tutorial_train.pkl
```

Each row is stamped with `system`, `struct_index`, `structure_hash`, `layout` and
`run_dir`, then joined against the manifest to pick up `spacegroup`,
`composition`, `formula`, `n_atoms` and `volume_per_atom`. The result:

```
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

Four rows per structure here, because `n_rattle: 1, n_stretch: 1` gives one base
image plus `1 + 2 × 1 = 3` permutations.

**`scf_convergence` is per row; `isif*_converged` are per structure.** The three
ISIF flags describe the shared relaxation chain that produced the geometry, so
they are broadcast identically across every row from that structure — base images
and permutations alike, since permutations are deformations of the base images.
Filtering on them is how you exclude training data whose geometry came from a
relaxation truncated by `ionic_steps`:

```python
df = pd.read_pickle("Al_tutorial_train.pkl")
clean = df[df.isif7_converged & df.isif5_converged & df.isif2_converged]
```

**Both on-disk layouts are read.** The hashed `struct_<idx:05d>_<sha8>/` this
package writes joins to the manifest on `(system, structure_hash)`; the pre-2026
`struct_pyxtal_<idx>/` has no hash on disk and joins on `(system, index)`
instead. Existing campaign data therefore stays readable without migration, and
the `layout` column tells you which is which.

`--all-systems` scans every system directory under `root` rather than only those
named in the config — useful when `root` accumulated systems across several
config files. `-o` ending in `.csv` writes CSV instead of a pickle; note that the
`structures` column holds serialised structures, so CSV is for inspection, not for
round-tripping.
