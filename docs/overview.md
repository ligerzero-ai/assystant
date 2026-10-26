# What `assystant` does, and why it exists

## The problem ASSYST solves

Training an interatomic potential needs energies and forces for configurations
that a molecular dynamics run would never visit on its own: compressed cells,
sheared cells, atoms displaced well off their equilibrium sites, coordination
environments that no experimentalist has ever seen. A potential fitted only to
relaxed ground-state structures is confident and wrong everywhere else.

ASSYST generates that data systematically rather than by hand. The recipe, per
chemical system, is:

1. **Enumerate random structures.** PyXtal is asked for a random crystal in
   every spacegroup from 1 to 230, crossed with every composition tuple in the
   requested atom-count range. Most (spacegroup, composition) pairs are
   geometrically impossible and fail; the ones that succeed are a broad,
   symmetry-diverse sample of the configuration space. Each is rescaled into a
   band around its equilibrium volume per atom and given a small random
   displacement to break exact symmetry.

2. **Relax each one through an ISIF7 → ISIF5 → ISIF2 chain.** ISIF7 relaxes the
   volume alone, ISIF5 the cell shape at fixed volume, ISIF2 the ion positions
   at fixed cell. Splitting the relaxation this way is far more robust for a
   random starting geometry than asking VASP to relax everything at once, which
   on a pathological cell tends to collapse or diverge. Each stage starts from
   the previous stage's CONTCAR.

3. **Run high-accuracy statics on the relaxed images.** The relaxation itself is
   run cheaply — coarse k-point sampling, loose convergence — because its only
   job is to produce a sensible geometry. The numbers that go into the training
   set come from a separate static calculation on that geometry, by default at a
   denser mesh (`KSPACING = 0.25`) and tighter convergence (`EDIFF = 1e-5`,
   `LREAL = False`). The two INCARs are set separately — `incar` for the
   relaxation, `incar_static` for the labels — see
   [`configuration.md`](configuration.md#incar_static--the-incar-of-the-training-labels).

4. **And on deformed permutations of those images.** This is the step that
   supplies the off-equilibrium data. Each relaxed image is rattled (atoms
   displaced by a Gaussian, plus a small cell strain), triaxially strained, and
   sheared; each candidate deformation is checked against a minimum-distance
   floor and a core-radius overlap filter, and retried if it fails. Every
   surviving permutation gets the same high-accuracy static.

5. **Collate everything into one dataframe.** One `df_ASSYST_jobs.pkl` per
   structure, concatenated across the campaign into a single training set.

A structure therefore costs three relaxation stages plus, at the production
settings `n_rattle: 5, n_stretch: 5`, one static per relaxed image and
`n_rattle + 2 × n_stretch = 15` more for its permutations — rattle, triaxial and
shear are generated per image, with `n_stretch` counting triaxial and shear
*separately*.

## What `assystant` adds

The recipe above already existed, as a pile of scripts and notebooks in
`/u/hmai/2024_12_05_DFTPyxtalAssystNodes`: a generation notebook per system
class, `calculate_assyst.py`, and a hand-maintained `submit_temp.sh` carrying one
`calculate_assyst.py` invocation per element. Running a new campaign meant
copying `2025_03_16_Unaries.py`, editing element lists in notebook cells, and
keeping the submit script in sync by hand.

`assystant` replaces that with one YAML file per campaign and four commands:

| command | what it does |
|---|---|
| `gen` | PyXtal scan → POSCARs + `manifest.csv` |
| `run` | one SLURM job per structure; idempotent, so it is also the resume command |
| `status` | per-system progress table |
| `collect` | every `df_ASSYST_jobs.pkl` → one dataframe |

A campaign covering forty unary systems is now `species: [[La], [Ce], [Pr], ...]`
in one file rather than forty lines in a shell script.

## Where the work actually happens

`assystant` is deliberately thin. It owns configuration, structure generation,
job submission, monitoring and collection — the campaign layer. The physics
lives one level down, in two libraries that are useful on their own:

```
pyiron_workflow_vasp     VASP nodes, input writing, output parsing
        ^
pyiron_workflow_assyst   the ASSYST graph and the structure-validity filters
        ^
assystant                campaign: config, generation, submission, status, collection
```

`run_ASSYST_on_structure` in `pyiron_workflow_assyst` is the graph that performs
steps 2–5 above for a single structure. `assystant`'s in-job runner
(`assystant/runner.py`) does nothing more than read the recorded campaign config,
read one POSCAR, build the INCAR, and call it. Everything `assystant` adds around
that — content-hashed run directories, the config staleness guard, the idempotent
skip — exists to make thousands of those single-structure runs safe to launch,
resume and re-launch without silently corrupting the dataset.

Why those safeguards are not optional is the subject of
[`operations.md`](operations.md); the short version is that the caching layer
underneath keys on directory path and never hashes its inputs, so "same
directory" is taken to mean "same calculation" whether or not that is true.

## Reading order

* [`installation.md`](installation.md) — dependencies, the POTCAR config.
* [`worked-example.md`](worked-example.md) — a complete campaign, start to finish.
* [`multi-element.md`](multi-element.md) — several elements in one campaign:
  many unaries, binaries and ternaries, and adding elements later.
* [`configuration.md`](configuration.md) — every YAML key.
* [`commands.md`](commands.md) — command reference.
* [`operations.md`](operations.md) — the things that save you days.
* [`behaviour-changes.md`](behaviour-changes.md) — read before comparing new data
  against pre-2026 campaigns.
* [`troubleshooting.md`](troubleshooting.md) — real failure modes and their text.
