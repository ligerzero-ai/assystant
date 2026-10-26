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

### Site files: `--site FILE`

A second YAML merged over the campaign YAML before anything else, so what is
specific to one machine — `slurm`, `vasp`, perhaps `root` — stays out of the
physics file. Nested mappings merge key by key, scalars and lists are
replaced, and the flag repeats (later files win). Full precedence:
**CLI flag > site file(s) > campaign YAML > defaults**.
`assystant example site` writes a starting site file;
[`new-machine.md`](new-machine.md) is the walkthrough.

### Environment variables

`${VAR}`, `$VAR` and a leading `~` are expanded in `root` and in every string
of `slurm` and `vasp` (list items included), after overrides and before
validation, so `binary: ${VASP_ROOT}/bin/vasp_std` works. The physics sections
are left alone. A reference to an unset variable is a `ConfigError` naming the
key, raised by every command including `gen`, because an empty `vasp.binary`
would otherwise fail only inside the allocation. The recorded config holds the
expanded values.

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
  stoich_min: null
  stoich_max: null
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
`Fe`, `Fe_C`, `Fe_B_C`. [`multi-element.md`](multi-element.md) walks through a
campaign with several systems, including what `--limit`, `status` and `collect`
do with them and why an element is best added before the first `run`.

**`min_atoms` / `max_atoms`** — default `1` / `12`. Inclusive bounds on the atom
count per generated cell.

**`stoich_min` / `stoich_max`** — default `null` / `null`, meaning **the full
composition range**: `stoich_min` resolves to 1 and `stoich_max` to
`max_atoms`. Per-species count bounds for multi-component systems: every
species takes every count in `[stoich_min, stoich_max]`, and the combinations
with `min_atoms <= total <= max_atoms` are scanned. With the defaults that is,
for a cell of `n` atoms, every split with each species present — a binary
scans `1..n-1` of each species for every `n` from 2 to `max_atoms`, a ternary
every three-way split from `n = 3` up — whatever `max_atoms` is. Set either
bound explicitly only to *narrow* the scan (an interstitial-poor Fe–C set with
`stoich_max: 2` on carbon is not expressible, since the bounds apply to every
species alike; use a smaller `max_atoms` or accept the full range).
**They do not apply to unary systems**, where compositions are simply
`[(n,) for n in range(min_atoms, max_atoms + 1)]` — for one species the
stoichiometry *is* the atom count. See
[`multi-element.md`](multi-element.md#stoichiometry-stoich_min--stoich_max)
for how fast the scan grows.

Before 0.1.2 the defaults were the fixed `1` / `12`, which silently truncated
a multi-component scan whenever `max_atoms` exceeded 12 (Fe19C1 was never
generated at `max_atoms: 20`). The recorded config always carries the
effective bounds, and the staleness guard compares the composition sets they
imply rather than the numbers, so an existing campaign does not read as stale
after upgrading unless its scan actually changed.

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

## `incar` — the relaxation INCAR

A plain mapping of INCAR tag to value; keys are upper-cased on load. There is no
default INCAR — whatever you write here is the campaign's base. It is used
directly by the three relaxation stages, and it is the starting point for the
statics INCAR that [`incar_static`](#incar_static--the-incar-of-the-training-labels)
adjusts:

| stage | INCAR |
|---|---|
| ISIF7 | `incar` with `NSW = assyst.ionic_steps`, `ISIF = 7` |
| ISIF5 | `incar` with `NSW = assyst.ionic_steps`, `ISIF = 5` |
| ISIF2 | `incar` with `NSW = assyst.ionic_steps`, `ISIF = 2` |
| statics | `incar` with `incar_static` applied, `NSW = 0`, `ISIF` removed |

One consequence worth internalising: the `NSW` you write here is **never
used** — all three relaxation stages take `assyst.ionic_steps`, and the statics
take 0. It is kept in the example configs for provenance only.

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

A production relaxation INCAR, from
[`examples/Fe_unary.yaml`](../examples/Fe_unary.yaml):

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

## `incar_static` — the INCAR of the training labels

The relaxation only has to produce a sensible geometry, so it runs cheap. The
numbers that end up in the training set come from the **accurate statics** on
the relaxed images and their permutations, and those are a separate
calculation with their own INCAR. `incar_static` is where you set it.

```yaml
incar_static:            # defaults, spelled out
  KSPACING: 0.25
  EDIFF: 1.0e-5
  EDIFFG: 1.0e-4
  LREAL: false
```

The statics INCAR is `incar` with `incar_static` applied on top, then
`NSW = 0` and no `ISIF`. **Anything you do not mention is inherited from
`incar`** — `ENCUT`, `PREC`, `ISMEAR`, `SIGMA`, `ALGO`, `NELM`, `ISPIN`, the
mixing tags — so the two INCARs stay in step unless you say otherwise.

The four defaults above are what every ASSYST campaign has used for its labels
(denser k-mesh, tighter convergence, reciprocal-space projection), and they are
applied whether or not the section exists. Your entries layer over them:

```yaml
incar:                   # relaxation: cheap and robust
  ENCUT: 400
  PREC: Accurate
  KSPACING: 0.5
  EDIFF: 1.0e-4
  ALGO: Fast
  ISMEAR: 1
  SIGMA: 0.2
  IBRION: 2
  ISIF: 7

incar_static:            # labels: production grade
  ENCUT: 520             # higher cutoff for the labels only
  KSPACING: 0.2          # denser than the 0.25 default
  ALGO: Normal           # the relaxation's ALGO: Fast is not inherited
  NELM: 300
  ISMEAR: -5             # tetrahedron method is fine for a static
                         # EDIFF 1e-5, EDIFFG 1e-4, LREAL false: defaults, still applied
```

**`null` means "inherit from `incar`"**: it drops a default rather than setting
one. `incar_static: {KSPACING: null}` makes the statics use the relaxation's
`KSPACING` — the one way to get the labels at the same k-mesh as the
relaxation.

Two things are not settings and are refused at config-load time: `ISIF`
(statics never relax; it belongs in `incar`, where it seeds ISIF7) and any
`NSW` other than 0. `SYSTEM` and `MAGMOM` are stamped exactly as for `incar`;
an `ISPIN: 2` set only in `incar_static` gets its MAGMOM.

Every key is addressable from the command line, `--set incar_static.ENCUT=520`,
and the section participates in the staleness guard, compared through its
effective tags: a campaign recorded before this section existed, or one that
spells the defaults out, is not stale; a changed `KSPACING` is, reported as
`incar_static.KSPACING`.

**Needs `pyiron_workflow_assyst >= 0.2.2`** in the environment the jobs run in
(`slurm.env`). Older releases derive the statics INCAR themselves, from exactly
these defaults, so a config with no `incar_static` runs identically on either;
a customised one is refused by the in-job runner on an older release rather
than silently ignored, and `run` warns up front when the login environment is
too old.

## `magmom` — the MAGMOM string

```yaml
magmom:
  host: null
  host_moment: 3.0
  solute_moment: -0.5
  moments: {}
```

Only consulted when `incar.ISPIN` is 2. The MAGMOM string is built positionally
from the structure's site order — consecutive runs of the same species are
collapsed into `count*moment` terms — so `Fe Fe C Fe` with the defaults becomes
`2*3.0 1*-0.5 1*3.0`.

**`host`** — default `null`, meaning "the first species of this system". That
default is what lets a multi-system unary campaign work in one file: with
`species: [[La], [Ce], [Pr]]` and `host: null`, each element is its own host.
Set it explicitly only for a multi-component campaign where one specific element
is the matrix — and even then, putting the matrix first in each inner list
(`[[Fe, C], [Ni, C]]`) is usually simpler, since an explicit `host` applies to
every system in the file. See
[`multi-element.md`](multi-element.md#magnetic-systems-magmomhost).

**`host_moment` / `solute_moment`** — defaults `3.0` / `-0.5`. Initial moments in
µB for the host and for everything else. Note that
[`examples/Fe_unary.yaml`](../examples/Fe_unary.yaml) uses `-1.0` for the solute;
the dataclass default is `-0.5`.

**`moments`** — default `{}`. Per-element initial moments,
`{Mn: -2.0, C: 0.0}`, applied in every system of the campaign. An element
listed here gets that value wherever it appears; one not listed follows the
host / solute rule. This is how a multi-element campaign gives each solute its
own moment instead of one `solute_moment` for all, and listing every element
makes the rule irrelevant. Symbols are validated; values must be numbers. See
[`new-machine.md`](new-machine.md#6-magnetic-moments-per-element).

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
  prologue: []
  module_purge: true
  submit_command: sbatch
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
strings, e.g. `["--reservation=benchmarking"]` or `["--gres=gpu:4"]`.

**`prologue`** — default `[]`; verbatim shell lines placed after `module purge`
and before the runner starts, run once per job: `source` a site profile,
`export OMP_NUM_THREADS=...`. (Lines the *VASP process* needs go in
`vasp.prologue`.) **`module_purge`** — default `true`; set `false` on a machine
without an environment-modules system. **`submit_command`** — default
`sbatch`; the command a rendered script is handed to, split like a shell
would, with the script path appended — `sbatch --export=ALL`, a site wrapper,
or another batch system's command. How these compose into the script is shown
line by line in [`new-machine.md`](new-machine.md#3-how-the-job-file-is-built).

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
  potcar_config: null
  prologue: []
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

**`prologue`** — default `[]`; verbatim shell lines run in every VASP
calculation directory after the `module load` lines and before the launcher:
`export OMP_PLACES=cores`, `export CUDA_VISIBLE_DEVICES=...`, `source` a
toolkit environment. With `modules` and `launcher` this is the whole VASP
invocation — see [`new-machine.md`](new-machine.md#4-adapting-it-gpus-other-mpis-flux)
for GPU, OpenMPI and flux recipes.

**`potcar_config`** — default `null`. The POTCAR configuration file
`pyiron_workflow_vasp` reads (see
[the POTCAR library](installation.md#the-potcar-library)). `null` leaves the
library's own lookup — `$PYIRON_VASP_CONFIG`, then `~/.pyiron_vasp_config` —
in place. A path pins it: the rendered job script exports
`PYIRON_VASP_CONFIG` and the in-job runner sets it before the library is
imported, so the campaign stops depending on the submitting shell or on a home
directory. `run` warns if the file does not exist on the login node (it must
exist on the compute nodes). Participates in the staleness guard, as the rest
of `vasp` does.

## The staleness guard: which sections participate

`run` writes the fully resolved config to `<root>/assystant_config.yaml` and, on
every later invocation, compares against it. A calculation-affecting difference
aborts with `StaleConfigError` naming the changed keys.

**`slurm` is exempt. Everything else participates**: `root`, `system`, `incar`,
`magmom`, `assyst`, `filters`, `vasp`.

Within `system`, `stoich_min` / `stoich_max` are compared through the
composition sets they produce, reported as `system.compositions.<system>`: a
unary campaign recorded with the pre-0.1.2 default `stoich_max: 12` is not
stale, because its compositions are the same; a binary whose `max_atoms` grew is.
A key that a recorded config predates (`vasp.potcar_config`, `magmom.moments`,
`incar_static`) counts as its empty default, so upgrading `assystant` never by
itself makes a campaign stale.

The exemption is deliberate. Wall time, task count and partition change how the
work reaches the scheduler, not what is computed — and since the only way past
the guard is `--from-scratch`, which *deletes run directories in full*, making a
`--time` bump demand it would be worse than useless. Everything else can change
the numbers, and the executorlib cache under an existing run directory would
serve results computed under the old settings. The full reasoning is in
[`operations.md`](operations.md#the-staleness-guard).
