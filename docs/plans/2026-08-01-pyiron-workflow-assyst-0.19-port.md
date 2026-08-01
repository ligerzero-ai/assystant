# pyiron_workflow_assyst 0.19 Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `ligerzero-ai/pyiron_workflow_assyst` to pyiron_workflow 0.19/flowrep on top of the completed `pyiron_workflow_vasp` 0.2.0 port, and correct two filter defects that affect which structures enter training data.

**Architecture:** `structure_filter_utils.py` has no pyiron dependency and changes for science reasons only. `workflow.py`'s `run_ASSYST_on_structure` macro becomes an `@fr.workflow`: the ISIF7→ISIF5→ISIF2 chain reads as sequential Python, and the two `for_node(zip_on=...)` fan-outs become real `for ... in zip(...)` accumulator loops. The `Length` → `get_multiple_input` → `generate_VaspInputs` broadcasting scaffolding disappears entirely, because `ForEach` broadcasts non-iterated inputs automatically.

**Tech Stack:** Python 3.12, pyiron_workflow 0.19.0, flowrep 0.6.2, pyiron_workflow_vasp 0.2.0, pymatgen 2026.7.27, ase, pytest.

## Global Constraints

- Environment: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/python` and `.../bin/pytest`. Do not create or modify conda environments.
- Dependency floors: `pyiron_workflow>=0.19,<0.20`, `flowrep>=0.6,<0.7`, `pyiron_workflow_vasp>=0.2.0`, `requires-python = ">=3.11,<3.14"`.
- **No `os.chdir`.** 0.19 evaluates sibling DAG-layer nodes on separate threads by default.
- **No execution signals.** No `>>`, no `starting_nodes`, no `self`/`wf` first argument. Ordering comes from data dependencies only.
- Node functions must be importable at module top level — flowrep rejects `<locals>` in a qualname.
- **Every new test must be mutation-proven.** Three tests in the sibling port were written to assert properties that could not fail. For each test you add that guards a behaviour, temporarily break that behaviour, confirm the test fails, restore, confirm it passes, and report both runs. A test you cannot make fail is not evidence.
- Git commits authored `Han Lin Mai` — use `git -c user.name="Han Lin Mai" commit`, because the repo's configured `user.name` is "Han Mai".
- Do not push.

## Verified before planning

Probed against the target environment; these are observed, not assumed:

- A `@fr.workflow` (`vasp_job`) can be **called from inside another `@fr.workflow`**, its **two outputs tuple-unpacked** (`out, conv = vasp_job(...)`), and that call can appear **inside a `zip` accumulator loop**. A probe with two such loops built children `['join_path_0', 'constant_0', 'vasp_job_0', 'collect_0', 'for_each_0', 'for_each_1', 'concat_0']`, ran, and returned correct counts. This is the crux of the whole port.
- The ported `pyiron_workflow_vasp` 0.2.0 API, quoted verbatim for use in this plan:
  - `vasp_job(workdir, vasp_input, command="mpiexec -n 1 vasp_std", files_to_be_deleted=None, compress=False, compressed_file_in_dir=False, remove_calc_dir=False, vasp_parser_function=None, vasp_parser_args=None)` → ports `"vasp_output"`, `"convergence_status"`
  - `generate_vasp_input(structure, incar: Incar, potcar_paths=None) -> VaspInput` → port `"vasp_input"`
  - `construct_sequential_vasp_input(vasp_output: pd.DataFrame, incar: Incar, potcar_paths=None) -> VaspInput` → port `"vasp_input"`
  - `generate_modified_incar(incar: Incar, modifications: dict) -> Incar` → port `"incar"`
  - `VaspInput(structure: Atoms, incar, pseudopot_lib_path=..., pseudopot_functional="GGA", potcar_paths=None, kpoints=None)` — normalises `structure` to `ase.Atoms` in `__post_init__`, so callers need not convert.
  - `parse_vasp_directory(directory, extract_error_dirs=True, parse_all_in_dir=True)` → a **pandas DataFrame**, one row per `OUTCAR*` plus one per error archive, sorted **ascending** by `calc_start_time`; its `structures` column holds a per-row array of `Structure.to_json()` strings.
- `workflow.py` is byte-identical between repo HEAD and the installed copy. `structure_filter_utils.py` is **not** — see Task 2.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `pyiron_workflow_assyst/structure_filter_utils.py` | validity filter | RCORE source rewritten, MIC bug fixed |
| `pyiron_workflow_assyst/perturb.py` | rattle / triaxial / shear | **new** — extracted from workflow.py |
| `pyiron_workflow_assyst/workflow.py` | ASSYST graph | ported to flowrep |
| `pyiron_workflow_assyst/__init__.py` | exports | neutralised then restored |
| `tests/conftest.py` | fixtures | create |
| `tests/test_filters.py` | RCORE + min-distance | create |
| `tests/test_perturb.py` | deformation generators | create |
| `tests/test_collect.py` | image selection | create |
| `tests/test_workflow.py` | end-to-end graph | create |
| `pyproject.toml` | deps, pytest config | modify |

---

## Preflight

- [ ] **Step 0.1: Branch, and deal with the uncommitted edit**

The repo has an uncommitted 2-line change to `workflow.py` (a commented-out `vasp_parser_kwargs` parameter and a trailing newline). It is cosmetic and the file is being rewritten anyway.

```bash
cd /cmmc/u/hmai/pip_repos/pyiron_workflow_assyst
git stash push -m "pre-port cosmetic edit" pyiron_workflow_assyst/workflow.py
git checkout -b port/pyiron-workflow-0.19
printf '.ipynb_checkpoints/\n__pycache__/\n*.pyc\n.pytest_cache/\n.superpowers/\npyiron.log\n' >> .gitignore
git add .gitignore
git -c user.name="Han Lin Mai" commit -m "chore: ignore caches and notebook checkpoints"
```

---

### Task 1: Test harness, dependency floors, neutralised `__init__`

**Files:**
- Modify: `pyproject.toml`, `pyiron_workflow_assyst/__init__.py`
- Create: `tests/conftest.py`, `tests/test_imports.py`

**Interfaces:**
- Consumes: nothing.
- Produces: fixtures `fe_structure()` (1-atom BCC Fe pymatgen `Structure`), `two_species_structure()` (Fe+H, deliberately close-contact), `sheared_cell()` (a heavily sheared cell whose true nearest-neighbour distance is below 1.0 Å but whose minimum-image distance is above it — this is the fixture Task 3 depends on).

`pyiron_workflow_assyst/__init__.py` currently does `from .workflow import ...` inside a `try`, re-raising as `ImportError`. `workflow.py` imports `pyiron_workflow_vasp.vasp`, which is fine, but `workflow.py` itself is on the 0.11 API, so the package cannot import until Task 5 lands. Neutralise it exactly as the sibling port did.

- [ ] **Step 1.1: Write the failing test**

Create `tests/test_imports.py`:

```python
import pyiron_workflow as pwf


def test_pyiron_workflow_is_019():
    assert pwf.__version__.startswith("0.19")


def test_vasp_dependency_is_ported_version():
    import pyiron_workflow_vasp as pwv

    assert pwv.__version__ >= "0.2.0"
    for name in ("vasp_job", "generate_vasp_input", "construct_sequential_vasp_input"):
        assert hasattr(pwv, name), f"{name} missing from pyiron_workflow_vasp"


def test_assyst_exports_present():
    import pyiron_workflow_assyst as pwa

    for name in ("run_ASSYST_on_structure", "is_valid_structure", "resolve_rcore"):
        assert hasattr(pwa, name), f"{name} missing"
```

- [ ] **Step 1.2: Run to verify it fails**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_imports.py -v`
Expected: first two PASS, `test_assyst_exports_present` FAILS (its target is Task 6).

- [ ] **Step 1.3: Update `pyproject.toml`**

```toml
[project]
requires-python = ">=3.11,<3.14"
dependencies = [
    "pyiron_workflow>=0.19,<0.20",
    "flowrep>=0.6,<0.7",
    "pyiron_workflow_vasp>=0.2.0",
    "pymatgen>=2023.0.0",
    "ase>=3.22.0",
    "numpy>=1.20.0",
    "pandas>=1.3.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["slow: end-to-end graph tests"]
```

Remove the `pyiron_vasp>=0.1.0` dependency — the sibling port deleted that import and nothing here uses it.

- [ ] **Step 1.4: Neutralise `__init__.py`**

```python
"""pyiron_workflow_assyst — ASSYST structure generation and DFT workflow.

Intentionally empty during the 0.19 port: eager imports of `workflow` would
break submodule imports while that module is mid-migration. Task 6 restores
the explicit public surface.
"""

__version__ = "0.2.0.dev0"
```

- [ ] **Step 1.5: Create fixtures**

Create `tests/conftest.py`:

```python
import numpy as np
import pytest
from pymatgen.core import Lattice, Structure


@pytest.fixture
def fe_structure() -> Structure:
    return Structure(Lattice.cubic(2.83), ["Fe"], [[0.0, 0.0, 0.0]])


@pytest.fixture
def two_species_structure() -> Structure:
    """Fe and H at 0.9 A separation - inside any sane core-overlap limit."""
    return Structure(
        Lattice.cubic(6.0),
        ["Fe", "H"],
        [[0.0, 0.0, 0.0], [0.15, 0.0, 0.0]],
    )


@pytest.fixture
def sheared_cell() -> Structure:
    """A heavily sheared cell where the minimum-image convention LIES.

    The true nearest-neighbour distance (via a real neighbour search) is well
    under 1 A, but pymatgen's distance_matrix, which applies the minimum-image
    convention, reports a comfortably larger value. This is the fixture that
    distinguishes the two min_dist backends in Task 3; if a candidate cell does
    not show that discrepancy, it is the wrong fixture and the test proves
    nothing.
    """
    lattice = Lattice([[3.0, 0.0, 0.0], [2.7, 3.0, 0.0], [2.7, 2.7, 3.0]])
    return Structure(lattice, ["H", "H"], [[0.0, 0.0, 0.0], [0.98, 0.0, 0.0]])
```

**Note for the implementer:** the exact `sheared_cell` numbers above are a starting point, not a guarantee. Verify the discrepancy empirically before relying on it:

```python
from pymatgen.core import Structure
import numpy as np
from ase.neighborlist import neighbor_list
from pymatgen.io.ase import AseAtomsAdaptor

s = ...  # the fixture
dm = s.distance_matrix.copy(); np.fill_diagonal(dm, np.inf)
mic = dm.min()
d_true = neighbor_list("d", AseAtomsAdaptor.get_atoms(s), 5.0).min()
print(mic, d_true)   # need d_true < 1.0 < mic
```

If the given lattice does not produce `d_true < 1.0 < mic`, adjust the shear until it does and record the numbers you settled on. Do not proceed to Task 3 without a fixture that demonstrates the discrepancy.

- [ ] **Step 1.6: Commit**

```bash
git add pyproject.toml pyiron_workflow_assyst/__init__.py tests/
git -c user.name="Han Lin Mai" commit -m "test: add harness, pin 0.19 floors, neutralise __init__ during port"
```

---

### Task 2: RCORE derived from the actual POTCAR

**This is a science change, decided deliberately.** The repo's `RCORE` dict and the copy that actually ran every past campaign disagree, and the repo's is wrong for the POTCARs the code selects.

Evidence, gathered by writing real POTCARs through `pyiron_workflow_vasp.write_POTCAR`:

| element | POTCAR actually written | RCORE (bohr) | → Å | repo dict | installed dict |
|---|---|---|---|---|---|
| Fe | `PAW_PBE Fe 06Sep2000` | 2.300 | 1.2171 | 1.2171 | 1.2171 |
| Ti | `PAW_PBE Ti_sv 26Sep2005` | 2.300 | 1.2171 | **1.4817** | 1.2171 |
| Cr | `PAW_PBE Cr_pv 02Aug2007` | 2.300 | 1.2171 | **1.3229** | 1.2171 |
| Zr | `PAW_PBE Zr_sv 04Jan2005` | 2.500 | 1.3229 | **1.5875** | 1.3229 |
| V  | `PAW_PBE V_sv 02Aug2007`  | 2.300 | 1.2171 | **1.4288** | 1.2171 |
| Mo | `PAW_PBE Mo_sv 02Feb2006` | 2.500 | 1.3229 | **1.4552** | 1.3229 |
| Nb | `PAW_PBE Nb_sv 25May2007` | 2.400 | 1.2700 | 1.2700 | 1.2700 |

The code selects `_sv`/`_pv` semicore variants; the installed dict matches those 7/7, the repo dict matches only where the plain and semicore radii coincide. Porting the repo dict unchanged would make the filter stricter than every campaign run to date, using radii for POTCARs that are never written.

**Decision: read RCORE from the POTCAR the run actually uses.** Correct for the default selection (reproducing past campaigns exactly) and correct when `potcar_paths` overrides with different variants.

**Files:**
- Modify: `pyiron_workflow_assyst/structure_filter_utils.py`
- Test: `tests/test_filters.py`

**Interfaces:**
- Produces:
  - `rcore_from_potcar(potcar_path: str) -> dict[str, float]` — parses `TITEL`/`RCORE` pairs from a (possibly concatenated multi-element) POTCAR, returning element symbol → radius in Å.
  - `resolve_rcore(structure, potcar_paths: list[str] | None = None) -> dict[str, float]` — RCORE for each element in `structure`, read from `potcar_paths` when given, else from the POTCARs `pyiron_workflow_vasp` would write by default. Falls back to `RCORE_FALLBACK` with a `warnings.warn` if a POTCAR cannot be read.
  - `RCORE_FALLBACK: dict[str, float]` — the semicore table (the installed dict's values), used only when a POTCAR is unavailable.

- [ ] **Step 2.1: Write the failing tests**

Create `tests/test_filters.py`:

```python
import warnings

import pytest

from pyiron_workflow_assyst.structure_filter_utils import (
    RCORE_FALLBACK,
    rcore_from_potcar,
    resolve_rcore,
)

BOHR = 0.5291773
FE_POTCAR = (
    "/cmmc/u/hmai/pyiron-resources-cmmc/vasp/potentials/"
    "pyiron_nodes/potpaw_64/GGA/Fe/POTCAR"
)
TI_SV_POTCAR = (
    "/cmmc/u/hmai/pyiron-resources-cmmc/vasp/potentials/"
    "pyiron_nodes/potpaw_64/GGA/Ti_sv/POTCAR"
)


def test_rcore_from_potcar_reads_fe():
    rc = rcore_from_potcar(FE_POTCAR)
    assert set(rc) == {"Fe"}
    assert rc["Fe"] == pytest.approx(2.300 * BOHR, abs=1e-4)


def test_rcore_from_potcar_maps_sv_variant_to_bare_element():
    """Ti_sv must key as 'Ti' - the filter looks up by element symbol."""
    rc = rcore_from_potcar(TI_SV_POTCAR)
    assert set(rc) == {"Ti"}
    assert rc["Ti"] == pytest.approx(2.300 * BOHR, abs=1e-4)


def test_resolve_rcore_uses_semicore_defaults_matching_past_campaigns(fe_structure):
    """The default selection must reproduce the values every campaign ran with."""
    from pymatgen.core import Lattice, Structure

    ti = Structure(Lattice.cubic(3.3), ["Ti"], [[0.0, 0.0, 0.0]])
    assert resolve_rcore(ti)["Ti"] == pytest.approx(1.2171, abs=1e-3)
    assert resolve_rcore(fe_structure)["Fe"] == pytest.approx(1.2171, abs=1e-3)


def test_resolve_rcore_honours_explicit_potcar_paths():
    """An explicit plain-Ti POTCAR must give the larger plain radius, not the
    semicore default - this is the case a hardcoded table gets wrong."""
    from pymatgen.core import Lattice, Structure

    plain_ti = (
        "/cmmc/u/hmai/pyiron-resources-cmmc/vasp/potentials/"
        "pyiron_nodes/potpaw_64/GGA/Ti/POTCAR"
    )
    ti = Structure(Lattice.cubic(3.3), ["Ti"], [[0.0, 0.0, 0.0]])
    rc = resolve_rcore(ti, potcar_paths=[plain_ti])
    assert rc["Ti"] == pytest.approx(2.800 * BOHR, abs=1e-3)
    assert rc["Ti"] > resolve_rcore(ti)["Ti"], "plain must exceed semicore"


def test_resolve_rcore_falls_back_and_warns_on_unreadable_potcar():
    from pymatgen.core import Lattice, Structure

    fe = Structure(Lattice.cubic(2.83), ["Fe"], [[0.0, 0.0, 0.0]])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rc = resolve_rcore(fe, potcar_paths=["/nonexistent/POTCAR"])
    assert rc["Fe"] == pytest.approx(RCORE_FALLBACK["Fe"], abs=1e-6)
    assert any("POTCAR" in str(w.message) for w in caught), "must warn on fallback"
```

- [ ] **Step 2.2: Run to verify they fail**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_filters.py -v`
Expected: FAIL on import — `rcore_from_potcar` does not exist.

- [ ] **Step 2.3: Implement**

Replace the single `RCORE` dict in `structure_filter_utils.py`. Keep the existing values but **rename to `RCORE_FALLBACK` and correct them to the semicore radii** (the installed dict's values), since fallback should match what the code actually writes. Add:

```python
import os
import re
import warnings
from functools import lru_cache

BOHR_TO_ANGSTROM = 0.5291773

_TITEL_RE = re.compile(r"TITEL\s*=\s*\S+\s+(\S+)")
_RCORE_RE = re.compile(r"RCORE\s*=\s*([0-9.]+)")


@lru_cache(maxsize=256)
def rcore_from_potcar(potcar_path: str) -> dict[str, float]:
    """Map element symbol -> core radius in Angstrom, read from a POTCAR.

    A POTCAR may concatenate several elements; every TITEL/RCORE pair is read.
    The TITEL carries the variant name (e.g. ``Ti_sv``); the key is the bare
    element symbol, because the filter looks species up by symbol.
    """
    with open(potcar_path, errors="ignore") as handle:
        text = handle.read()
    titles = _TITEL_RE.findall(text)
    radii = _RCORE_RE.findall(text)
    if not titles or len(titles) != len(radii):
        raise ValueError(
            f"Could not read matching TITEL/RCORE pairs from {potcar_path}: "
            f"{len(titles)} titles, {len(radii)} radii"
        )
    return {
        title.split("_")[0]: float(radius) * BOHR_TO_ANGSTROM
        for title, radius in zip(titles, radii)
    }


def resolve_rcore(structure, potcar_paths=None) -> dict[str, float]:
    """Core radii for every element in ``structure``, from the POTCARs in use.

    Reading the POTCAR rather than consulting a table matters because the
    default selection is semicore (``Ti_sv``, ``Cr_pv``, ...), whose radii
    differ from the plain-element ones; a table hardcoded to either is wrong
    for the other.
    """
    symbols = sorted({site.specie.symbol for site in structure})
    if potcar_paths:
        merged: dict[str, float] = {}
        for path in potcar_paths:
            try:
                merged.update(rcore_from_potcar(path))
            except (OSError, ValueError) as exc:
                warnings.warn(
                    f"Could not read RCORE from POTCAR {path!r} ({exc}); "
                    f"falling back to the built-in table.",
                    stacklevel=2,
                )
        if all(sym in merged for sym in symbols):
            return {sym: merged[sym] for sym in symbols}
        missing = [s for s in symbols if s not in merged]
        warnings.warn(
            f"No POTCAR RCORE for {missing}; falling back to the built-in table.",
            stacklevel=2,
        )
        return {sym: merged.get(sym, RCORE_FALLBACK[sym]) for sym in symbols}

    from pyiron_workflow_vasp.vasp import get_default_POTCAR_paths

    try:
        paths = get_default_POTCAR_paths(symbols, [1] * len(symbols))
        merged = {}
        for path in paths:
            merged.update(rcore_from_potcar(path))
        return {sym: merged[sym] for sym in symbols}
    except Exception as exc:
        warnings.warn(
            f"Could not resolve default POTCARs ({exc}); "
            f"falling back to the built-in RCORE table.",
            stacklevel=2,
        )
        return {sym: RCORE_FALLBACK[sym] for sym in symbols}
```

**Implementer note:** `get_default_POTCAR_paths`'s exact signature must be confirmed against the installed `pyiron_workflow_vasp` 0.2.0 before use — the call above is the intended behaviour, not a verified signature. Read the function and adapt. If it needs an `ase.Atoms` rather than a symbol list, build a throwaway one from the structure.

Then update `filter_distance_by_species` to take the resolved mapping:

```python
def filter_distance_by_species(structure, rcore, core_overlap_tolerance=0.2):
    pair = _element_wise_dist(structure)
    species_list = sorted({site.specie.symbol for site in structure})
    for ei, ej in combinations_with_replacement(species_list, 2):
        allowed = (1 - core_overlap_tolerance) * (rcore[ei] + rcore[ej])
        if pair[ei, ej] < allowed:
            return False
    return True
```

Delete the dead `structure_copy` block (it computed a 2×2×2 expansion and then passed the original `structure` to `_element_wise_dist` anyway; `get_all_neighbors` handles periodicity correctly, so the expansion was never needed).

- [ ] **Step 2.4: Run to verify they pass**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_filters.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 2.5: Mutation-prove the campaign-compatibility test**

Temporarily change `RCORE_FALLBACK["Ti"]` to the plain-POTCAR value `1.4817` AND force `resolve_rcore` down its fallback path. Confirm `test_resolve_rcore_uses_semicore_defaults_matching_past_campaigns` FAILS. Restore, confirm it PASSES. Report both runs. This proves the test actually pins the semicore values rather than accepting whatever the table says.

- [ ] **Step 2.6: Commit**

```bash
git add pyiron_workflow_assyst/structure_filter_utils.py tests/test_filters.py
git -c user.name="Han Lin Mai" commit -m "feat: derive RCORE from the POTCAR actually used, not a hardcoded table"
```

---

### Task 3: Fix the minimum-distance bug

**The mechanism is not what this plan originally claimed.** Task 1's implementer investigated
(~4000 randomised skewed cells) and found pymatgen's `distance_matrix` is numerically robust for
genuine cross-atom pairs — it does not exhibit a classic minimum-image failure. The controller
verified the corrected diagnosis independently. The real defect is narrower and worse:

`get_minimum_distance` computes `structure.distance_matrix` and then calls
`np.fill_diagonal(dm, np.inf)`. The distance matrix covers only *cross-atom* pairs, and zeroing the
diagonal discards **every atom's distance to its own periodic image**. Consequences, both measured:

- **A single-atom cell returns `inf`.** With one atom the matrix is all-diagonal, so after the fill
  every entry is `inf` and `np.min` is `inf`. The check `inf < min_dist` is never true, so
  **the `min_dist` floor is completely inert for one-atom cells** — verified: a 1-atom cubic H cell
  with a = 0.9 Å (the atom 0.9 Å from its own image) passes a `min_dist=1.0` floor. This matters
  because ASSYST unary generation runs `min_atoms=1`, so one-atom cells are routinely produced.
- **Short lattice vectors hide contacts in multi-atom cells too.** Verified: a 2-atom cell with a
  0.95 Å lattice vector reports a minimum of 4.2426 Å while the true nearest contact is 0.95 Å.

A real periodic neighbour search counts self-images and reports both correctly. Note the RCORE check
in `filter_distance_by_species` uses `get_all_neighbors`, which *does* see self-images, so it has
been catching most of these — the flat floor was the broken half.

**Files:**
- Modify: `pyiron_workflow_assyst/structure_filter_utils.py`
- Test: `tests/test_filters.py`

**Interfaces:**
- Produces: `get_minimum_distance(structure, backend: str = "neighbor_list") -> float` and `is_valid_structure(structure, min_dist=1.0, core_overlap_tolerance=0.2, potcar_paths=None, min_dist_backend="neighbor_list") -> bool`.

- [ ] **Step 3.1: Write the failing test**

Append to `tests/test_filters.py`:

```python
import numpy as np

from pyiron_workflow_assyst.structure_filter_utils import (
    get_minimum_distance,
    is_valid_structure,
)


def test_mic_backend_overestimates_distance_in_sheared_cell(sheared_cell):
    """Documents the bug the default backend exists to avoid.

    If this test ever fails, the fixture has stopped exhibiting the
    discrepancy and every other test in this group is proving nothing.
    """
    mic = get_minimum_distance(sheared_cell, backend="mic")
    true = get_minimum_distance(sheared_cell, backend="neighbor_list")
    assert true < 1.0, f"fixture must have a genuine sub-A contact, got {true}"
    assert mic > true, f"mic ({mic}) should overestimate vs true ({true})"


def test_mic_backend_is_inert_for_single_atom_cells():
    """The most damaging form of the bug, and the one that reached production.

    With one atom the distance matrix is all-diagonal, so excluding the
    diagonal leaves nothing and the minimum is inf - the min_dist floor can
    never reject a one-atom cell however compressed it is. ASSYST unary
    generation runs with min_atoms=1, so these are produced routinely.
    """
    from pymatgen.core import Lattice, Structure

    tiny = Structure(Lattice.cubic(0.9), ["H"], [[0.0, 0.0, 0.0]])
    assert get_minimum_distance(tiny, backend="mic") == float("inf")
    assert get_minimum_distance(tiny, backend="neighbor_list") == pytest.approx(0.9)
    assert is_valid_structure(tiny, min_dist=1.0, min_dist_backend="mic") is True
    assert is_valid_structure(tiny, min_dist=1.0) is False


def test_short_lattice_vector_hides_contact_from_mic_backend():
    """A multi-atom cell can also hide a contact along a short lattice vector."""
    from pymatgen.core import Lattice, Structure

    s = Structure(
        Lattice([[0.95, 0, 0], [0, 6, 0], [0, 0, 6]]),
        ["H", "H"],
        [[0.0, 0.0, 0.0], [0.0, 0.5, 0.5]],
    )
    assert get_minimum_distance(s, backend="mic") > 4.0
    assert get_minimum_distance(s, backend="neighbor_list") == pytest.approx(0.95)


def test_default_backend_rejects_the_sub_angstrom_contact(sheared_cell):
    assert is_valid_structure(sheared_cell, min_dist=1.0) is False


def test_mic_backend_wrongly_accepts_it(sheared_cell):
    """Retained so pre-2026 campaigns can be reproduced exactly."""
    assert (
        is_valid_structure(sheared_cell, min_dist=1.0, min_dist_backend="mic") is True
    )


def test_backends_agree_on_an_undistorted_cell(fe_structure):
    assert get_minimum_distance(fe_structure, backend="mic") == pytest.approx(
        get_minimum_distance(fe_structure, backend="neighbor_list"), rel=1e-6
    )


def test_unknown_backend_raises():
    from pymatgen.core import Lattice, Structure

    s = Structure(Lattice.cubic(3.0), ["Fe"], [[0, 0, 0]])
    with pytest.raises(ValueError, match="backend"):
        get_minimum_distance(s, backend="telepathy")
```

- [ ] **Step 3.2: Run to verify they fail**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_filters.py -k "backend or angstrom" -v`
Expected: FAIL — `get_minimum_distance` takes no `backend` argument.

- [ ] **Step 3.3: Implement**

```python
def get_minimum_distance(structure, backend: str = "neighbor_list") -> float:
    """Smallest interatomic distance in ``structure``.

    ``neighbor_list`` (default) performs a real periodic neighbour search.
    ``mic`` uses pymatgen's distance matrix, which applies the minimum-image
    convention and OVERESTIMATES the minimum in heavily sheared or strained
    cells - it can miss the true nearest periodic image entirely. It is kept
    only to reproduce campaigns run before this was fixed.
    """
    if backend == "mic":
        distance_matrix = structure.distance_matrix.copy()
        np.fill_diagonal(distance_matrix, np.inf)
        return float(np.min(distance_matrix))
    if backend == "neighbor_list":
        from ase.neighborlist import neighbor_list
        from pymatgen.io.ase import AseAtomsAdaptor

        atoms = AseAtomsAdaptor.get_atoms(structure)
        cutoff = 5.0
        for _ in range(4):
            distances = neighbor_list("d", atoms, cutoff)
            if len(distances):
                return float(distances.min())
            cutoff *= 2
        return float("inf")
    raise ValueError(
        f"Unknown backend {backend!r}; expected 'neighbor_list' or 'mic'."
    )


def is_valid_structure(
    structure,
    min_dist=1.0,
    core_overlap_tolerance=0.2,
    potcar_paths=None,
    min_dist_backend="neighbor_list",
):
    if get_minimum_distance(structure, backend=min_dist_backend) < min_dist:
        return False
    rcore = resolve_rcore(structure, potcar_paths=potcar_paths)
    return filter_distance_by_species(
        structure, rcore, core_overlap_tolerance=core_overlap_tolerance
    )
```

The cutoff-doubling loop matters: a very dilute cell can have no neighbour inside 5 Å, and `neighbor_list` would return an empty array whose `.min()` raises.

- [ ] **Step 3.4: Run to verify they pass**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_filters.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 3.5: Commit**

```bash
git add pyiron_workflow_assyst/structure_filter_utils.py tests/test_filters.py
git -c user.name="Han Lin Mai" commit -m "fix: use a real neighbour search for min distance; keep mic backend for reproducibility"
```

---

### Task 4: Extract and test the deformation generators

The rattle/triaxial/shear functions and the node that drives them currently live in `workflow.py` mixed with graph plumbing. They are pure functions of a structure and belong in their own module, which also makes them testable without building a graph.

The existing `get_ASSYST_deformed_structures` has redundant outer `for` loops wrapping `while` loops that already fill to the target count; the outer loop bodies are no-ops after the first iteration. Simplify while preserving the output ordering, which is `[rattle × n, triax × n, shear × n]` per input structure, with `job_names` parallel to `all_structures`.

**Files:**
- Create: `pyiron_workflow_assyst/perturb.py`
- Test: `tests/test_perturb.py`

**Interfaces:**
- Produces (plain functions): `apply_triaxial_strain(structure, max_strain=0.8)`, `apply_shear_strain(structure, max_strain=0.8)`, `apply_rattle(structure, displacement=0.5, max_cell_strain=0.05)`.
- Produces (`@fr.atomic("all_structures", "job_names")`): `get_ASSYST_deformed_structures(structure_list, job_basename, n_stretch_permutations=5, n_rattle_permutations=5, shear_strain=0.8, triaxial_strain=0.8, rattle_displacement=0.1, rattle_strain=0.05, min_dist=1.0, core_overlap_tolerance=0.2, potcar_paths=None, min_dist_backend="neighbor_list", max_attempts=100, seed=None)`.

Add `seed` — the generators use `np.random` and are currently untestable deterministically.

- [ ] **Step 4.1: Write the failing tests**

Create `tests/test_perturb.py`:

```python
import numpy as np
import pyiron_workflow as pwf
import pytest
from pymatgen.io.ase import AseAtomsAdaptor

from pyiron_workflow_assyst.perturb import (
    apply_rattle,
    apply_shear_strain,
    apply_triaxial_strain,
    get_ASSYST_deformed_structures,
)


def test_rattle_moves_atoms_but_preserves_composition(fe_structure):
    np.random.seed(0)
    out = apply_rattle(fe_structure, displacement=0.1, max_cell_strain=0.05)
    assert out.composition == fe_structure.composition
    assert len(out) == len(fe_structure)


def test_triaxial_strain_changes_volume(fe_structure):
    np.random.seed(0)
    out = apply_triaxial_strain(fe_structure, max_strain=0.5)
    assert out.volume != pytest.approx(fe_structure.volume)


def test_shear_strain_changes_angles_not_composition(fe_structure):
    np.random.seed(0)
    out = apply_shear_strain(fe_structure, max_strain=0.5)
    assert out.composition == fe_structure.composition
    assert out.lattice.angles != pytest.approx(fe_structure.lattice.angles)


def test_deformed_structures_counts_and_name_ordering(fe_structure):
    atoms = AseAtomsAdaptor.get_atoms(fe_structure)
    run = pwf.node(get_ASSYST_deformed_structures).run(
        structure_list=[atoms],
        job_basename=["base0"],
        n_stretch_permutations=2,
        n_rattle_permutations=3,
        seed=1234,
    )
    structures = run.outputs.all_structures
    names = run.outputs.job_names
    assert len(structures) == len(names) == 3 + 2 + 2
    assert [n.split("_")[1] for n in names] == (
        ["rattle"] * 3 + ["triax"] * 2 + ["shear"] * 2
    )
    assert all(n.startswith("base0_") for n in names)


def test_deformed_structures_are_deterministic_under_seed(fe_structure):
    atoms = AseAtomsAdaptor.get_atoms(fe_structure)
    kwargs = dict(
        structure_list=[atoms],
        job_basename=["b"],
        n_stretch_permutations=2,
        n_rattle_permutations=2,
        seed=7,
    )
    a = pwf.node(get_ASSYST_deformed_structures).run(**kwargs).outputs.all_structures
    b = pwf.node(get_ASSYST_deformed_structures).run(**kwargs).outputs.all_structures
    for x, y in zip(a, b):
        assert np.allclose(x.get_positions(), y.get_positions())
        assert np.allclose(x.get_cell(), y.get_cell())


def test_every_returned_structure_passes_the_validity_filter(fe_structure):
    """The generator's contract: it only emits structures that pass."""
    from pyiron_workflow_assyst.structure_filter_utils import is_valid_structure

    atoms = AseAtomsAdaptor.get_atoms(fe_structure)
    run = pwf.node(get_ASSYST_deformed_structures).run(
        structure_list=[atoms],
        job_basename=["b"],
        n_stretch_permutations=3,
        n_rattle_permutations=3,
        seed=99,
    )
    for atoms_out in run.outputs.all_structures:
        structure = AseAtomsAdaptor.get_structure(atoms_out)
        assert is_valid_structure(structure, min_dist=1.0, core_overlap_tolerance=0.2)


def test_gives_up_after_max_attempts_rather_than_looping_forever(two_species_structure):
    """A structure that cannot be validly deformed must terminate, returning
    fewer structures than requested rather than hanging."""
    atoms = AseAtomsAdaptor.get_atoms(two_species_structure)
    run = pwf.node(get_ASSYST_deformed_structures).run(
        structure_list=[atoms],
        job_basename=["tight"],
        n_stretch_permutations=2,
        n_rattle_permutations=2,
        rattle_displacement=3.0,
        max_attempts=5,
        seed=3,
    )
    assert len(run.outputs.all_structures) == len(run.outputs.job_names)
    assert run.status == "finished"
```

- [ ] **Step 4.2: Run to verify they fail**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_perturb.py -v`
Expected: FAIL on import — `pyiron_workflow_assyst.perturb` does not exist.

- [ ] **Step 4.3: Implement**

Move `apply_triaxial_strain`, `apply_shear_strain`, `apply_rattle` verbatim from `workflow.py` into `perturb.py`. Rewrite the driver with the redundant outer loops removed and a seeded RNG:

```python
@fr.atomic("all_structures", "job_names")
def get_ASSYST_deformed_structures(
    structure_list,
    job_basename,
    n_stretch_permutations=5,
    n_rattle_permutations=5,
    shear_strain=0.8,
    triaxial_strain=0.8,
    rattle_displacement=0.1,
    rattle_strain=0.05,
    min_dist=1.0,
    core_overlap_tolerance=0.2,
    potcar_paths=None,
    min_dist_backend="neighbor_list",
    max_attempts=100,
    seed=None,
):
    """Generate validity-filtered rattle/triaxial/shear variants of each input.

    Output order per input structure is rattle, then triaxial, then shear;
    ``job_names`` is parallel to ``all_structures``. Generation retries until
    ``max_attempts`` per requested variant, so a structure that cannot be
    validly deformed yields fewer variants rather than looping forever.
    """
    if seed is not None:
        np.random.seed(seed)
    all_structures, job_names = [], []
    for idx, atoms in enumerate(structure_list):
        structure = AseAtomsAdaptor.get_structure(atoms)
        base = job_basename[idx]
        for kind, count, generate in (
            ("rattle", n_rattle_permutations,
             lambda s: apply_rattle(s, rattle_displacement, rattle_strain)),
            ("triax", n_stretch_permutations,
             lambda s: apply_triaxial_strain(s, triaxial_strain)),
            ("shear", n_stretch_permutations,
             lambda s: apply_shear_strain(s, shear_strain)),
        ):
            made = 0
            attempts = 0
            while made < count and attempts < max_attempts * count:
                attempts += 1
                candidate = generate(structure)
                if is_valid_structure(
                    candidate,
                    min_dist=min_dist,
                    core_overlap_tolerance=core_overlap_tolerance,
                    potcar_paths=potcar_paths,
                    min_dist_backend=min_dist_backend,
                ):
                    made += 1
                    all_structures.append(AseAtomsAdaptor.get_atoms(candidate))
                    job_names.append(f"{base}_{kind}_{made}")
    return all_structures, job_names
```

Note the closure over loop variables in the tuple is evaluated immediately at each iteration, so the `lambda`s are safe here; but they are defined inside the function, which flowrep permits because only the decorated function itself must be importable.

- [ ] **Step 4.4: Run to verify they pass**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_perturb.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 4.5: Mutation-prove the validity-filter contract test**

Temporarily make the generator append candidates without calling `is_valid_structure`. Confirm `test_every_returned_structure_passes_the_validity_filter` FAILS (with a large `rattle_displacement` if needed to force an invalid candidate). Restore, confirm PASS. Report both runs.

- [ ] **Step 4.6: Commit**

```bash
git add pyiron_workflow_assyst/perturb.py tests/test_perturb.py
git -c user.name="Han Lin Mai" commit -m "feat: extract seeded, filter-honouring deformation generators"
```

---

### Task 5: Port `collect_structures` and `run_ASSYST_on_structure`

**Files:**
- Modify: `pyiron_workflow_assyst/workflow.py`
- Test: `tests/test_collect.py`, `tests/test_workflow.py`

**Interfaces:**
- Produces `@fr.atomic("structures", "names", "energies")`: `collect_structures(vasp_output, job_name, image_selection_eVatom_threshold=-1)`.
- Produces `@fr.workflow("train_df")`: `run_ASSYST_on_structure(structure, incar, potcar_paths, job_name, vasp_command, ionic_steps=100, n_stretch_permutations=2, n_rattle_permutations=2, image_selection_eVatom_threshold=-1, shear_strain=0.8, triaxial_strain=0.8, rattle_displacement=0.1, rattle_strain=0.05, core_overlap_tolerance=0.3, min_dist_backend="neighbor_list", compress_dirs=True, compressed_file_in_dir=False, remove_calc_dirs=True, train_df_filename="df_ASSYST_jobs.pkl", seed=None)`.

**Two corrections to carry over from the sibling port**, both already proven there:

1. **Use the most recent parser row, not the first.** The old `collect_structures` reads `df.structures.iloc[0]`. `parse_vasp_directory` returns one row per `OUTCAR*` plus one per custodian error archive, sorted ascending by `calc_start_time`, so `.iloc[0]` is the *oldest* — with custodian restarts (ASSYST runs `max_errors=10`) that is the crashed calculation. Use `.iloc[-1]`.
2. **Coerce to `str` before `Structure.from_str`.** The `structures` column holds `numpy.str_`, and pymatgen's `orjson` reader rejects it despite it subclassing `str`.

- [ ] **Step 5.1: Write the failing tests for `collect_structures`**

Create `tests/test_collect.py`:

```python
import numpy as np
import pandas as pd
import pyiron_workflow as pwf
import pytest
from pymatgen.core import Lattice, Structure

from pyiron_workflow_assyst.workflow import collect_structures


def _row(lattice_params, n_images, energies, scf, start_time):
    structures = np.array(
        [
            Structure(Lattice.cubic(a), ["Fe"], [[0, 0, 0]]).to_json()
            for a in lattice_params
        ]
    )
    assert len(structures) == n_images
    return {
        "structures": structures,
        "energy": np.array(energies),
        "scf_convergence": np.array(scf),
        "calc_start_time": start_time,
    }


def test_collect_takes_last_image_by_default():
    df = pd.DataFrame([_row([4.0, 3.5, 3.0], 3, [-1.0, -2.0, -3.0], [True] * 3, 1)])
    run = pwf.node(collect_structures).run(vasp_output=df, job_name="ISIF2")
    assert len(run.outputs.structures) == 1
    assert run.outputs.structures[0].get_cell()[0][0] == pytest.approx(3.0)
    assert run.outputs.names == ["ISIF2_accur_relaxstep2"]


def test_collect_drops_unconverged_images():
    df = pd.DataFrame([_row([4.0, 3.0], 2, [-1.0, -2.0], [True, False], 1)])
    run = pwf.node(collect_structures).run(
        vasp_output=df, job_name="ISIF2", image_selection_eVatom_threshold=0.001
    )
    assert all(name.endswith("relaxstep0") for name in run.outputs.names)
    assert len(run.outputs.structures) == 1


def test_collect_uses_the_most_recent_row_not_the_first():
    """With a custodian error archive present the parser returns several rows,
    ascending by start time. The crashed earliest run must NOT be chosen."""
    crashed = _row([9.9], 1, [-0.1], [True], 1)
    good = _row([3.0], 1, [-3.0], [True], 2)
    df = pd.DataFrame([crashed, good])
    run = pwf.node(collect_structures).run(vasp_output=df, job_name="ISIF2")
    assert run.outputs.structures[0].get_cell()[0][0] == pytest.approx(3.0), (
        "picked the crashed earliest row"
    )


def test_collect_threshold_selects_multiple_images():
    df = pd.DataFrame(
        [_row([4.0, 3.9, 3.0], 3, [-1.0, -1.0005, -3.0], [True] * 3, 1)]
    )
    run = pwf.node(collect_structures).run(
        vasp_output=df, job_name="ISIF2", image_selection_eVatom_threshold=0.1
    )
    assert len(run.outputs.structures) >= 2
    assert len(run.outputs.structures) == len(run.outputs.names)
```

- [ ] **Step 5.2: Run to verify they fail**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_collect.py -v`
Expected: FAIL — `collect_structures` has the old signature and cannot be built by `pwf.node`.

- [ ] **Step 5.3: Implement `collect_structures`**

```python
@fr.atomic("structures", "names", "energies")
def collect_structures(vasp_output, job_name, image_selection_eVatom_threshold=-1):
    """Select relaxation images from a parsed VASP directory.

    Takes the MOST RECENT row (``.iloc[-1]``): parse_vasp_directory returns one
    row per OUTCAR plus one per custodian error archive, ascending by start
    time, so the first row is the oldest - the crashed run in a restart chain.

    ``image_selection_eVatom_threshold=-1`` keeps only the final image.
    Images whose SCF did not converge are dropped.
    """
    row = vasp_output.iloc[-1]
    json_blobs = row["structures"]
    energies = list(row["energy"])
    scf = list(row["scf_convergence"])

    first = Structure.from_str(str(json_blobs[0]), fmt="json")
    n_atoms = len(first)
    ev_atom = [e / n_atoms for e in energies]

    if image_selection_eVatom_threshold == -1:
        selected = [len(ev_atom) - 1]
    else:
        selected = select_indices_by_threshold(
            ev_atom, threshold=image_selection_eVatom_threshold
        )

    structures, names, kept_energies = [], [], []
    for i in selected:
        if not scf[i]:
            continue
        structure = Structure.from_str(str(json_blobs[i]), fmt="json")
        structures.append(AseAtomsAdaptor.get_atoms(structure))
        names.append(f"{job_name}_accur_relaxstep{i}")
        kept_energies.append(energies[i])
    return structures, names, kept_energies
```

- [ ] **Step 5.4: Run to verify they pass**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_collect.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5.5: Mutation-prove the most-recent-row test**

Change `.iloc[-1]` to `.iloc[0]`; confirm `test_collect_uses_the_most_recent_row_not_the_first` FAILS. Restore; confirm PASS. Report both.

- [ ] **Step 5.6: Write the end-to-end graph test**

Create `tests/test_workflow.py`:

```python
import os

import numpy as np
import pandas as pd
import pyiron_workflow as pwf
import pytest
from pymatgen.core import Lattice, Structure
from pymatgen.io.vasp.inputs import Incar

from pyiron_workflow_assyst.workflow import run_ASSYST_on_structure


def _fake_parser(directory):
    """Stand in for parse_vasp_directory with a realistically-shaped frame."""
    blobs = np.array(
        [Structure(Lattice.cubic(a), ["Fe"], [[0, 0, 0]]).to_json() for a in (3.0, 2.9)]
    )
    return pd.DataFrame(
        [
            {
                "structures": blobs,
                "energy": np.array([-8.0, -8.2]),
                "scf_convergence": np.array([True, True]),
                "calc_start_time": 1,
                "job_name": os.path.basename(str(directory)),
            }
        ]
    )


@pytest.mark.slow
def test_assyst_graph_runs_end_to_end(tmp_path):
    job_name = str((tmp_path / "struct_0").resolve())
    incar = Incar.from_dict({"ENCUT": 300, "ISPIN": 1, "NSW": 5})

    node = pwf.node(run_ASSYST_on_structure)
    run = node.run(
        structure=Structure(Lattice.cubic(2.83), ["Fe"], [[0, 0, 0]]),
        incar=incar,
        potcar_paths=None,
        job_name=job_name,
        vasp_command="echo 'reached required accuracy' > vasp.log",
        ionic_steps=5,
        n_stretch_permutations=1,
        n_rattle_permutations=1,
        remove_calc_dirs=False,
        compress_dirs=False,
        train_df_filename=str(tmp_path / "df_ASSYST_jobs.pkl"),
        seed=42,
        vasp_parser_function=_fake_parser,
    )

    assert run.status == "finished", f"graph failed: {run.exception}"
    df = run.outputs.train_df
    assert isinstance(df, pd.DataFrame)
    assert len(df) > 0
    assert os.path.exists(tmp_path / "df_ASSYST_jobs.pkl")


def test_assyst_graph_has_the_expected_isif_chain():
    """The three relaxation stages must be present and ordered 7 -> 5 -> 2."""
    node = pwf.node(run_ASSYST_on_structure)
    labels = list(node.nodes.keys())
    assert sum("vasp_job" in label for label in labels) >= 3
    assert any("for_each" in label for label in labels), "fan-outs missing"
```

- [ ] **Step 5.7: Implement `run_ASSYST_on_structure`**

```python
@fr.workflow("train_df")
def run_ASSYST_on_structure(
    structure,
    incar,
    potcar_paths,
    job_name,
    vasp_command,
    ionic_steps=100,
    n_stretch_permutations=2,
    n_rattle_permutations=2,
    image_selection_eVatom_threshold=-1,
    shear_strain=0.8,
    triaxial_strain=0.8,
    rattle_displacement=0.1,
    rattle_strain=0.05,
    core_overlap_tolerance=0.3,
    min_dist_backend="neighbor_list",
    compress_dirs=True,
    compressed_file_in_dir=False,
    remove_calc_dirs=True,
    train_df_filename="df_ASSYST_jobs.pkl",
    seed=None,
    vasp_parser_function=None,
):
    """Relax a structure through ISIF 7 -> 5 -> 2, then run accurate statics on
    the converged images and on rattle/triaxial/shear permutations of them.

    All work directories are absolute paths derived from ``job_name``; nothing
    depends on the process working directory.
    """
    relax_incar = set_ionic_steps(incar, ionic_steps)

    incar7 = generate_modified_incar(relax_incar, {"ISIF": 7})
    input7 = generate_vasp_input(structure, incar7, potcar_paths)
    path7 = join_path(job_name, "ISIF7")
    out7, conv7 = vasp_job(
        path7, input7, vasp_command, None, compress_dirs,
        compressed_file_in_dir, remove_calc_dirs, vasp_parser_function, None,
    )

    incar5 = generate_modified_incar(relax_incar, {"ISIF": 5})
    input5 = construct_sequential_vasp_input(out7, incar5, potcar_paths)
    path5 = join_path(job_name, "ISIF5")
    out5, conv5 = vasp_job(
        path5, input5, vasp_command, None, compress_dirs,
        compressed_file_in_dir, remove_calc_dirs, vasp_parser_function, None,
    )

    incar2 = generate_modified_incar(relax_incar, {"ISIF": 2})
    input2 = construct_sequential_vasp_input(out5, incar2, potcar_paths)
    path2 = join_path(job_name, "ISIF2")
    out2, conv2 = vasp_job(
        path2, input2, vasp_command, None, compress_dirs,
        compressed_file_in_dir, remove_calc_dirs, vasp_parser_function, None,
    )

    base_structures, base_names, base_energies = collect_structures(
        out2, "ISIF2", image_selection_eVatom_threshold
    )

    accurate_incar = generate_modified_incar(
        incar,
        {"KSPACING": 0.25, "EDIFFG": 1e-4, "EDIFF": 1e-5, "LREAL": False, "NSW": 0},
    )

    base_results = []
    for base_structure, base_name in zip(base_structures, base_names):
        base_input = generate_vasp_input(base_structure, accurate_incar, potcar_paths)
        base_path = join_path(job_name, base_name)
        base_out, base_conv = vasp_job(
            base_path, base_input, vasp_command, None, compress_dirs,
            compressed_file_in_dir, remove_calc_dirs, vasp_parser_function, None,
        )
        base_results.append(base_out)

    perm_structures, perm_names = get_ASSYST_deformed_structures(
        base_structures,
        base_names,
        n_stretch_permutations,
        n_rattle_permutations,
        shear_strain,
        triaxial_strain,
        rattle_displacement,
        rattle_strain,
        1.0,
        core_overlap_tolerance,
        potcar_paths,
        min_dist_backend,
        100,
        seed,
    )

    perm_results = []
    for perm_structure, perm_name in zip(perm_structures, perm_names):
        perm_input = generate_vasp_input(perm_structure, accurate_incar, potcar_paths)
        perm_path = join_path(job_name, perm_name)
        perm_out, perm_conv = vasp_job(
            perm_path, perm_input, vasp_command, None, compress_dirs,
            compressed_file_in_dir, remove_calc_dirs, vasp_parser_function, None,
        )
        perm_results.append(perm_out)

    train_df = concat_and_save(base_results, perm_results, train_df_filename)
    return train_df
```

Supporting atomics to add:

```python
@fr.atomic("incar")
def set_ionic_steps(incar, ionic_steps):
    modified = Incar.from_dict(dict(incar))
    modified["NSW"] = ionic_steps
    return modified


@fr.atomic("path")
def join_path(base, leaf):
    return os.path.join(base, leaf)


@fr.atomic("df")
def concat_and_save(base_results, perm_results, filename):
    frames = [f for f in list(base_results) + list(perm_results) if f is not None]
    combined = pd.concat(frames, ignore_index=True)
    combined.to_pickle(filename)
    return combined
```

Delete outright, with no replacement: `generate_VaspInputs`, `get_multiple_input` usages, `get_multiple_vasp_parser_args`, `get_df_from_vaspfornode`, `get_string`, `get_ionic_steps_dict`, `convert_pymatgen_to_ase`, `convert_ase_to_pymatgen`, `get_vasp_parser_args`, `get_concat_df`, `save_df`. `ForEach` broadcasts non-iterated inputs, `VaspInput.__post_init__` normalises structures, and the accumulator pattern replaces list-building.

- [ ] **Step 5.8: Run the graph tests**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_workflow.py -v`
Expected: PASS, 2 tests. If flowrep rejects any construct in the workflow body, report the exact error rather than restructuring blindly — the body was probed and the shape is known to parse.

- [ ] **Step 5.9: Print the real graph shape**

```bash
/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/python -c "
import pyiron_workflow as pwf
from pyiron_workflow_assyst.workflow import run_ASSYST_on_structure
n = pwf.node(run_ASSYST_on_structure)
print('inputs :', list(n.inputs.keys()))
print('outputs:', list(n.outputs.keys()))
print('children:', list(n.nodes.keys()))
"
```

Paste the real output into your report — downstream `assystant` work needs the child labels.

- [ ] **Step 5.10: Commit**

```bash
git add pyiron_workflow_assyst/workflow.py tests/test_collect.py tests/test_workflow.py
git -c user.name="Han Lin Mai" commit -m "feat: port the ASSYST graph to flowrep with zip-loop fan-outs"
```

---

### Task 6: Restore exports, bump version, full green

**Files:**
- Modify: `pyiron_workflow_assyst/__init__.py`, `pyproject.toml`
- Test: `tests/test_imports.py`

- [ ] **Step 6.1: Run the failing test**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/test_imports.py::test_assyst_exports_present -v`
Expected: FAIL — `__init__.py` is still neutralised.

- [ ] **Step 6.2: Write the exports**

```python
"""pyiron_workflow_assyst - ASSYST structure generation and DFT workflow."""

from pyiron_workflow_assyst.perturb import (
    apply_rattle,
    apply_shear_strain,
    apply_triaxial_strain,
    get_ASSYST_deformed_structures,
)
from pyiron_workflow_assyst.structure_filter_utils import (
    RCORE_FALLBACK,
    filter_distance_by_species,
    get_minimum_distance,
    is_valid_structure,
    rcore_from_potcar,
    resolve_rcore,
)
from pyiron_workflow_assyst.workflow import (
    collect_structures,
    run_ASSYST_on_structure,
    select_indices_by_threshold,
)

__version__ = "0.2.0"

__all__ = [
    "RCORE_FALLBACK",
    "apply_rattle",
    "apply_shear_strain",
    "apply_triaxial_strain",
    "collect_structures",
    "filter_distance_by_species",
    "get_ASSYST_deformed_structures",
    "get_minimum_distance",
    "is_valid_structure",
    "rcore_from_potcar",
    "resolve_rcore",
    "run_ASSYST_on_structure",
    "select_indices_by_threshold",
]
```

Set `version = "0.2.0"` in `pyproject.toml`.

Note the old `__init__.py` exported `RCORE`; that name is deliberately gone, since a bare table is no longer the source of truth. Anything importing `RCORE` must move to `resolve_rcore`.

- [ ] **Step 6.3: Full suite**

Run: `/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pytest tests/ -v`
Expected: all pass (3 imports + 10 filters + 7 perturb + 4 collect + 2 workflow = 26).

- [ ] **Step 6.4: Clean-environment install check**

```bash
/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/pip install -e /cmmc/u/hmai/pip_repos/pyiron_workflow_assyst
/cmmc/ptmp/hmai/mambaforge/envs/assystant/bin/python -c "
import pyiron_workflow_assyst as pwa
print(pwa.__version__, len(pwa.__all__), 'exports')
"
```

- [ ] **Step 6.5: Commit**

```bash
git add pyiron_workflow_assyst/__init__.py pyproject.toml tests/test_imports.py
git -c user.name="Han Lin Mai" commit -m "feat: explicit exports and 0.2.0 bump for the 0.19 port"
```

---

## Self-Review

**Spec coverage.** The design spec's Repo 2 section requires: `structure_filter_utils.py` bug fixes (Tasks 2 and 3), `run_ASSYST_on_structure` rewritten as `@fr.workflow` with `for ... in zip(...)` fan-outs (Task 5), and removal of the `Length`/`get_multiple_input`/`inputs_to_list` scaffolding (Task 5, deletion list). The `min_dist_backend` config switch the spec calls for is Task 3. The RCORE decision supersedes the spec's "delete the dead duplicate dict" instruction — the repo turned out to have only one dict, and the wrong one, which the spec did not anticipate; Task 2 records the evidence.

**Type consistency.** `resolve_rcore` is used by `filter_distance_by_species` (Task 2), `is_valid_structure` (Task 3), and `get_ASSYST_deformed_structures` (Task 4), with `potcar_paths` threaded through all three. `collect_structures` returns `(structures, names, energies)` in Task 5's atomic and is consumed with exactly that arity in the workflow body. `get_ASSYST_deformed_structures` returns `(all_structures, job_names)` and is consumed with that arity. `vasp_job`'s nine positional arguments in the workflow body match the signature quoted in "Verified before planning".

**Known uncertainty, flagged rather than hidden.** Two things in this plan are intended behaviour rather than verified fact, and the implementer is told so at the point of use: `get_default_POTCAR_paths`'s exact signature (Step 2.3), and whether the `sheared_cell` lattice in Step 1.5 actually produces the MIC discrepancy (Step 1.5 carries a verification snippet and an instruction not to proceed without it). Everything else — nested workflow calls, two-output unpacking, nested workflows inside `zip` loops, and the full ported `pyiron_workflow_vasp` API — was probed against the live environment before writing.

**Deliberately not covered.** The `assystant` CLI itself (Repo 3) and any real-VASP end-to-end run of the ASSYST chain. The latter is worth doing once this lands, but it is a multi-hour, many-core job rather than the single-atom check the sibling port used, so it belongs to `assystant`'s own validation rather than here.
