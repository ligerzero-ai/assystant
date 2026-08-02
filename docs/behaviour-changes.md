# Behaviour changes versus pre-2026 campaigns

If you are comparing data produced by `assystant` against data produced by
`calculate_assyst.py` and the notebooks in
`/u/hmai/2024_12_05_DFTPyxtalAssystNodes`, read this first. Five things changed,
each deliberately, and one of them makes part of the new data **not directly
comparable** with the old.

---

## `ionic_steps` now applies to all three ISIF stages

**Old:** `ionic_steps` set `NSW` for the ISIF7 stage only. ISIF5 and ISIF2 ran
with whatever `NSW` the raw INCAR carried.

**New:** `ionic_steps` sets `NSW` for ISIF7, ISIF5 *and* ISIF2.

In production this was not a subtlety. ISIF7 ran at `NSW = 100` (200 on
reconverge) while ISIF5 and ISIF2 ran at the raw INCAR's `NSW = 500`. Under the
new behaviour, `ionic_steps: 100` gives all three stages 100 steps.

**Consequence.** A structure that previously needed more than `ionic_steps`
iterations to converge its cell shape (ISIF5) or its ion positions (ISIF2) now
stops early, and *it is that unconverged geometry, not a relaxed one, that feeds
the subsequent high-accuracy statics.* New ISIF5 and ISIF2 data is therefore not
directly comparable with existing datasets.

This was reviewed and deliberately accepted by the package owner on 2026-08-01.
It is not an oversight and should not be reverted without a fresh discussion.

**What to do.** The `isif7_converged`, `isif5_converged` and `isif2_converged`
columns exist in the collected dataframe precisely so this is visible after the
fact. Check the convergence rate on your first campaign under this version:

```python
df = pd.read_pickle("train.pkl")
per_structure = df.drop_duplicates(subset=["system", "structure_hash"])
print(per_structure[["isif7_converged", "isif5_converged", "isif2_converged"]].mean())
```

If ISIF5 or ISIF2 convergence is poor, raise `ionic_steps` rather than
reintroducing the old per-stage asymmetry.

---

## The minimum-distance filter does a real neighbour search

**Old:** `get_minimum_distance` used pymatgen's `Structure.distance_matrix` with
the diagonal masked to infinity. That is the minimum-image convention with every
atom's distance *to its own periodic image* thrown away — not just a degenerate
self-term.

**New:** an ASE `neighbor_list`, which performs a real periodic neighbour search.

The old behaviour had two failure modes. For a **single-atom cell** the whole
distance matrix *is* the diagonal, so the function always returned infinity and
the 1.0 Å floor was **completely inert** no matter how compressed the cell. For
any cell with a **short lattice vector**, a genuine short contact along that
vector was invisible: a real 0.88 Å H–H contact in a sheared cell was reported as
1.88 Å. Since ASSYST's entire purpose is generating heavily distorted cells, this
mattered.

**How much it matters, measured.** Over 300 randomly sheared and triaxially
strained 1–3 atom cells, the two backends disagree about whether the minimum
distance falls below 1.0 Å in **10.3%** of cases. But the *overall* validity
verdict flips far less often, because the core-overlap check independently
rejects most of the same structures:

| species | min-dist verdict differs | `is_valid_structure` verdict differs |
|---|---|---|
| H | 10.3% | **0.7%** |
| Fe | 10.3% | 0.0% |

The effect on which structures actually enter your dataset is confined to species
where the min-dist floor binds before the core-overlap check does — that is,
where `(1 - core_overlap_tolerance) × 2 × RCORE < min_dist`. At the default
tolerance of 0.3 that means `RCORE < 0.714 Å`, which in the default POTCAR
selection is **hydrogen and helium only** (both 0.582 Å). For Fe the overlap
check binds first at 1.70 Å, so the min-dist backend never changes the answer.

Those are, of course, exactly the light elements where a sub-ångström contact
wrecks an SCF.

(The rates above depend on how aggressively you deform; they were measured at
`shear_strain = triaxial_strain = 0.8` on cells with lattice parameters 1.5–3.5 Å,
which is a deliberately harsh sample.)

**Reproducing an old campaign** exactly requires `filters.min_dist_backend: mic`.
That is the only reason the option exists.

---

## RCORE is read from the POTCAR actually used

**Old:** a hardcoded `RCORE` table. `structure_filter_utils.py` in fact defined
one *twice*, at roughly lines 5 and 77; the second silently won and the first was
dead code. They disagreed — the dead one held physically per-element values
(Ti 2.8, V 2.7, Cr 2.5 bohr), the live one collapsed many transition metals to
2.3 bohr.

**New:** radii are read directly from the POTCAR files a run will actually use,
with a fallback table for the case where no POTCAR can be resolved.

The old table's *live* values were, as it happens, correct: they were the radii of
the semicore POTCAR variants pyiron selects by default. The default selection is
not the plain element —

```
Fe -> .../potpaw_64/GGA/Fe/POTCAR
Ti -> .../potpaw_64/GGA/Ti_sv/POTCAR
```

— so a table of plain-POTCAR radii (the dead dictionary) would have been wrong
for every element where a `_sv`/`_pv` variant is chosen. Reading from the POTCAR
removes the possibility of the table and the writer disagreeing at all.

**The default selection reproduces past campaigns exactly.** The fallback table
also holds the semicore radii, not the plain ones, so even the no-POTCAR path
matches what actually ran. This change is a robustness fix, not a numerical one:
it only bites if you deviate from the default POTCAR selection, in which case the
old table would have been silently wrong.

---

## The generation seed now works

**Old:** the generation notebooks called `np.random.seed(...)` and believed
generation was reproducible. It was not. `pyxtal` 1.1.4 draws from its own
`Generator`, and pymatgen's `Structure.perturb` calls
`np.random.default_rng(seed=seed)` internally — **neither reads numpy's global
RNG**. Every `np.random.seed(...)` in those notebooks was a no-op with respect to
the structures produced, and no pre-2026 structure set was ever reproducible.

**New:** the seed is threaded explicitly into `pyxtal.from_random(random_state=…)`
and into `Structure.perturb(seed=…)`, with a per-task seed derived
deterministically from the master seed, the spacegroup, the composition and the
task index — so tasks are decorrelated but the set as a whole is reproducible.
`np.random.seed` is still called as well, for anything further down that does read
global state.

Measured while porting: `np.random.seed` alone gives two differing runs;
`random_state=s` gives two identical runs and dominates a differing numpy seed.

**Consequence.** `system.seed: null` (the default) reproduces the historical
behaviour — a fresh `secrets.randbits(32)` per task. Setting an integer now
genuinely pins the structure set, which also pins the content hashes and therefore
the run-directory names. You cannot retroactively reproduce a pre-2026 structure
set; those POSCARs are the only record of themselves.

---

## The perturbation moved to generation time

**Old:** `calculate_assyst.py` called `struct.perturb(0.1)` just before
submitting — that is, *after* the POSCAR had been written to disk. The file on
disk and the geometry calculated were different structures.

**New:** `system.perturb` (default 0.1 Å, the same value) is applied inside `gen`,
before the POSCAR is written.

This is not a physics change: the same distribution of displacements is applied to
the same structures. It is a bookkeeping change forced by content-hashed
directories — the hash naming a run directory has to describe the geometry
actually calculated in it, or the whole mechanism is a lie. Now the POSCAR on
disk, its content hash, the directory name and the calculated geometry are all
the same object.

---

## Smaller changes

**`MAGMOM` is written only when `ISPIN: 2`.** The pre-2026 scripts wrote it
unconditionally, which made a nonmagnetic INCAR read as though it were magnetic.
With `ISPIN: 1`, any `MAGMOM` is now removed.

**`system.species` accepts a list of lists**, so one campaign file covers many
systems. This is what replaces `submit_temp.sh`'s one line per element.

**A dead variable was removed from `filter_distance_by_species`.** It built
`structure_copy` (a 2×2×2 expansion for single-atom cells) and then never used
it, passing the original structure onward. Removing it is safe because the
function it feeds uses `get_all_neighbors`, which handles periodicity correctly —
the core-overlap check was never affected by the minimum-image bug above.

**Node labels inside the graph are auto-generated** by flowrep (`vasp_job_0`,
`for_each_0`, `body_3`) rather than hand-chosen. Any old notebook that reached
into the graph by label — `wf.ISIF7_job` and friends — needs updating.
