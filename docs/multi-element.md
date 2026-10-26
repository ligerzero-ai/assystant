# Several elements in one campaign

"Multiple elements" means two different things, and `assystant` does both from
the same key, `system.species`:

* **several systems in one campaign** — a unary Fe campaign, a unary Ni
  campaign and a unary Cu campaign, generated, submitted, watched and collected
  together. This is what replaces a `submit_temp.sh` with one line per element.
* **a multi-component system** — Fe–C, Fe–Ni–C: one system whose structures
  contain more than one species, scanned over stoichiometries as well as over
  spacegroups and cell sizes.

They combine freely. Every transcript below was executed as written against
the bundled `AlCu_tutorial` template; only the absolute paths were shortened.

## The two shapes of `species`

```yaml
system:
  species: [Fe]                       # one unary system           -> Fe/
  species: [Fe, C]                    # one binary system          -> Fe_C/
  species: [[Fe], [Ni], [Cu]]         # three unary systems        -> Fe/ Ni/ Cu/
  species: [[Fe], [Fe, C], [Fe, N]]   # one unary and two binaries -> Fe/ Fe_C/ Fe_N/
```

A **flat list is one system**; a **list of lists is one system per inner list**.
The two forms cannot be mixed (`[Fe, [Ni]]` is a `ConfigError`). Each system gets
its own directory under `root`, named by joining its symbols with underscores in
the order you wrote them, with its own `structures/` and `manifest.csv`. Symbols
are validated against pymatgen's periodic table, and a symbol repeated inside
one system is rejected.

The distinction that matters: `[Fe, C]` and `[[Fe], [C]]` are different
campaigns. The first generates Fe–C crystals; the second generates pure-Fe
crystals and pure-carbon crystals and never puts the two in one cell.

## A runnable example

The bundled `AlCu_tutorial` template is the unary `Al_tutorial` extended to
`species: [[Al], [Cu], [Al, Cu]]` — two unaries and the binary, cubic
spacegroups, 1–4 atoms, so the whole scan takes seconds and
`gen` / `run --dry-run` need neither SLURM nor VASP:

```
$ assystant example AlCu_tutorial -o camp.yaml
wrote camp.yaml

$ assystant gen camp.yaml --no-progress
Al: 43/144 (spacegroup, composition) pairs produced a structure; rejected: Comp_CompatibilityError x101
Cu: 43/144 (spacegroup, composition) pairs produced a structure; rejected: Comp_CompatibilityError x101
Al_Cu: 15/216 (spacegroup, composition) pairs produced a structure; rejected: Comp_CompatibilityError x201
Al: 43 structures -> /scratch/demo/AlCu_campaign/Al
Cu: 43 structures -> /scratch/demo/AlCu_campaign/Cu
Al_Cu: 15 structures -> /scratch/demo/AlCu_campaign/Al_Cu
```

One tally line per system. The unaries scan 36 spacegroups × 4 cell sizes = 144
pairs; the binary scans 36 × 6 compositions = 216 — the six are every Al:Cu
split of 2, 3 and 4 atoms, the full composition range for `max_atoms: 4` (see
below). Rejection rates
are higher for the binary, and that is normal: a fixed Al:Cu ratio fits far fewer
Wyckoff-position combinations than "n atoms of one kind". With `seed` set, the
Al system is byte-identical to the one `Al_tutorial` produces — same hashes,
same directory names.

```
$ ls AlCu_campaign AlCu_campaign/*
AlCu_campaign:
Al  Al_Cu  Cu

AlCu_campaign/Al:
manifest.csv  structures

AlCu_campaign/Al_Cu:
manifest.csv  structures

AlCu_campaign/Cu:
manifest.csv  structures

$ head -4 AlCu_campaign/Al_Cu/manifest.csv
index,system,dirname,hash,spacegroup,composition,formula,n_atoms,volume_per_atom
0,Al_Cu,struct_00000_2ef8780b,2ef8780b,195,1-1,Al1Cu1,2,14.017409355982672
1,Al_Cu,struct_00001_c00de976,c00de976,195,1-3,Al1Cu3,4,13.290183051085574
2,Al_Cu,struct_00002_f7336bd1,f7336bd1,195,3-1,Al3Cu1,4,16.100052787637047
```

In a multi-component manifest the `composition` column is the per-species count
in `species` order (`1-3` is one Al, three Cu) and `formula` is the pymatgen
formula. Structure indices restart at 0 in every system; `(system, index)` and
`(system, hash)` are the unique keys, which is how `collect` joins results back to
the manifest.

```
$ assystant run camp.yaml --dry-run --limit 1
rendered 1, skipped 0 queued / 0 finished

$ find AlCu_campaign -name assystant_job.sh
AlCu_campaign/Al/struct_00000_36c5d68a/assystant_job.sh

$ assystant run camp.yaml --dry-run
rendered 101, skipped 0 queued / 0 finished

$ assystant status camp.yaml
system          category        done  started   total     pct
-------------------------------------------------------------
Al              Unary              0       43      43   0.00%
Cu              Unary              0       43      43   0.00%
Al_Cu           Binary             0       15      15   0.00%
-------------------------------------------------------------
TOTAL                              0              101   0.00%
```

Note what `--limit 1` did: it rendered one job, from the **first** system only.
See [`run` and `--limit`](#run-and---limit-across-systems).

Each rendered script carries its system in the job name and in the runner
invocation, so queue listings and `assystant.out` files are self-identifying:

```
#SBATCH --job-name=assyst_Al_Cu_00000
... python -m assystant.runner ... --system Al_Cu
```

## Stoichiometry: `stoich_min` / `stoich_max`

For a **unary** system the composition *is* the cell size, so `gen` scans
`min_atoms .. max_atoms` atoms per cell and `stoich_min` / `stoich_max` are
ignored. For a **multi-component** system each species independently takes
every count in `stoich_min .. stoich_max`, and the combinations are filtered to
`min_atoms <= total <= max_atoms`.

**By default the whole composition range is scanned.** Both bounds are `null`,
which resolves to `stoich_min = 1` and `stoich_max = max_atoms`: for every cell
size `n` in the atom-count window, every split with each species present. A
binary therefore covers `n = 2 .. max_atoms` with `1 .. n-1` atoms of each
species; a ternary every three-way split from `n = 3` up. Nothing needs to be
set for that — the tutorial template sets neither bound:

```python
>>> from assystant.generate import enumerate_compositions
>>> enumerate_compositions(["Al", "Cu"], min_atoms=1, max_atoms=4)
[(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (3, 1)]
>>> len(enumerate_compositions(["Fe", "C"], min_atoms=1, max_atoms=20))
190
```

Pure-Al and pure-Cu cells are *not* in that list — every species is present in
every cell of a multi-component system. If you want the end members in the
dataset, add them as their own unary systems, as the template does. That is
also the reason to prefer `[[Fe], [Fe, C]]` over just `[Fe, C]` for a potential
that must describe pure iron.

Set a bound explicitly only to **narrow** the scan, and know that it applies to
every species alike: `stoich_max: 3` caps iron *and* carbon at three atoms. (In
0.1.1 and earlier the defaults were the fixed `1` / `12`, so a binary with
`max_atoms: 20` silently stopped at `Fe12C8` and never saw `Fe19C1`; see the
changelog for how the staleness guard treats that on upgrade.)

The scan grows quickly with the number of species. Attempts below are
compositions × 230 spacegroups at `min_atoms: 1`, `max_atoms: 12`; structures
produced is a fraction of that, and every produced structure is one SLURM job
of `1 + n_rattle + 2 × n_stretch` statics after its three relaxations:

| system | compositions | (spacegroup, composition) attempts |
|---|---:|---:|
| `[Fe]` | 12 | 2,760 |
| `[Fe, C]` | 66 | 15,180 |
| `[Fe, B, C]` | 220 | 50,600 |

Lower `max_atoms` (or set `stoich_max`) for anything beyond a binary.
`enumerate_compositions` is the cheap way to see what a setting buys before
running `gen`.

## Magnetic systems: `magmom.host`

Only relevant when `incar.ISPIN` is 2. The MAGMOM string gives every site of the
host element `host_moment` and every other site `solute_moment`, and
**`host: null` (the default) means "the first species of each system"**:

```yaml
system:
  species: [[Fe], [Fe, C], [Fe, N]]   # Fe is first everywhere -> Fe is the host everywhere
magmom:
  host: null
  host_moment: 3.0
  solute_moment: -1.0
```

Because the host is resolved per system, a campaign over several magnetic
matrices needs nothing more than putting the matrix first in each inner list:
`[[Fe, C], [Ni, C], [Co, C]]` gives Fe, Ni and Co as the hosts of their own
systems. Setting `host: Fe` explicitly applies to *every* system in the file,
and is only worth doing when the matrix element is not first — but reordering
the list is the simpler fix, with one caveat: species order is also the
directory name (`Fe_C` vs `C_Fe`), so pick an order before the first `run`.

When different solutes need different moments — Mn and C in iron are not
both `-1.0` — list them in `magmom.moments`, which applies per element in
every system and takes precedence over the host / solute rule:

```yaml
magmom:
  host: null
  host_moment: 3.0
  moments: {Mn: -2.0, C: 0.0}
```

See [`new-machine.md`](new-machine.md#6-magnetic-moments-per-element).

For a non-magnetic campaign (`ISPIN: 1`, as in the bundled tutorial templates)
the `magmom` section is never consulted and no MAGMOM is written.

## Equilibrium volumes for mixed compositions

Generated cells are rescaled into `[strain_min, strain_max]` × an equilibrium
volume per atom. For a multi-component cell that reference is the
composition-weighted mean of the per-element values from `eq_volume_source` —
for Al1Cu3, `(1 × V_Al + 3 × V_Cu) / 4`.

The bundled table covers 87 elements. **If any element of a cell is missing
from the table, that cell is left at PyXtal's guessed volume, silently** — no
error, no tally line. Check before a campaign on anything exotic:

```python
>>> from assystant.config import resolve_eq_volume_map
>>> table = resolve_eq_volume_map(None)             # the bundled table
>>> [el for el in ("Fe", "C", "Pu") if el not in table]
['Pu']
```

and if something is missing, give an explicit mapping that covers **every
element of every system**:

```yaml
system:
  species: [[Fe], [Fe, Pu]]
  eq_volume_source: {Fe: 11.8, Pu: 25.0}
```

## POTCARs

`assystant` never names a POTCAR; `pyiron_workflow_vasp` resolves one per
element from the [POTCAR config](installation.md#the-potcar-library). A
multi-element campaign therefore needs a POTCAR for every element in every
system, and a missing one fails only inside the job, after queueing. Extend the
check in the installation guide to the full element set before submitting:

```bash
python -c "
from ase import Atoms
from pyiron_workflow_vasp.vasp import (
    _default_POTCAR_library_path, _default_functional, get_default_POTCAR_paths)
lib, func = _default_POTCAR_library_path(), _default_functional()
for el in ('Al', 'Cu'):
    print(el, get_default_POTCAR_paths(Atoms(el), lib, func))
"
```

## `run` and `--limit` across systems

`run` walks the systems **in the order they appear in `species`**, and
`--limit N` caps the number of structures acted on across the whole campaign,
not per system. So `--limit 1` renders or submits one structure from the first
system and nothing from the others, and `--limit 50` on the tutorial would
submit 43 Al structures and 7 Cu — the binary would not be touched.

That is still the right way to start: it proves the binary, the modules, the
POTCAR path for the first element. To check the *other* elements before
committing their full set, use `--dry-run` (which renders every script) and run
the first script of each system by hand, exactly as in
[smoke testing without a scheduler](operations.md#smoke-testing-without-a-scheduler):

```bash
assystant run camp.yaml --dry-run
bash AlCu_campaign/Cu/struct_00000_*/assystant_job.sh      # on a node, in the right env
bash AlCu_campaign/Al_Cu/struct_00000_*/assystant_job.sh
```

Then `assystant run camp.yaml` submits everything that is not already queued or
finished — including the two you just ran by hand, which are skipped as done.

A system whose manifest is missing (you added it to `species` but have not
re-run `gen`) is reported and skipped, not treated as an error:

```
No manifest for Ni; run `assystant gen` first
```

## `status` and `collect`

`status` prints one row per system plus a total, as shown above; the
`category` column is `Unary`, `Binary`, `Ternary`, … from the species count.
`--csv` writes one row per system per timestamp, with the system in the
`run_dir` column.

`collect` concatenates every system's results into one dataframe and stamps
each row with its `system`, so one campaign gives one training set and the
split is a pandas filter away:

```python
import pandas as pd
df = pd.read_pickle("train.pkl")
df.groupby("system").size()                       # rows per system
binary = df[df.system == "Al_Cu"]
clean = df[df.isif7_converged & df.isif5_converged & df.isif2_converged]
```

`collect --all-systems` reads every system directory under `root` rather than
only those in the current YAML, which matters for the situation in the next
section.

## Adding an element to a campaign that has already run

`system.species` is part of the calculation-affecting config that `run` records
in `<root>/assystant_config.yaml`, so appending a system to a campaign that has
already been through `run` trips the [staleness guard](operations.md#the-staleness-guard):

```
$ assystant run camp.yaml --dry-run           # species: [[Al], [Cu], [Al, Cu], [Ni]]
error: resolved campaign config differs from the one recorded at
/scratch/demo/AlCu_campaign/assystant_config.yaml at: system.species.
executorlib's cache is keyed on node lexical path with no input hashing, so
re-running an existing run_dir under changed settings would silently return
results computed with the OLD ones. Either revert the change, point `root` at a
fresh campaign directory, or pass --from-scratch (which DELETES each structure's
run_dir in full, including executorlib_cache).
```

**Do not reach for `--from-scratch` here.** It deletes every existing run
directory in the campaign — the finished Al, Cu and Al–Cu results included — to
add a system that would not have touched them. Instead, give the new elements
their own campaign:

```yaml
# camp_Ni.yaml
root: ./AlCu_campaign_Ni      # a fresh root; everything else copied from camp.yaml
system:
  species: [[Ni]]
```

run `gen` / `run` / `status` against it, and concatenate at the end:

```python
df = pd.concat([pd.read_pickle("AlCu_train.pkl"), pd.read_pickle("Ni_train.pkl")],
               ignore_index=True)
```

The cheaper path is to decide the element list up front: adding a system
*before* the first `run` costs nothing, because `gen` is per system and nothing
has been recorded yet.

## A production-shaped example

[`examples/Fe_unary.yaml`](../examples/Fe_unary.yaml) is a real production
config for one element. The multi-element version of it changes only the
`system` block — keep everything else, in particular the full INCAR,
`custodian: true` and the 48-hour wall time:

```yaml
system:
  species: [[Fe], [Fe, C], [Fe, N]]   # pure Fe plus two interstitial binaries
  min_atoms: 1
  max_atoms: 12                       # stoich_min/stoich_max left null: full composition range
  spacegroups: [1, 230]
  strain_min: 0.8
  strain_max: 1.2
  eq_volume_source: EleBulk_MatProj_VorVol.csv
  perturb: 0.1
  seed: null

magmom:
  host: null                          # Fe, in every system
  host_moment: 3.0
  solute_moment: -1.0
```

Executed with `seed: 20260802` on 16 cores, the `Fe_C` system alone generates
4,005 structures from 15,180 attempts in 29 seconds:

```
Fe_C: 4005/15180 (spacegroup, composition) pairs produced a structure; rejected: Comp_CompatibilityError x11175
```

against roughly 2,700 for a unary — each a SLURM job of three relaxations and
16 statics. Three systems like these are on the order of 10,000 jobs, which is
why the sections above on `--limit`, on hand-running one script per system, and
on fixing the element list before the first `run` exist.

Before launching, read [`operations.md`](operations.md): content-hashed
directories and the staleness guard are what make a campaign of this size safe
to resume, and they are also what makes "I will add Ni later" a new `root`
rather than an edit.
