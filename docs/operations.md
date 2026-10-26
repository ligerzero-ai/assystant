# Operational guidance

Everything here exists because something went wrong once. None of it is
theoretical.

## Content-hashed run directories

Every structure runs in

```
<root>/<system>/struct_<idx:05d>_<sha8>/
```

where `sha8` is the first eight hex characters of the SHA-256 of the canonical
POSCAR string, recorded in the `hash` column of `manifest.csv`. So structure 0 of
the Al tutorial lives in `struct_00000_36c5d68a/`.

**This is a correctness mechanism, not a naming convention.**

The ASSYST graph runs under `pyiron_workflow`, which caches node results through
executorlib. That cache is keyed on the node's **lexical path within the graph**,
and it does **not hash the node's inputs**. A cache entry says "the node at this
position in this run directory produced this result" — nothing in the key
describes *what the node was given*.

Now consider the obvious alternative, `struct_00005/`. You run `gen`, submit,
some jobs finish. Later you re-run `gen` — a different seed, a widened strain
band, a changed atom-count range — and the structure that lands at index 5 is a
completely different crystal. Its run directory is still `struct_00005/`, and
that directory still holds a populated `executorlib_cache` from the previous
structure. The new job starts, the graph reaches the ISIF7 node, executorlib
finds a cache entry at that lexical path, and returns it. **You get the previous
structure's VASP results, attributed to the new structure, with no error and no
warning.** They then flow into your training set.

Deriving the directory name from the structure's content removes that failure
mode by construction: a changed structure cannot land in a populated namespace,
because its hash differs and so does its directory. There is no runtime check to
forget and no invariant to maintain.

Two rules follow, and both are departures from the pre-2026 scripts:

* **No `os.chdir` anywhere in the package.** Every rendered path is absolute. The
  job's working directory comes from `#SBATCH --chdir`, which additionally keeps
  `squeue -o %Z` reporting the structure directory — the thing the queued-skip
  matches on. `calculate_assyst.py` used `os.chdir` before `sbatch` and passed
  relative work directories, so its correctness depended on process working
  directory.
* **The executorlib cache lives at `<run_dir>/executorlib_cache`**, scoped to
  exactly one structure and unable to be shared between two.

The remaining hole is that a structure hash cannot catch a changed INCAR. That is
what the staleness guard is for.

## The staleness guard

`run` writes the fully resolved config to `<root>/assystant_config.yaml` and
compares against it on every subsequent invocation. A calculation-affecting
difference aborts:

```
$ assystant run camp.yaml --dry-run --limit 1 --set incar.ENCUT=350
error: resolved campaign config differs from the one recorded at
/cmmc/ptmp/hmai/assystant_smoke/assystant_config.yaml at: incar.ENCUT.
executorlib's cache is keyed on node lexical path with no input hashing, so
re-running an existing run_dir under changed settings would silently return
results computed with the OLD ones. Either revert the change, point `root` at a
fresh campaign directory, or pass --from-scratch (which DELETES each structure's
run_dir in full, including executorlib_cache).
```

Same mechanism as above, different trigger: the directory name encodes the
structure, so a changed structure is caught by construction — but the same
structure recomputed at `ENCUT: 350` occupies the same directory, and the cache
would hand back the `ENCUT: 300` numbers.

**`slurm` is exempt from the comparison. Everything else participates** —
`root`, `system`, `incar`, `magmom`, `assyst`, `filters`, `vasp`. (The
stoichiometry bounds in `system` are compared through the composition sets
they imply, as `system.compositions.<system>`, so the same scan written two
ways is not a difference.)

```
$ assystant run camp.yaml --dry-run --limit 1 --time 08:00:00
rendered 1, skipped 0 queued / 0 finished
```

Wall time, task count and partition change how the work reaches the scheduler,
not what is computed. Since the only way past the guard is `--from-scratch`, and
`--from-scratch` deletes data, making a `--time` bump demand it would be actively
harmful.

### `--from-scratch` deletes run directories in full

Not "clears the cache". Not "removes the calculation directories". `shutil.rmtree`
on each structure's run directory, including `executorlib_cache`, including any
finished `df_ASSYST_jobs.pkl`, including the compressed VASP archives. On a
campaign that has been running for a week, that is a week of compute.

Half-measures would not work: leaving the cache would defeat the entire purpose,
and leaving results computed under the old settings would leave your dataset
inconsistent in a way nothing downstream could detect.

**Check what it would do first.** `--dry-run --from-scratch` deletes nothing and
reports the count:

```
$ assystant run camp.yaml --dry-run --from-scratch --limit 2
rendered 2, skipped 0 queued / 0 finished, would delete 2 existing run_dir(s) (not deleted -- dry run)
```

**Usually you want a fresh `root` instead.** If the campaign has real results in
it, changing physics means you are starting a different campaign; point `root` at
a new directory and keep both. `--from-scratch` is for the case where the
existing results are known-worthless — a wrong INCAR caught after the first few
jobs, say.

## `IBRION` is not optional

Whenever `assyst.ionic_steps > 0` — which is every real campaign — the config is
rejected at load time if `incar.IBRION` is absent:

```
$ assystant gen no_ibrion.yaml
error: `incar` has no IBRION set, but `assyst.ionic_steps` (20) means
run_ASSYST_on_structure's relaxation stages run with NSW > 0. VASP defaults an
unset IBRION to 0 (molecular dynamics), which additionally requires POTIM and is
never what an ASSYST relaxation wants -- VASP itself refuses the job with 'Fatal
error! IBRION=0, but no entry for POTIM on file INCAR. MUST be specified!'. Set
`incar.IBRION: 2` (conjugate-gradient relaxation -- what ASSYST campaigns have
always used).
```

The graph sets `NSW = ionic_steps` on all three relaxation stages. With `NSW > 0`
and no `IBRION`, VASP defaults `IBRION` to 0, which is molecular dynamics — never
what an ASSYST relaxation wants — and which additionally requires `POTIM`. Without
`POTIM`, VASP refuses to start:

```
Fatal error! IBRION=0, but no entry for POTIM on file INCAR. MUST be specified!
----> I REFUSE TO CONTINUE WITH THIS SICK JOB ... BYE!!! <----
```

Catching this at config-load time costs a second. Discovering it from the
scheduler costs a queue wait per structure, times however many structures you
submitted.

**`IBRION: 2`** — conjugate gradient — is what every ASSYST campaign has used.

There is deliberately no auto-default. Injecting a physics choice into someone's
INCAR silently is a worse failure mode than an explicit, actionable error. Note
also that `--set incar.IBRION=null` will *not* trigger this check: overrides
resolving to `null` are skipped, so the key is simply not overridden. Edit the
YAML.

## `vasp.launcher` and running outside an allocation

`launcher: null`, the default, means the SLURM launcher:

```
srun -c 1 -n <slurm.ntasks> --hint=nomultithread
```

Any other string replaces it verbatim, with `{ntasks}` substituted if present.
`""` invokes the binary with no launcher at all. The rendered command, at the
defaults, is:

```
module load intel/2024.0
module load impi/2021.11
module load mkl/2024.0
cat > custodian_vasp.py << 'PYEOF'
...
PYEOF
/cmmc/ptmp/hmai/mambaforge/envs/pymatgen/bin/python custodian_vasp.py \
    srun -c 1 -n 16 --hint=nomultithread .../vasp_std >> vasp.log
```

### Smoke testing without a scheduler

To prove a new environment actually runs DFT — before queueing anything — you can
execute a rendered job script directly on a login node. Two settings have to
change, and both for reasons that are worth understanding, because the failure
modes are opaque:

```yaml
vasp:
  custodian: false
  launcher: "env I_MPI_FABRICS=shm"
  modules: []
```

**`modules: []`.** The site `impi` module wires Intel MPI to Slurm's PMI2:

```
$ module load intel/2024.0 impi/2021.11
$ env | grep -E '^(I_MPI_PMI_LIBRARY|I_MPI_HYDRA_BOOTSTRAP|SLURM_MPI_TYPE)='
I_MPI_HYDRA_BOOTSTRAP=slurm
I_MPI_PMI_LIBRARY=/usr/lib64/libpmi2.so.0
SLURM_MPI_TYPE=pmi2
```

Outside an allocation there is no PMI2 server to talk to, so `MPI_Init` aborts
before VASP prints anything:

```
Abort(1090063) on node 0 (rank 0 in comm 0): Fatal error in PMPI_Init:
    Unknown error class, error stack:
MPIR_Init_thread(189):
MPID_Init(1454)......:
MPIR_pmi_init(169)...: PMI2_Job_GetId returned 14
```

The modules resolve perfectly well on the login node — this is not a
module-availability problem, it is that loading them tells MPI to expect a
scheduler that is not there.

**`launcher: "env I_MPI_FABRICS=shm"`.** This replaces `srun`, which cannot run
outside an allocation. But dropping the modules leaves the binary using its own
bundled Intel MPI, which defaults to the OFI netmod and finds no provider on a
login node:

```
Abort(2138767) on node 0 (rank 0 in comm 0): Fatal error in PMPI_Init:
    Unknown error class, error stack:
MPIDI_OFI_mpi_init_hook(1584):
open_fabric(2663)............:
find_provider(2819)..........: OFI fi_getinfo() failed
    (ofi_init.c:2819:find_provider:No data available)
```

Pinning the fabric to shared memory sidesteps the fabric search entirely. With
both changes, VASP starts.

**`custodian: false`** simply avoids needing custodian importable for a test whose
point is the VASP invocation.

A complete smoke campaign is in [`examples/Fe_smoke.yaml`](../examples/Fe_smoke.yaml)
— two structures, two ionic steps, `ENCUT: 200`, `PREC: Low`. It runs the full
ISIF7 → ISIF5 → ISIF2 chain plus one static and its three permutations in 42
seconds:

```bash
assystant gen Fe_smoke.yaml --no-progress
assystant run Fe_smoke.yaml --dry-run --limit 1
bash campaign/Fe/struct_00000_*/assystant_job.sh
```

```
Fe: 2 structures -> /cmmc/ptmp/hmai/assystant_smoke/campaign/Fe
rendered 1, skipped 0 queued / 0 finished
...
ASSYST finished: 4 rows -> .../struct_00000_7da6c043/df_ASSYST_jobs.pkl
```

Note the last step runs the rendered sbatch script *as a shell script* — the
`#SBATCH` lines are comments to bash, so it simply executes the runner in the
foreground.

These settings are for proving the plumbing works. They are not physics.

## Start every campaign with `--limit 1`

A `--dry-run` proves the script renders. It cannot prove that `vasp.binary`
exists on the compute nodes, that `vasp.modules` resolve there, that
`custodian_python` has `custodian` importable, that the POTCAR library is
readable, or that your INCAR converges on a random PyXtal cell.

So:

```bash
assystant run camp.yaml --dry-run --limit 1   # read the script
assystant run camp.yaml --limit 1             # submit exactly one
# wait; confirm df_ASSYST_jobs.pkl appears and the numbers look sane
assystant run camp.yaml                       # submit the rest
```

The last command skips the one already done. A production unary campaign is
around 2,700 structures per element; a mistake found at structure 1 costs
minutes, and the same mistake found after a full submission costs a queue's worth
of wasted allocation and a `--from-scratch` to clean up.

`--limit N` counts structures *acted on*, not inspected — skipped ones do not
consume the budget — so it behaves sensibly on a resume.

## There is no "relax and statics only" mode

`n_rattle: 0` together with `n_stretch: 0` is rejected:

```
$ assystant gen camp.yaml --n-rattle 0 --n-stretch 0
error: `assyst.n_rattle` and `assyst.n_stretch` cannot both be 0: a
permutation-free ('relax and statics only') campaign is not supported.
pyiron_workflow 0.19.0's ForEach raises an opaque IndexError on a length-0
iterable (https://github.com/pyiron/pyiron_workflow/issues/915), so
run_ASSYST_on_structure rejects an empty permutation set with
NoPermutationsGeneratedError. Set at least one of them to >= 1.
```

This is an upstream limitation, not a design choice. `pyiron_workflow` 0.19.0's
`ForEach` cannot scatter a zero-length iterable — it raises
`IndexError: list index out of range` from deep inside the graph machinery, with
no mention of permutations or of which loop. (The same bug wraps elements in a
spurious 1-tuple at length exactly 1, which `pyiron_workflow_assyst` works around
with an `unwrap_singleton` node; the length-0 case cannot be worked around,
because the loop body never runs at all.)

`assystant` rejects the combination at config-validation time so the message names
the YAML keys you would actually edit. Until
[pyiron/pyiron_workflow#915](https://github.com/pyiron/pyiron_workflow/issues/915)
is fixed, set at least one of them to 1.

Note that a *nonzero* request can still produce zero permutations if every
generated candidate fails the validity filter, which surfaces as
`NoPermutationsGeneratedError` at runtime — see
[`troubleshooting.md`](troubleshooting.md#nopermutationsgeneratederror).

## Housekeeping at scale

`compress_dirs: true` and `remove_calc_dirs: true` are the defaults and should
stay that way. A production campaign is tens of thousands of VASP directories;
leaving them uncompressed is measured in terabytes and in inode-quota failures.

Relatedly, `assystant` never passes `files_to_be_deleted` to the graph.
`vasp_job` treats `None` as "delete CHG/CHGCAR/WAVECAR" — the documented default —
and `[]` as "delete nothing". Passing `[]` would retain those files across every
calculation in a campaign, tens of terabytes, with no error anywhere. A test
guards against it being reintroduced.
