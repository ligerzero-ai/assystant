# Troubleshooting

Real failure modes, with the text they actually print. Config errors exit with
status 2 and print to stderr prefixed `error:`.

---

## `incar` has no IBRION set

```
error: `incar` has no IBRION set, but `assyst.ionic_steps` (20) means
run_ASSYST_on_structure's relaxation stages run with NSW > 0. VASP defaults an
unset IBRION to 0 (molecular dynamics), which additionally requires POTIM and is
never what an ASSYST relaxation wants -- VASP itself refuses the job with 'Fatal
error! IBRION=0, but no entry for POTIM on file INCAR. MUST be specified!'. Set
`incar.IBRION: 2` (conjugate-gradient relaxation -- what ASSYST campaigns have
always used).
```

**Fix:** add `IBRION: 2` to the `incar` section. Full reasoning in
[`operations.md`](operations.md#ibrion-is-not-optional).

Note that `--set incar.IBRION=2` works but `--set incar.IBRION=null` does *not*
remove the key — overrides resolving to `null` are skipped entirely.

If you genuinely want molecular dynamics (`IBRION: 0`), you must also set
`POTIM`, and a separate check says so.

---

## Stale config: `run` refuses to proceed

```
error: resolved campaign config differs from the one recorded at
/cmmc/ptmp/hmai/assystant_smoke/assystant_config.yaml at: incar.ENCUT.
executorlib's cache is keyed on node lexical path with no input hashing, so
re-running an existing run_dir under changed settings would silently return
results computed with the OLD ones. Either revert the change, point `root` at a
fresh campaign directory, or pass --from-scratch (which DELETES each structure's
run_dir in full, including executorlib_cache).
```

The message names every changed key. **Three ways out, in order of preference:**

1. **Revert the change** if it was accidental — a stray `--set`, an edited YAML
   you meant to save elsewhere.
2. **Point `root` at a fresh directory** if the change was intentional. You are
   starting a different campaign; keep both.
3. **`--from-scratch`** only if the existing results are known-worthless. It
   *deletes each run directory in full*. Check first with
   `--dry-run --from-scratch`, which reports the count and destroys nothing.

Changing only the `slurm` section never triggers this — that section is exempt.

---

## `NoConvergedImagesError`

Raised inside the job, so it appears in `<run_dir>/assystant.err`:

```
collect_structures('ISIF2'): no SCF-converged image found; examined 1 image(s),
1 rejected for non-convergence.
```

Every candidate relaxation image failed the SCF-convergence filter, leaving
nothing to run statics on. The error is raised rather than returning an empty
list because an empty list would feed a zero-length `ForEach` and surface as an
opaque `IndexError` with no mention of SCF or images.

**Causes, roughly in order of likelihood:**

* **`NELM` too low for a hostile random cell.** The convergence flag is derived
  from whether the electronic loop terminated before `NELM`. Production INCARs use
  `NELM: 300` for exactly this reason.
* **Mixing parameters unsuited to random geometries.** The production INCAR's
  `AMIX: 0.01`, `BMIX: 0.0001`, `AMIX_MAG: 0.1`, `BMIX_MAG: 0.0001` exist because
  PyXtal cells are hard on the default mixer.
* **A genuinely pathological structure.** Some random cells at the compressed end
  of the strain band simply will not converge. One structure failing out of
  thousands is not a problem.

If this happens across the whole campaign rather than to isolated structures, it
is your INCAR, not your structures.

---

## `NoPermutationsGeneratedError`

```
get_ASSYST_deformed_structures produced zero permutations
(n_rattle_permutations=0 and n_stretch_permutations=0 would always do this; a
nonzero request can still land here if every generation attempt failed the
validity filter). The permutation for-loop cannot run on an empty list under
pyiron_workflow's current n=0 ForEach bug (see the module docstring); request at
least one permutation, or check core_overlap_tolerance/min_dist if a nonzero
request produced this.
```

Two distinct situations produce this:

**Both counts are zero.** `assystant` rejects that at config-validation time, so
you should see the config error instead:

```
error: `assyst.n_rattle` and `assyst.n_stretch` cannot both be 0: a
permutation-free ('relax and statics only') campaign is not supported.
pyiron_workflow 0.19.0's ForEach raises an opaque IndexError on a length-0
iterable (https://github.com/pyiron/pyiron_workflow/issues/915), so
run_ASSYST_on_structure rejects an empty permutation set with
NoPermutationsGeneratedError. Set at least one of them to >= 1.
```

**A nonzero request produced nothing.** Every candidate deformation failed the
validity filter within its attempt budget. This is a real, physical outcome for
a structure that cannot be deformed without atoms colliding — most likely a
very small, very dense cell. Levers, if it happens broadly rather than to
isolated structures:

* Reduce `shear_strain` / `triaxial_strain` from the default 0.8. At 0.8 the
  deformations are extreme and most candidates are rejected and regenerated;
  a structure with little room to spare can exhaust its budget.
* Raise `core_overlap_tolerance` above 0.3 to permit more core overlap.
* Narrow `system.strain_min`/`strain_max` so fewer structures start out
  compressed.

`filters.min_dist` cannot be changed — it is fixed at 1.0 Å upstream.

---

## Unconverged relaxations in the output

Not an error, and easy to miss. The collected dataframe carries three columns
describing the relaxation chain that produced each row's geometry:

```
isif7_converged   isif5_converged   isif2_converged
```

They are properties of the *structure*, not of the individual static, so the same
three booleans are broadcast across every row from that structure — base image and
permutations alike, since permutations are deformations of the base image.

**Filter before training:**

```python
df = pd.read_pickle("train.pkl")
clean = df[df.isif7_converged & df.isif5_converged & df.isif2_converged]
```

In the Al tutorial campaign this keeps 20 of 24 rows. A row that fails the filter
is not wrong — the energy and forces are correct for the geometry that was
calculated — but that geometry is a truncated relaxation rather than a relaxed
structure, which may or may not be what you want in a training set.

Note the separate, per-row `scf_convergence` column, which describes the static
calculation itself. Both matter, and they are independent: a converged static on
a truncated relaxation has `scf_convergence == True` and `isif2_converged == False`.

If a large fraction of your campaign fails these flags, raise `assyst.ionic_steps`
— and see
[`behaviour-changes.md`](behaviour-changes.md#ionic_steps-now-applies-to-all-three-isif-stages),
because this is exactly where the new behaviour differs from pre-2026 campaigns.

---

## `sbatch: option '--get-user-env' doesn't allow an argument`

```
sbatch: option '--get-user-env' doesn't allow an argument
subprocess.CalledProcessError: Command '['sbatch', '.../assystant_job.sh']'
    returned non-zero exit status 255.
```

Slurm 25.11 rejects the `=L` argument that older versions accepted, and every
submission fails. Fixed — rendered scripts now emit the bare
`#SBATCH --get-user-env`. If you see this, you are running a script rendered by an
older `assystant`; re-render it with `assystant run --dry-run`.

---

## `No manifest for <system>; run 'assystant gen' first`

```
No manifest for Al; run `assystant gen` first
rendered 0, skipped 0 queued / 0 finished
```

`run` walks `manifest.csv`, so `gen` has to happen first. This is a warning, not
an error — a campaign with several systems keeps going and submits the ones that
do have manifests.

Also check that `root` is what you think it is: a relative `root` resolves against
your current directory, so running `assystant` from a different place points it at
a different campaign.

---

## Configuration errors

All exit 2 and name the offending key.

```
$ assystant gen camp.yaml --set assyst.nonsense=1
error: unknown config path: assyst.nonsense
```

```
error: unknown key(s) in `assyst` section: n_rattl. Allowed: compress_dirs,
compressed_file_in_dir, core_overlap_tolerance,
image_selection_eVatom_threshold, ionic_steps, n_rattle, n_stretch,
rattle_displacement, rattle_strain, remove_calc_dirs, seed, shear_strain,
triaxial_strain
```

```
error: `system.species` must be a list, e.g. [Fe] or [[Fe], [Ni]], not the bare
string 'Al'
```

```
error: unknown element symbol in `system.species`: 'Xx'
```

```
error: `filters.min_dist` must be 1.0 (got 1.2): run_ASSYST_on_structure in
pyiron_workflow_assyst 0.2.0 hardcodes the 1.0 Angstrom floor it passes to
get_ASSYST_deformed_structures and exposes no parameter for it, so any other
value would be silently ignored.
```

Unknown keys are rejected rather than ignored specifically so that a typo'd tag
cannot silently do nothing for an entire campaign.

---

## `gen` produced 0 structures

Look at the tally line:

```
Al: 43/144 (spacegroup, composition) pairs produced a structure; rejected:
Comp_CompatibilityError x101
```

`Comp_CompatibilityError` is PyXtal declining an impossible
(spacegroup, composition) pair, and a high count is normal. A tally dominated by
some *other* exception class is a real bug. This breakdown exists because the
pre-2026 code caught every exception identically, which made "your composition
range is infeasible" and "your install is broken" look the same.

If everything is `Comp_CompatibilityError` and you got nothing, your scan is
genuinely infeasible — widen `spacegroups`, or check `min_atoms`/`max_atoms`
against `stoich_min`/`stoich_max` for a multi-component system, where the
intersection can easily be empty.

---

## Jobs fail immediately with a POTCAR error

`assystant` never names a POTCAR; `pyiron_workflow_vasp` resolves them from
`$PYIRON_VASP_CONFIG` or `~/.pyiron_vasp_config`. If that file is missing or
points at a nonexistent library, every job fails at input-writing time. Verify it
independently before blaming the campaign — see
[`installation.md`](installation.md#the-potcar-library).

---

## MPI errors in `vasp.log`

```
MPIR_pmi_init(169)...: PMI2_Job_GetId returned 14
```

Intel MPI is trying to reach a Slurm PMI2 server that is not there — you are
running outside an allocation with the site `impi` module loaded.

```
find_provider(2819)..........: OFI fi_getinfo() failed
    (ofi_init.c:2819:find_provider:No data available)
```

No OFI fabric provider — again, typically a login node.

Both are covered by the smoke-test recipe in
[`operations.md`](operations.md#smoke-testing-without-a-scheduler). Inside a real
allocation with the default `launcher: null` and the standard modules, neither
should occur.

---

## Where to look when a job fails

```
<run_dir>/assystant.out      runner stdout; ends with "ASSYST finished: N rows"
<run_dir>/assystant.err      the traceback, if any
<run_dir>/<stage>.tar.gz     the VASP directory, including vasp.log and OUTCAR
<run_dir>/pyiron.log         graph-level logging
```

A structure that finished has `df_ASSYST_jobs.pkl`; one that did not, does not —
which is exactly what `status` counts and what makes `run` safe to re-issue as a
resume. With `compress_dirs: true` the VASP directories are tarballs, so inspect
them with `tar xzOf ISIF7.tar.gz ISIF7/OUTCAR | less`.
