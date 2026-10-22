# The campaign YAML

One file describes one campaign. It is parsed into typed dataclasses, so an
unknown key is an error rather than a silently ignored typo:

```
$ assystant gen camp.yaml --set assyst.nonsense=1
error: unknown config path: assyst.nonsense
```

Every section is optional except `system.species` and — whenever
`assyst.ionic_steps > 0`, which is always in practice — `incar.IBRION`.
Everything else has a default, listed below.

The authoritative schema is the "Campaign YAML" section of
[`2026_07_31_assystant_design.md`](2026_07_31_assystant_design.md); this document
expands it key by key.

## Overrides and precedence

Any scalar is addressable by its dotted path from the command line, and
precedence is **CLI flag > YAML > default**:

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

`--set` parses its value as YAML, so `--set vasp.custodian=false` gives a boolean
and `--set vasp.modules="[]"` an empty list. One caveat: an override whose value
resolves to `null` is *skipped*, not applied — `--set incar.IBRION=null` will not
remove `IBRION` from your INCAR. Delete the key from the YAML instead.

## `root`

Default: the current working directory. The campaign directory. Everything the
campaign writes lives underneath it:

```
<root>/
  assystant_config.yaml            the resolved config `run` was launched with
  <system>/
    manifest.csv
    structures/00000_structure.vasp ...
    struct_00000_<sha8>/           one per structure, content-hashed
```

Made absolute on load, so a relative `root` is resolved against your current
directory at the time you run the command — write it absolute to avoid surprises.
Leaving `root` out entirely has the same effect as `root: .`: each system lands
in `./<system>/` and `run` records its config in `./assystant_config.yaml`. The
bundled tutorial template (`assystant example Al_tutorial`) uses the relative
`root: ./Al_campaign`, so run all its commands from the same directory.

Point `root` at a *fresh* directory for a campaign with different physics. It is
the cheapest way to avoid the staleness guard and the only way to keep two
parameter sets side by side.

## `system` — what to generate

The PyXtal scan that `gen` walks.

```yaml
system:
  species: [Fe]
  min_atoms: 1
  max_atoms: 12
  stoich_min: 1
  stoich_max: 12
  spacegroups: [1, 230]
  strain_min: 0.8
  strain_max: 1.2
  eq_volume_source: null
  perturb: 0.1
  seed: null
  n_jobs: null
```

**`species`** — **required**. Either a flat list of element symbols, which is one
system (`[Fe, C]` is the binary Fe–C), or a list of lists, which is several
systems in one campaign (`[[Fe], [Ni], [Fe, C]]`). The list-of-lists form is what
replaces the hand-maintained `submit_temp.sh` with one line per element; a
forty-element unary campaign is one file. Symbols are validated against
pymatgen's periodic table, and a repeated element within one system is rejected.
Each system gets its own directory named by joining its symbols with underscores:
`Fe`, `Fe_C`, `Fe_B_C`.

**`min_atoms` / `max_atoms`** — default `1` / `12`. Inclusive bounds on the atom
count per generated cell.

**`stoich_min` / `stoich_max`** — default `1` / `12`. Per-species count bounds for
multi-component systems: compositions are the Cartesian product of
`range(stoich_min, stoich_max + 1)` over the species, filtered to those with
`min_atoms <= sum <= max_atoms`. **They do not apply to unary systems**, where
compositions are simply `[(n,) for n in range(min_atoms, max_atoms + 1)]` —
for one species the stoichiometry *is* the atom count, so applying both bounds
would only let you contradict yourself.

**`spacegroups`** — default `[1, 230]`, i.e. all of them. A `[min, max]` pair; a
bare integer means that one group; a `{min:, max:}` mapping also works. Every
group in the range is crossed with every composition. Most pairs are
geometrically impossible and PyXtal rejects them — see
[`commands.md`](commands.md#gen) on reading the rejection tally.

**`strain_min` / `strain_max`** — default `0.8` / `1.2`. The volume-per-atom band,
as multiples of the equilibrium volume, that generated structures are rescaled
into. A structure already inside the band is left untouched; one outside is
scaled to whichever bound it violated. Widening the band samples more compressed
and expanded configurations, which is usually what you want for potential
training — at the cost of more SCF trouble at the compressed end.

**`eq_volume_source`** — default `null`, meaning the bundled Materials Project
Voronoi-volume table (`assystant/data/EleBulk_MatProj_VorVol.csv`). Alternatively
an explicit mapping, `{Fe: 11.8, C: 5.7}`, or a path to a CSV with `element` and
`min_VorNN_tot_vol` columns. Species absent from the table are left unscaled
rather than guessed at, so a campaign on an exotic element silently gets PyXtal's
own volume choice unless you supply a mapping.

**`perturb`** — default `0.1` (Ångström). A random displacement applied to every
generated structure to break exact symmetry, without which a perfectly symmetric
random cell can sit on a saddle and refuse to relax. Applied at *generation*
time, which is a change from the pre-2026 scripts — see
[`behaviour-changes.md`](behaviour-changes.md#the-perturbation-moved-to-generation-time).
Set `0` to disable.

**`seed`** — default `null`, which draws a fresh `secrets.randbits(32)` per task
and reproduces the historical behaviour. An integer makes generation
deterministic: the same seed and the same scan parameters produce byte-identical
POSCARs and therefore identical content hashes and directory names. Unlike in the
pre-2026 notebooks, this actually works — see
[`behaviour-changes.md`](behaviour-changes.md#the-generation-seed-now-works).

**`n_jobs`** — default `null` → `os.cpu_count()`. Worker processes for the scan.
BLAS and OpenMP thread counts are pinned to 1 in every worker regardless, and
unconditionally, so an `OMP_NUM_THREADS` inherited from your login shell cannot
reintroduce the oversubscription that pinning exists to prevent.

## `incar` — the base VASP settings

A plain mapping of INCAR tag to value; keys are upper-cased on load. There is no
default INCAR — whatever you write here is the campaign's base, and the ASSYST
graph derives four INCARs from it:

| stage | derived from base by |
|---|---|
| ISIF7 | `NSW = assyst.ionic_steps`, `ISIF = 7` |
| ISIF5 | `NSW = assyst.ionic_steps`, `ISIF = 5` |
| ISIF2 | `NSW = assyst.ionic_steps`, `ISIF = 2` |
| statics | `NSW = 0`, `ISIF` removed, `KSPACING = 0.25`, `EDIFF = 1e-5`, `EDIFFG = 1e-4`, `LREAL = False` |

Two consequences worth internalising. First, the base `NSW` you write is
**never used** — all three relaxation stages take `assyst.ionic_steps`, and the
statics take 0. It is kept in the example configs for provenance only. Second,
the statics override `KSPACING` to 0.25 unconditionally, so the k-point density
of your training data is fixed regardless of what the base INCAR says; the base
`KSPACING` governs only the relaxation stages, where cheap sampling is the point.

Two tags `assystant` manages for you:

* **`SYSTEM`** is stamped with the run-directory name, so an OUTCAR identifies
  which structure produced it.
* **`MAGMOM`** is written from the `magmom` section, but **only when
  `ISPIN: 2`**. With `ISPIN: 1` any `MAGMOM` is removed, because writing one
  anyway (as the pre-2026 scripts did) makes a nonmagnetic INCAR read as though
  it were magnetic.

**No `KPOINTS` file is written, ever.** k-point sampling comes entirely from
`KSPACING`. If your base INCAR omits it, the relaxation stages run at VASP's own
`KSPACING` default of 0.5, which may or may not be what you meant. Set it
explicitly.

**`IBRION` is required** whenever `assyst.ionic_steps > 0`, and is rejected at
config-load time if missing. `IBRION: 2` is what ASSYST campaigns use; the
reasoning is in [`operations.md`](operations.md#ibrion-is-not-optional).

A production INCAR, from [`examples/Fe_unary.yaml`](../examples/Fe_unary.yaml):

```yaml
incar:
  ALGO: Normal
  AMIX: 0.01
  AMIX_MAG: 0.1
  BMIX: 0.0001
  BMIX_MAG: 0.0001
  EDIFF: 1.0e-4
  EDIFFG: 1.0e-4
  ENCUT: 400
  GGA: Pe
  IBRION: 2
  ISIF: 7
  ISMEAR: 1
  ISPIN: 2
  ISTART: 0
  KPAR: 4
  LORBIT: 10
  LPLANE: false
  LREAL: false
  NCORE: 4
  NELM: 300
  NSW: 500
  PREC: Accurate
  SIGMA: 0.2
```

The aggressive mixing parameters (`AMIX`, `BMIX`, …) are there because random
PyXtal cells are hostile to the default mixer.

## `magmom` — the MAGMOM string

```yaml
magmom:
  host: null
  host_moment: 3.0
  solute_moment: -0.5
```

Only consulted when `incar.ISPIN` is 2. The MAGMOM string is built positionally
from the structure's site order — consecutive runs of the same species are
collapsed into `count*moment` terms — so `Fe Fe C Fe` with the defaults becomes
`2*3.0 1*-0.5 1*3.0`.

**`host`** — default `null`, meaning "the first species of this system". That
default is what lets a multi-system unary campaign work in one file: with
`species: [[La], [Ce], [Pr]]` and `host: null`, each element is its own host.
Set it explicitly only for a multi-component campaign where one specific element
is the matrix.

**`host_moment` / `solute_moment`** — defaults `3.0` / `-0.5`. Initial moments in
µB for the host and for everything else. Note that
[`examples/Fe_unary.yaml`](../examples/Fe_unary.yaml) uses `-1.0` for the solute;
the dataclass default is `-0.5`.

## `assyst` — the ASSYST graph parameters

Forwarded, essentially unchanged, to `run_ASSYST_on_structure`.

```yaml
assyst:
  ionic_steps: 100
  n_rattle: 5
  n_stretch: 5
  shear_strain: 0.8
  triaxial_strain: 0.8
  rattle_displacement: 0.1
  rattle_strain: 0.05
  core_overlap_tolerance: 0.3
  image_selection_eVatom_threshold: -1
  compress_dirs: true
  compressed_file_in_dir: false
  remove_calc_dirs: true
  seed: null
```

**`ionic_steps`** — default `100`. `NSW` for the relaxation stages. **It applies
to all three of them**, which differs from pre-2026 campaigns and makes ISIF5/ISIF2
data not directly comparable; see
[`behaviour-changes.md`](behaviour-changes.md#ionic_steps-now-applies-to-all-three-isif-stages).
Must be >= 1.

**`n_rattle` / `n_stretch`** — default `5` / `5`. Permutations generated *per
relaxed image*. `n_rattle` counts rattled variants; `n_stretch` counts triaxially
strained variants **and** sheared variants separately, so the total is
`n_rattle + 2 × n_stretch` — 15 at the defaults, 3 at `n_rattle: 1, n_stretch: 1`.
Each candidate is validity-checked and regenerated if it fails, with a budget of
`100 × count` attempts for each of the three families, so a structure that cannot
be validly deformed yields fewer permutations rather than looping forever.

**Both cannot be 0.** A permutation-free "relax and statics only" campaign is
rejected at config-validation time; see
[`operations.md`](operations.md#there-is-no-relax-and-statics-only-mode).

**`shear_strain` / `triaxial_strain`** — default `0.8` / `0.8`. The half-width of
the uniform distribution the deformation matrices are drawn from. Triaxial strain
draws three diagonal entries from `1 + U(-t, t)`; shear draws the six off-diagonal
entries from `U(-s, s)` with the diagonal left at 1. At `0.8` these are very
aggressive — deliberately, since the point is to sample far from equilibrium —
and most candidates are rejected by the validity filter and regenerated.

**`rattle_displacement` / `rattle_strain`** — defaults `0.1` Å / `0.05`. A rattle
applies a per-atom Gaussian displacement with standard deviation
`rattle_displacement`, then a diagonal cell strain drawn from
`1 + U(-rattle_strain, rattle_strain)`.

**`core_overlap_tolerance`** — default `0.3`. The fractional overlap of PAW core
spheres a candidate structure may have and still be accepted: a pair of species is
rejected when their separation falls below `(1 - tolerance) × (RCORE_a + RCORE_b)`.
Larger values are more permissive. The radii come from the POTCARs actually in
use — see
[`behaviour-changes.md`](behaviour-changes.md#rcore-is-read-from-the-potcar-actually-used).

**`image_selection_eVatom_threshold`** — default `-1`, meaning "keep only the
final relaxation image". A positive value instead walks the ISIF2 relaxation
trajectory and keeps every image whose energy per atom differs from the last kept
one by more than the threshold, which multiplies your statics count by however
many images that selects. Images whose SCF did not converge are dropped either
way.

**`compress_dirs` / `remove_calc_dirs`** — defaults `true` / `true`. After each
VASP calculation the work directory is tarred to `<name>.tar.gz` and the
uncompressed directory removed. At campaign scale this is not optional
housekeeping: a production campaign is tens of thousands of VASP directories.
`compressed_file_in_dir` (default `false`) controls whether the archive is left
inside the directory rather than beside it.

**`seed`** — default `null`. Seeds the permutation generator inside
`get_ASSYST_deformed_structures`. Distinct from `system.seed`, which seeds
structure generation.

## `filters` — structure validity

```yaml
filters:
  min_dist: 1.0
  min_dist_backend: neighbor_list
```

**`min_dist`** — **must be 1.0**. `run_ASSYST_on_structure` in
`pyiron_workflow_assyst` 0.2.0 hardcodes the 1.0 Å floor it passes down and
exposes no parameter for it. The key is kept for provenance and *rejected* if
changed, rather than accepted and silently ignored. Making it settable needs a
parameter added upstream.

**`min_dist_backend`** — default `neighbor_list`. Chooses how the minimum
interatomic distance is computed, and therefore which permutations enter your
dataset. `neighbor_list` performs a real periodic neighbour search; `mic` uses
pymatgen's minimum-image distance matrix with the diagonal masked out, which
discards every atom's distance to its own periodic image. Use `mic` only to
reproduce a pre-2026 campaign exactly. The consequences are quantified in
[`behaviour-changes.md`](behaviour-changes.md#the-minimum-distance-filter-does-a-real-neighbour-search).

## `slurm` — how the work reaches the scheduler

```yaml
slurm:
  partition: s.cmmg
  ntasks: 64
  cpus_per_task: 1
  time: "48:00:00"
  mem_per_cpu: 2000MB
  env: null
  account: null
  extra_directives: []
```

One job per structure, each a single `ntasks`-task allocation that runs the whole
ASSYST chain internally. This keeps scheduler load at one job per structure rather
than one per VASP call.

**`partition`** — default `s.cmmg`. **`ntasks`** — default `64`; also the task
count `srun` is given for VASP. **`cpus_per_task`** — default `1`.
**`time`** — default `"48:00:00"`; quote it, or YAML reads `48:00:00` as a
sexagesimal integer. **`mem_per_cpu`** — default `"2000MB"`.

**`env`** — default `null` → the interpreter running `assystant` itself. A conda
prefix whose `bin/python` runs the in-job runner. Set this whenever you invoke
`assystant` from a different environment than the one the jobs should use.
Leaving it unset is what makes a config portable: the rendered job script then
names whatever interpreter `assystant` was run with.

**`account`** — default `null`; emitted as `#SBATCH --account=` when set.
**`extra_directives`** — default `[]`; a list of verbatim `#SBATCH` argument
strings, e.g. `["--reservation=benchmarking"]`.

**This whole section is exempt from the staleness guard** — see below.

## `vasp` — how VASP is invoked

```yaml
vasp:
  binary: vasp_std
  custodian: true
  custodian_python: null
  modules: [intel/2024.0, impi/2021.11, mkl/2024.0]
  max_errors: 10
  launcher: null
```

**`binary`** — default `vasp_std`; required to be non-empty. Write an absolute
path.

**Placeholders.** A string value beginning `<EDIT` — as in the bundled
templates' `slurm.partition` and `vasp.binary` — is a placeholder. It is
accepted by `gen` and `run --dry-run`, but a real `run` refuses to submit while
any is left, naming the dotted paths still to fill in. Replacing a placeholder
recorded by an earlier `run --dry-run` does not trip the
[staleness guard](operations.md#the-staleness-guard); nothing was ever computed with it.

**`custodian`** — default `true`. Drives VASP through a custodian wrapper with
`VaspErrorHandler`, `NonConvergingErrorHandler` and `FrozenJobErrorHandler`, which
is what pre-2026 campaigns did and what makes a campaign survive the SCF failures
random cells produce. Set `false` to invoke the binary directly — appropriate for
a smoke test, not for a campaign.

**`custodian_python`** — default `null` → `slurm.env`. The interpreter running the
custodian wrapper, which must have `custodian` importable. It is frequently *not*
the runner environment: the example config points it at
`/cmmc/ptmp/hmai/mambaforge/envs/pymatgen`. Not validated at submit time.

**`modules`** — default `[intel/2024.0, impi/2021.11, mkl/2024.0]`, emitted as
`module load` lines before VASP runs. Set `[]` to load nothing.

**`max_errors`** — default `10`. Passed to custodian.

**`launcher`** — default `null`, meaning the SLURM default
`srun -c 1 -n <slurm.ntasks> --hint=nomultithread`. Any other string replaces it
verbatim, with `{ntasks}` substituted. `""` invokes the binary with no launcher at
all. See [`operations.md`](operations.md#vasplauncher-and-running-outside-an-allocation).

## The staleness guard: which sections participate

`run` writes the fully resolved config to `<root>/assystant_config.yaml` and, on
every later invocation, compares against it. A calculation-affecting difference
aborts with `StaleConfigError` naming the changed keys.

**`slurm` is exempt. Everything else participates**: `root`, `system`, `incar`,
`magmom`, `assyst`, `filters`, `vasp`.

The exemption is deliberate. Wall time, task count and partition change how the
work reaches the scheduler, not what is computed — and since the only way past
the guard is `--from-scratch`, which *deletes run directories in full*, making a
`--time` bump demand it would be worse than useless. Everything else can change
the numbers, and the executorlib cache under an existing run directory would
serve results computed under the old settings. The full reasoning is in
[`operations.md`](operations.md#the-staleness-guard).
