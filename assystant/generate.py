"""PyXtal structure generation: spacegroup x stoichiometry scan -> POSCARs + manifest.

Ported from ``/u/hmai/2024_12_05_DFTPyxtalAssystNodes/2025_08_31_PyxtalUnaryGen.ipynb``
(the current generation notebook), with three deliberate changes:

* structures are written straight from the worker's canonical POSCAR string
  rather than round-tripped through a pickled DataFrame of pymatgen objects;
* the 0.1 Angstrom symmetry-breaking perturbation that ``calculate_assyst.py``
  applied at *submit* time happens here instead, so the POSCAR on disk, the
  content hash naming its run directory, and the geometry actually calculated
  are the same object;
* every structure gets a content-hashed run-directory name (see
  :func:`struct_dir_name`).

**Why content-hashed directories.**  executorlib's cache is keyed on node
lexical path with no hashing of inputs.  Re-running ``gen`` with a different
seed produces a different structure at index 5 while ``struct_00005/`` still
holds a populated cache, and the stale cache would be silently reused --
returning another structure's VASP results.  Deriving the directory name from
the structure's content makes that impossible by construction: a changed
structure cannot land in a populated namespace.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from concurrent.futures import ProcessPoolExecutor
from itertools import product
from pathlib import Path

import pandas as pd

from .config import CampaignConfig, resolve_eq_volume_map, system_name

__all__ = [
    "MANIFEST_NAME",
    "STRUCTURES_DIRNAME",
    "enumerate_compositions",
    "adjust_volume_per_atom",
    "poscar_hash",
    "struct_dir_name",
    "parse_struct_dir",
    "generate_structures",
    "write_system",
    "read_manifest",
    "run_generation",
    "limit_blas_threads",
]

MANIFEST_NAME = "manifest.csv"
#: Columns that must stay strings on re-read. A unary "2" composition or a hash
#: of all digits would otherwise come back as an int and break the join.
MANIFEST_DTYPES = {"hash": str, "composition": str, "formula": str, "dirname": str}
STRUCTURES_DIRNAME = "structures"

_BLAS_THREAD_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)

_NEW_DIR_RE = re.compile(r"^struct_(\d+)_([0-9a-f]{8})$")
_LEGACY_DIR_RE = re.compile(r"^struct_pyxtal_(\d+)$")


def limit_blas_threads() -> None:
    """Pin BLAS/OpenMP to one thread per process.

    ``gen`` fans out over ``ProcessPoolExecutor``; without this every worker's
    BLAS spawns ``os.cpu_count()`` threads and the node thrashes.  Set
    unconditionally (not ``setdefault``) so an inherited ``OMP_NUM_THREADS=8``
    from the login shell cannot reintroduce the oversubscription this exists to
    prevent.  Scoped to the ``assystant gen`` process.
    """
    for var in _BLAS_THREAD_VARS:
        os.environ[var] = "1"


# -- composition enumeration ---------------------------------------------
def enumerate_compositions(
    species,
    min_atoms: int,
    max_atoms: int,
    stoich_min: int = 1,
    stoich_max: int = 12,
) -> list[tuple[int, ...]]:
    """Composition tuples to scan for ``species``.

    Unary systems enumerate cell sizes directly, ``[(n,) for n in
    range(min_atoms, max_atoms + 1)]`` -- ``stoich_min``/``stoich_max`` do not
    apply, because for one species the stoichiometry *is* the atom count.
    Multi-component systems take the Cartesian product of per-species counts
    over ``[stoich_min, stoich_max]`` and keep only those with
    ``min_atoms <= sum(comp) <= max_atoms``.
    """
    k = len(species)
    if k == 0:
        return []
    if k == 1:
        return [(n,) for n in range(min_atoms, max_atoms + 1)]
    raw = product(range(stoich_min, stoich_max + 1), repeat=k)
    return [comp for comp in raw if min_atoms <= sum(comp) <= max_atoms]


# -- volume rescaling -----------------------------------------------------
def adjust_volume_per_atom(structure, min_volume: float, max_volume: float):
    """Rescale ``structure`` into the [min, max] volume-per-atom band.

    A structure already inside the band is returned untouched; otherwise the
    lattice is scaled to whichever bound it violated.
    """
    current_vpa = structure.volume / structure.num_sites
    if min_volume <= current_vpa <= max_volume:
        return structure
    target_vpa = min_volume if current_vpa < min_volume else max_volume
    adjusted = structure.copy()
    adjusted.scale_lattice(target_vpa * structure.num_sites)
    return adjusted


# -- content hashing / directory naming -----------------------------------
def poscar_hash(poscar: str) -> str:
    """First eight hex characters of the SHA-256 of a canonical POSCAR string."""
    return hashlib.sha256(poscar.encode("utf-8")).hexdigest()[:8]


def struct_dir_name(index: int, sha8: str) -> str:
    """``struct_<idx:05d>_<sha8>`` -- the per-structure run-directory name."""
    return f"struct_{int(index):05d}_{sha8}"


def parse_struct_dir(name: str):
    """Parse a run-directory name into ``(index, sha8_or_None, layout)``.

    Recognises both the hashed layout written by this package and the legacy
    ``struct_pyxtal_<idx>`` layout of the pre-2026 campaigns, so existing data
    stays readable without migration.  Returns ``None`` for anything else.
    """
    match = _NEW_DIR_RE.match(name)
    if match:
        return int(match.group(1)), match.group(2), "hashed"
    match = _LEGACY_DIR_RE.match(name)
    if match:
        return int(match.group(1)), None, "legacy"
    return None


# -- the scan -------------------------------------------------------------
def _gen_one(
    sg: int,
    species: list,
    comp: tuple,
    eq_volume_map: dict,
    strain_min: float,
    strain_max: float,
    seed,
    perturb: float,
):
    """Generate one random structure.

    Returns a record dict on success, or ``{"__error__": <exception class name>}``
    on failure.  Failure is routine -- pyxtal raises ``Comp_CompatibilityError``
    whenever a composition cannot be placed in a spacegroup's Wyckoff positions,
    which is most (spacegroup, composition) pairs -- but reporting *which*
    exception lets :func:`generate_structures` distinguish "225 cannot hold one
    atom" from a genuine bug that would otherwise silently produce an empty
    campaign.

    Runs in a worker process, so everything it touches must be picklable and it
    must never raise (a raised exception would poison the pool).
    """
    try:
        import numpy as np
        from pyxtal import pyxtal

        # NEITHER pyxtal 1.1.4 NOR pymatgen's Structure.perturb reads numpy's
        # global RNG -- pyxtal draws from its own Generator and perturb calls
        # np.random.default_rng(seed=seed) -- so the source notebook's bare
        # np.random.seed() did not actually make generation reproducible.  Both
        # need the seed passed explicitly.  np.random is seeded as well, for any
        # library further down that does still use the global state.
        rng_seed = None if seed is None else int(seed) % (2**32)
        if rng_seed is not None:
            np.random.seed(rng_seed)

        xtal = pyxtal()
        xtal.from_random(
            dim=3, group=sg, species=list(species), numIons=list(comp),
            random_state=rng_seed,
        )
        pmg = xtal.to_pymatgen()

        comp_dict = pmg.composition.get_el_amt_dict()
        missing = [el for el in comp_dict if eq_volume_map.get(el) is None]
        if missing:
            pmg_adj = pmg
        else:
            total = sum(comp_dict.values())
            eq_vpa = sum(comp_dict[el] * eq_volume_map[el] for el in comp_dict) / total
            pmg_adj = adjust_volume_per_atom(pmg, eq_vpa * strain_min, eq_vpa * strain_max)

        if perturb:
            pmg_adj = pmg_adj.copy()
            try:
                pmg_adj.perturb(float(perturb), seed=rng_seed)
            except TypeError:  # pragma: no cover - pymatgen < 2024 has no `seed`
                pmg_adj.perturb(float(perturb))

        poscar = pmg_adj.to(fmt="poscar")
        return {
            "poscar": poscar,
            "hash": poscar_hash(poscar),
            "spacegroup": int(sg),
            "composition": "-".join(str(c) for c in comp),
            "formula": pmg_adj.composition.formula.replace(" ", ""),
            "n_atoms": int(pmg_adj.num_sites),
            "volume_per_atom": float(pmg_adj.volume / pmg_adj.num_sites),
        }
    except Exception as exc:
        return {"__error__": type(exc).__name__}


def _gen_one_star(args):
    """Top-level (picklable) star-unpacking adapter for ``ProcessPoolExecutor.map``."""
    return _gen_one(*args)


def generate_structures(
    species,
    eq_volume_map: dict,
    strain_min: float = 0.8,
    strain_max: float = 1.2,
    stoich_min: int = 1,
    stoich_max: int = 12,
    min_atoms: int = 1,
    max_atoms: int = 12,
    sg_min: int = 1,
    sg_max: int = 230,
    perturb: float = 0.1,
    seed=None,
    n_jobs: int | None = None,
    chunksize: int | None = None,
    progress: bool = True,
) -> list[dict]:
    """Scan spacegroups x compositions in parallel and return the successes."""
    species = list(species)
    compositions = enumerate_compositions(species, min_atoms, max_atoms, stoich_min, stoich_max)
    total_tasks = max(0, (sg_max - sg_min + 1)) * len(compositions)
    if total_tasks == 0:
        return []
    if n_jobs is None:
        n_jobs = os.cpu_count() or 1

    limit_blas_threads()

    def task_iter():
        counter = 0
        for sg in range(sg_min, sg_max + 1):
            for comp in compositions:
                if seed is None:
                    task_seed = secrets.randbits(32)
                else:
                    # Deterministic but decorrelated per task.
                    task_seed = int(
                        hashlib.sha256(
                            f"{seed}|{sg}|{comp}|{counter}".encode()
                        ).hexdigest()[:8],
                        16,
                    )
                counter += 1
                yield (sg, species, comp, eq_volume_map, strain_min, strain_max,
                       task_seed, perturb)

    records = []
    failures: dict[str, int] = {}
    with ProcessPoolExecutor(max_workers=n_jobs, initializer=limit_blas_threads) as pool:
        iterable = pool.map(_gen_one_star, task_iter(), chunksize=chunksize or 1)
        if progress:
            iterable = _tqdm(iterable, total=total_tasks,
                             desc=f"PyXtal scan {system_name(species)}")
        for record in iterable:
            if record is None:
                failures["None"] = failures.get("None", 0) + 1
            elif "__error__" in record:
                reason = record["__error__"]
                failures[reason] = failures.get(reason, 0) + 1
            else:
                records.append(record)
    if failures:
        tally = ", ".join(f"{name} x{count}" for name, count in sorted(failures.items()))
        print(
            f"{system_name(species)}: {len(records)}/{total_tasks} (spacegroup, "
            f"composition) pairs produced a structure; rejected: {tally}"
        )
    return records


def _tqdm(iterable, **kwargs):
    try:
        from tqdm import tqdm
    except ImportError:  # pragma: no cover - tqdm is a hard dependency
        return iterable
    return tqdm(iterable, **kwargs)


# -- on-disk layout -------------------------------------------------------
def write_system(root, species, records) -> pd.DataFrame:
    """Write POSCARs and ``manifest.csv`` for one system; return the manifest."""
    species = list(species)
    sys_dir = Path(root) / system_name(species)
    struct_dir = sys_dir / STRUCTURES_DIRNAME
    struct_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for index, record in enumerate(records):
        poscar_name = f"{index:05d}_structure.vasp"
        (struct_dir / poscar_name).write_text(record["poscar"])
        rows.append(
            {
                "index": index,
                "system": system_name(species),
                "dirname": struct_dir_name(index, record["hash"]),
                "hash": record["hash"],
                "spacegroup": record["spacegroup"],
                "composition": record["composition"],
                "formula": record["formula"],
                "n_atoms": record["n_atoms"],
                "volume_per_atom": record["volume_per_atom"],
                "poscar": str(struct_dir / poscar_name),
            }
        )

    manifest = pd.DataFrame(
        rows,
        columns=[
            "index", "system", "dirname", "hash", "spacegroup", "composition",
            "formula", "n_atoms", "volume_per_atom", "poscar",
        ],
    )
    manifest.to_csv(sys_dir / MANIFEST_NAME, index=False)
    return manifest


def read_manifest(root, species) -> pd.DataFrame | None:
    """Read one system's manifest, or ``None`` if it has not been generated."""
    path = Path(root) / system_name(list(species)) / MANIFEST_NAME
    if not path.is_file():
        return None
    return pd.read_csv(path, dtype=MANIFEST_DTYPES)


def run_generation(cfg: CampaignConfig, progress: bool = True) -> dict:
    """Run the PyXtal scan for every system in ``cfg`` and write the manifests."""
    eq_volume_map = resolve_eq_volume_map(cfg.system.eq_volume_source)
    manifests = {}
    for species in cfg.system.systems:
        records = generate_structures(
            species,
            eq_volume_map,
            strain_min=cfg.system.strain_min,
            strain_max=cfg.system.strain_max,
            stoich_min=cfg.system.stoich_min,
            stoich_max=cfg.system.stoich_max,
            min_atoms=cfg.system.min_atoms,
            max_atoms=cfg.system.max_atoms,
            sg_min=cfg.system.sg_min,
            sg_max=cfg.system.sg_max,
            perturb=cfg.system.perturb,
            seed=cfg.system.seed,
            n_jobs=cfg.system.n_jobs,
            progress=progress,
        )
        manifests[system_name(species)] = write_system(cfg.root, species, records)
    return manifests
