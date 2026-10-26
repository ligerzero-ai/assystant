"""Campaign configuration: YAML -> :class:`CampaignConfig`, CLI overrides, provenance dump.

The YAML schema is documented in ``docs/2026_07_31_assystant_design.md`` (section
"Campaign YAML") and in the README.  Three responsibilities live here:

1. **Parsing** a campaign YAML into typed, defaulted dataclasses, rejecting
   unknown keys rather than silently ignoring a typo'd tag.
2. **Overrides** -- every scalar is addressable by a dotted path
   (``slurm.ntasks``) so the CLI can override it.  Precedence is
   CLI flag > YAML > dataclass default.
3. **Provenance and the staleness guard** -- :meth:`CampaignConfig.dump`
   produces a canonical dict that ``assystant run`` writes into the campaign
   directory.  A later ``run`` compares against it and refuses to proceed if
   anything calculation-affecting changed, because executorlib's cache is keyed
   on node lexical path with no hashing of inputs: reusing a ``run_dir`` after
   an INCAR change would silently return the previous result.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "ConfigError",
    "StaleConfigError",
    "CampaignConfig",
    "SystemConfig",
    "MagmomConfig",
    "AssystConfig",
    "FiltersConfig",
    "SlurmConfig",
    "VaspConfig",
    "RECORDED_CONFIG_NAME",
    "DEFAULT_INCAR_STATIC",
    "effective_static_overrides",
    "recorded_config_path",
    "load_recorded_config",
    "calculation_key",
    "config_differences",
    "merge_site",
    "load_campaign_dict",
    "expand_environment",
    "enumerate_compositions",
    "resolve_stoich_range",
]

#: Name of the resolved-config file written into the campaign root by ``run``.
RECORDED_CONFIG_NAME = "assystant_config.yaml"

#: What the accurate statics -- the calculations that produce the training
#: labels -- change relative to the relaxation ``incar`` when ``incar_static``
#: says nothing: a denser k-mesh, tighter electronic and ionic convergence and
#: reciprocal-space projection.  Identical to ``STATIC_INCAR_OVERRIDES`` in
#: pyiron_workflow_assyst, which is what every campaign before ``incar_static``
#: existed ran with.  ``NSW = 0`` and the absence of ``ISIF`` are not listed
#: because they are invariants of a statics job, not settings.
DEFAULT_INCAR_STATIC = {"KSPACING": 0.25, "EDIFF": 1e-5, "EDIFFG": 1e-4, "LREAL": False}

#: Sections excluded from the staleness comparison.  ``slurm`` describes how the
#: work reaches the scheduler (wall time, task count, partition), not what is
#: calculated, so changing it must not force the destructive ``--from-scratch``
#: path.  Everything else -- system, incar, magmom, assyst, filters, vasp -- can
#: change the numbers and therefore invalidates an existing run directory.
_STALENESS_EXEMPT_SECTIONS = ("slurm",)

#: Marker opening every value a bundled template leaves for the user to fill in
#: (see :mod:`assystant.examples`).  A real ``run`` refuses to submit while one
#: is set, so nothing is ever computed with it, and the staleness guard ignores a
#: *recorded* placeholder -- filling one in after a ``run --dry-run`` is not a
#: change of calculation settings.
PLACEHOLDER_PREFIX = "<EDIT"

#: ``root`` used when the YAML and the CLI leave it unset: the directory the
#: command runs from, resolved to an absolute path by :meth:`CampaignConfig.resolve`.
DEFAULT_ROOT = "."

#: Bundled equilibrium-volume table (Voronoi volumes from Materials Project).
DEFAULT_EQ_VOLUME_CSV = Path(__file__).parent / "data" / "EleBulk_MatProj_VorVol.csv"


class ConfigError(ValueError):
    """Raised for a malformed or self-inconsistent campaign configuration."""


class StaleConfigError(RuntimeError):
    """Raised by ``run`` when the resolved config differs from the recorded one.

    See ``docs/2026_07_31_assystant_design.md`` -> "Cache-staleness hazard".
    """


def _check_keys(name: str, given: dict, allowed) -> None:
    unknown = sorted(set(given) - set(allowed))
    if unknown:
        raise ConfigError(
            f"unknown key(s) in `{name}` section: {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(allowed))}"
        )


def _from_section(cls, name: str, data: Any):
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise ConfigError(f"`{name}` must be a mapping, got {type(data).__name__}")
    _check_keys(name, data, {f.name for f in fields(cls)})
    return cls(**data)


@dataclass
class SystemConfig:
    """The species / stoichiometry / spacegroup scan that ``gen`` walks.

    ``species`` accepts either a flat list of element symbols (one system, e.g.
    ``[Fe, C]`` is the binary Fe-C) or a list of lists (several systems in one
    campaign, e.g. ``[[Fe], [Ni], [Fe, C]]`` -- this is what replaces the
    hand-maintained ``submit_temp.sh`` with one entry per element).  It is
    normalised to a list of lists on parse.
    """

    species: Any = field(default_factory=list)
    min_atoms: int = 1
    max_atoms: int = 12
    #: Per-species count bounds for multi-component systems.  ``null`` (the
    #: default) means the full composition range: ``stoich_min`` -> 1 and
    #: ``stoich_max`` -> ``max_atoms``, so every split of every cell size in
    #: ``[min_atoms, max_atoms]`` with each species present is scanned.  Set
    #: either explicitly only to narrow the scan.  Unary systems ignore both.
    stoich_min: Any = None
    stoich_max: Any = None
    spacegroups: Any = field(default_factory=lambda: [1, 230])
    strain_min: float = 0.8
    strain_max: float = 1.2
    eq_volume_source: Any = None
    #: Random displacement (Angstrom) applied to every generated structure.
    #: The pre-2026 scripts perturbed at *submit* time; doing it at *generation*
    #: time instead means the POSCAR on disk, its content hash, and the geometry
    #: actually calculated are the same object.  0.1 reproduces production.
    perturb: float = 0.1
    #: Master seed.  ``null`` -> a fresh ``secrets.randbits(32)`` per task (the
    #: ported behaviour); an integer -> deterministic, reproducible generation.
    seed: Any = None
    #: Worker processes for the PyXtal scan.  ``null`` -> ``os.cpu_count()``.
    n_jobs: Any = None

    def __post_init__(self):
        self.species = _normalise_species(self.species)
        self.spacegroups = _normalise_spacegroups(self.spacegroups)

    @property
    def systems(self) -> list[list[str]]:
        return [list(s) for s in self.species]

    @property
    def stoich_range(self) -> tuple[int, int]:
        """Effective ``(stoich_min, stoich_max)``: ``null`` -> ``(1, max_atoms)``."""
        return resolve_stoich_range(self.stoich_min, self.stoich_max, self.max_atoms)

    @property
    def sg_min(self) -> int:
        return int(self.spacegroups[0])

    @property
    def sg_max(self) -> int:
        return int(self.spacegroups[1])


def _normalise_species(raw) -> list[list[str]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raise ConfigError(
            "`system.species` must be a list, e.g. [Fe] or [[Fe], [Ni]], "
            f"not the bare string {raw!r}"
        )
    raw = list(raw)
    if not raw:
        return []
    if all(isinstance(x, str) for x in raw):
        return [list(raw)]
    if all(isinstance(x, (list, tuple)) for x in raw):
        out = []
        for group in raw:
            if not group or not all(isinstance(x, str) for x in group):
                raise ConfigError(
                    f"`system.species` entry {group!r} must be a non-empty list "
                    "of element symbols"
                )
            out.append(list(group))
        return out
    raise ConfigError(
        "`system.species` must be either a flat list of symbols ([Fe, C]) or a "
        "list of lists ([[Fe], [Fe, C]]), not a mixture"
    )


def _normalise_spacegroups(raw) -> list[int]:
    if isinstance(raw, dict):
        _check_keys("system.spacegroups", raw, {"min", "max"})
        raw = [raw.get("min", 1), raw.get("max", 230)]
    if isinstance(raw, int):
        raw = [raw, raw]
    try:
        lo, hi = raw
    except (TypeError, ValueError):
        raise ConfigError(f"`system.spacegroups` must be a [min, max] pair, got {raw!r}") from None
    return [int(lo), int(hi)]


def resolve_stoich_range(stoich_min, stoich_max, max_atoms: int) -> tuple[int, int]:
    """``null`` stoichiometry bounds mean the full composition range.

    ``stoich_min: null`` -> 1 (every species present in every cell) and
    ``stoich_max: null`` -> ``max_atoms``.  Together with the total-atom filter
    in :func:`enumerate_compositions` that is "for a cell of ``n`` atoms, every
    species count from 1 to ``n - (k - 1)``" -- the whole composition range for
    every cell size, which is what a multi-component ASSYST scan should cover
    unless deliberately narrowed.
    """
    lo = 1 if stoich_min is None else int(stoich_min)
    hi = int(max_atoms) if stoich_max is None else int(stoich_max)
    return lo, hi


def enumerate_compositions(
    species,
    min_atoms: int,
    max_atoms: int,
    stoich_min=None,
    stoich_max=None,
) -> list[tuple[int, ...]]:
    """Composition tuples to scan for ``species``, in lexicographic order.

    Unary systems enumerate cell sizes directly, ``[(n,) for n in
    range(min_atoms, max_atoms + 1)]`` -- ``stoich_min``/``stoich_max`` do not
    apply, because for one species the stoichiometry *is* the atom count.

    Multi-component systems take every per-species count in
    ``[stoich_min, stoich_max]`` and keep the tuples with
    ``min_atoms <= sum(comp) <= max_atoms``.  With the defaults
    (``None`` -> :func:`resolve_stoich_range`) that is the full composition
    range: a binary scans ``(1, n-1) .. (n-1, 1)`` for every ``n`` from
    ``max(min_atoms, 2)`` to ``max_atoms``, a ternary every three-way split
    from ``n = 3`` up.

    The order is lexicographic, identical to ``itertools.product`` filtered, and
    must stay so: the per-task generation seeds are keyed on position.
    """
    species = list(species)
    k = len(species)
    if k == 0:
        return []
    if k == 1:
        return [(n,) for n in range(int(min_atoms), int(max_atoms) + 1)]
    lo, hi = resolve_stoich_range(stoich_min, stoich_max, max_atoms)
    min_atoms, max_atoms = int(min_atoms), int(max_atoms)
    out: list[tuple[int, ...]] = []

    def walk(prefix: tuple[int, ...], total: int, remaining: int) -> None:
        if remaining == 0:
            if min_atoms <= total <= max_atoms:
                out.append(prefix)
            return
        # Every remaining species needs at least ``lo`` more atoms.
        budget = max_atoms - total - lo * (remaining - 1)
        for n in range(lo, min(hi, budget) + 1):
            walk(prefix + (n,), total + n, remaining - 1)

    walk((), 0, k)
    return out


@dataclass
class MagmomConfig:
    """MAGMOM construction.

    ``host: null`` means "the first species of each system", which is what makes
    a multi-system unary campaign (``[[La], [Ce], [Pr], ...]``) express in one
    file -- each element is its own host.
    """

    host: Any = None
    host_moment: float = 3.0
    solute_moment: float = -0.5
    #: Per-element initial moments, ``{Fe: 3.0, Mn: -2.0, C: 0.0}``.  An element
    #: listed here gets that moment in every system; one not listed falls back
    #: to the host / solute rule.  This is what lets a multi-element campaign
    #: give each solute its own moment instead of one ``solute_moment`` for all.
    moments: Any = None

    def __post_init__(self):
        self.moments = {str(k): v for k, v in (self.moments or {}).items()}

    def host_for(self, species: list[str]) -> str:
        return self.host if self.host is not None else species[0]

    def moment_for(self, element: str, host: str) -> float:
        """Initial moment for ``element`` given the system's resolved ``host``."""
        if element in self.moments:
            return float(self.moments[element])
        return float(self.host_moment if element == host else self.solute_moment)


@dataclass
class AssystConfig:
    """Parameters forwarded to ``run_ASSYST_on_structure``."""

    ionic_steps: int = 100
    n_rattle: int = 5
    n_stretch: int = 5
    shear_strain: float = 0.8
    triaxial_strain: float = 0.8
    rattle_displacement: float = 0.1
    rattle_strain: float = 0.05
    core_overlap_tolerance: float = 0.3
    image_selection_eVatom_threshold: float = -1
    compress_dirs: bool = True
    compressed_file_in_dir: bool = False
    remove_calc_dirs: bool = True
    seed: Any = None


@dataclass
class FiltersConfig:
    """Structure-validity filter settings.

    ``min_dist`` is carried for provenance only: ``run_ASSYST_on_structure``
    (pyiron_workflow_assyst 0.2.0) hardcodes the 1.0 Angstrom floor it passes to
    ``get_ASSYST_deformed_structures`` and exposes no parameter for it, so any
    other value cannot be honoured.  Rather than accept it and silently ignore
    it, :meth:`CampaignConfig.validate` rejects it.
    """

    min_dist: float = 1.0
    min_dist_backend: str = "neighbor_list"


@dataclass
class SlurmConfig:
    partition: str = "s.cmmg"
    ntasks: int = 64
    cpus_per_task: int = 1
    time: str = "48:00:00"
    mem_per_cpu: str = "2000MB"
    #: Conda prefix whose ``bin/python`` runs the in-job runner.  Defaults to the
    #: interpreter running ``assystant`` itself.
    env: Any = None
    account: Any = None
    #: Verbatim extra ``#SBATCH`` lines, e.g. ["--reservation=benchmarking"].
    extra_directives: list = field(default_factory=list)

    def python_executable(self) -> str:
        if self.env:
            return str(Path(self.env) / "bin" / "python")
        import sys

        return sys.executable


@dataclass
class VaspConfig:
    binary: str = "vasp_std"
    custodian: bool = True
    #: Interpreter that runs the custodian wrapper inside the allocation.  It
    #: needs ``custodian`` importable; ``null`` falls back to ``slurm.env``.
    custodian_python: Any = None
    modules: list = field(default_factory=lambda: ["intel/2024.0", "impi/2021.11", "mkl/2024.0"])
    max_errors: int = 10
    #: MPI launcher placed before :attr:`binary`.  ``null`` means the SLURM
    #: default, ``srun -c 1 -n <slurm.ntasks> --hint=nomultithread``.  Set it
    #: explicitly to run outside an allocation -- ``"mpirun -n 1"`` for a local
    #: smoke test, or ``""`` to invoke the binary with no launcher at all.
    #: ``{ntasks}`` in the string is substituted with the effective task count.
    launcher: Any = None
    #: The POTCAR configuration file ``pyiron_workflow_vasp`` reads (the file
    #: otherwise looked up as ``$PYIRON_VASP_CONFIG`` or
    #: ``~/.pyiron_vasp_config``).  ``null`` leaves that lookup alone; a path
    #: pins it: the rendered job script exports ``PYIRON_VASP_CONFIG`` and the
    #: in-job runner sets it before the library is imported, so the campaign
    #: no longer depends on the submitting shell's environment or home directory.
    potcar_config: Any = None


@dataclass
class CampaignConfig:
    #: Campaign directory.  Empty -> :data:`DEFAULT_ROOT` (the current working
    #: directory) when the config is resolved.
    root: str = ""
    system: SystemConfig = field(default_factory=SystemConfig)
    incar: dict = field(default_factory=dict)
    #: Overrides, on top of ``incar``, for the accurate statics that produce the
    #: training labels.  Layered over :data:`DEFAULT_INCAR_STATIC`; a tag set to
    #: ``null`` drops the default and inherits the relaxation value.  See
    #: :func:`effective_static_overrides`.
    incar_static: dict = field(default_factory=dict)
    magmom: MagmomConfig = field(default_factory=MagmomConfig)
    assyst: AssystConfig = field(default_factory=AssystConfig)
    filters: FiltersConfig = field(default_factory=FiltersConfig)
    slurm: SlurmConfig = field(default_factory=SlurmConfig)
    vasp: VaspConfig = field(default_factory=VaspConfig)

    # -- construction -----------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict) -> "CampaignConfig":
        if not isinstance(data, dict):
            raise ConfigError(f"campaign config must be a mapping, got {type(data).__name__}")
        _check_keys("<top level>", data, {f.name for f in fields(cls)})
        incar = data.get("incar") or {}
        if not isinstance(incar, dict):
            raise ConfigError("`incar` must be a mapping of INCAR tag -> value")
        incar_static = data.get("incar_static") or {}
        if not isinstance(incar_static, dict):
            raise ConfigError(
                "`incar_static` must be a mapping of INCAR tag -> value (or null to "
                "inherit the relaxation value)"
            )
        return cls(
            root=str(data.get("root", "") or ""),
            system=_from_section(SystemConfig, "system", data.get("system")),
            incar={str(k).upper(): v for k, v in incar.items()},
            incar_static={str(k).upper(): v for k, v in incar_static.items()},
            magmom=_from_section(MagmomConfig, "magmom", data.get("magmom")),
            assyst=_from_section(AssystConfig, "assyst", data.get("assyst")),
            filters=_from_section(FiltersConfig, "filters", data.get("filters")),
            slurm=_from_section(SlurmConfig, "slurm", data.get("slurm")),
            vasp=_from_section(VaspConfig, "vasp", data.get("vasp")),
        )

    @classmethod
    def from_yaml(cls, path, sites=()) -> "CampaignConfig":
        """Load ``path``; ``sites`` are site YAMLs merged over it, in order."""
        return cls.from_dict(load_campaign_dict(path, sites))

    # -- overrides and validation ----------------------------------------
    def resolve(self, overrides: dict | None = None) -> "CampaignConfig":
        """Return a new, validated config with dotted-path ``overrides`` applied.

        Precedence is CLI flag > YAML > dataclass default, which falls out of
        applying the overrides on top of the parsed YAML.  ``root`` is made
        absolute so every downstream path is absolute; an unset ``root``
        defaults to the current working directory (:data:`DEFAULT_ROOT`), so
        each system lands in ``./<system>/``.
        """
        out = copy.deepcopy(self)
        for dotted, value in (overrides or {}).items():
            if value is None:
                continue
            out._set_dotted(dotted, value)
        out._expand_environment()
        out.root = str(Path(out.root or DEFAULT_ROOT).expanduser().resolve())
        out.validate()
        return out

    def _expand_environment(self) -> None:
        """``${VAR}`` / ``$VAR`` / leading ``~`` in the machine-specific sections.

        Applied to ``root`` and to every string in ``slurm`` and ``vasp`` (list
        items included), so one campaign YAML can say
        ``binary: ${VASP_STD}`` and be submitted from several machines.  An
        unset variable is an error, not an empty string: a silently empty
        ``vasp.binary`` would only fail inside the allocation.
        """
        self.root = expand_environment(self.root, "root")
        for section in ("slurm", "vasp"):
            obj = getattr(self, section)
            for fld in fields(obj):
                value = getattr(obj, fld.name)
                setattr(obj, fld.name, expand_environment(value, f"{section}.{fld.name}"))

    def _set_dotted(self, dotted: str, value) -> None:
        parts = dotted.split(".")
        target = self
        for part in parts[:-1]:
            if not hasattr(target, part):
                raise ConfigError(f"unknown config path: {dotted}")
            target = getattr(target, part)
        leaf = parts[-1]
        if isinstance(target, dict):
            target[leaf] = value
            return
        if not hasattr(target, leaf):
            raise ConfigError(f"unknown config path: {dotted}")
        setattr(target, leaf, value)
        if is_dataclass(target) and hasattr(target, "__post_init__"):
            target.__post_init__()

    def validate(self) -> None:
        s, a, f = self.system, self.assyst, self.filters
        if not self.root:
            raise ConfigError("`root` is required (the campaign directory)")
        if not s.systems:
            raise ConfigError("`system.species` is required and must be non-empty")
        _validate_symbols(s.systems)
        if s.min_atoms < 1:
            raise ConfigError(f"`system.min_atoms` must be >= 1, got {s.min_atoms}")
        if s.max_atoms < s.min_atoms:
            raise ConfigError(
                f"`system.max_atoms` ({s.max_atoms}) must be >= `system.min_atoms` "
                f"({s.min_atoms})"
            )
        lo, hi = s.stoich_range
        if lo < 1 or hi < lo:
            raise ConfigError(
                f"`system.stoich_min`/`stoich_max` must satisfy 1 <= min <= max, "
                f"got {lo}/{hi} (null means 1 / max_atoms: the full composition range)"
            )
        if not (1 <= s.sg_min <= s.sg_max <= 230):
            raise ConfigError(
                f"`system.spacegroups` must satisfy 1 <= min <= max <= 230, " f"got {s.spacegroups}"
            )
        if not (0 < s.strain_min <= s.strain_max):
            raise ConfigError(
                f"`system.strain_min`/`strain_max` must satisfy 0 < min <= max, "
                f"got {s.strain_min}/{s.strain_max}"
            )
        if s.perturb < 0:
            raise ConfigError(f"`system.perturb` must be >= 0, got {s.perturb}")

        if a.ionic_steps < 1:
            raise ConfigError(f"`assyst.ionic_steps` must be >= 1, got {a.ionic_steps}")
        if a.n_rattle < 0 or a.n_stretch < 0:
            raise ConfigError(
                "`assyst.n_rattle` and `assyst.n_stretch` must be >= 0, got "
                f"{a.n_rattle}/{a.n_stretch}"
            )
        if a.n_rattle == 0 and a.n_stretch == 0:
            # pyiron_workflow 0.19.0 cannot scatter a length-0 ForEach iterable
            # (https://github.com/pyiron/pyiron_workflow/issues/915), so
            # run_ASSYST_on_structure raises NoPermutationsGeneratedError rather
            # than running "relax and statics only".  Reject it here, where the
            # message can name the YAML keys, instead of deep in the graph.
            raise ConfigError(
                "`assyst.n_rattle` and `assyst.n_stretch` cannot both be 0: a "
                "permutation-free ('relax and statics only') campaign is not "
                "supported. pyiron_workflow 0.19.0's ForEach raises an opaque "
                "IndexError on a length-0 iterable "
                "(https://github.com/pyiron/pyiron_workflow/issues/915), so "
                "run_ASSYST_on_structure rejects an empty permutation set with "
                "NoPermutationsGeneratedError. Set at least one of them to >= 1."
            )

        _validate_ibrion(self.incar, a.ionic_steps)
        _validate_incar_static(self.incar_static)
        _validate_moments(self.magmom.moments)

        if f.min_dist_backend not in ("neighbor_list", "mic"):
            raise ConfigError(
                f"`filters.min_dist_backend` must be 'neighbor_list' or 'mic', "
                f"got {f.min_dist_backend!r}"
            )
        if float(f.min_dist) != 1.0:
            raise ConfigError(
                f"`filters.min_dist` must be 1.0 (got {f.min_dist}): "
                "run_ASSYST_on_structure in pyiron_workflow_assyst 0.2.0 hardcodes "
                "the 1.0 Angstrom floor it passes to get_ASSYST_deformed_structures "
                "and exposes no parameter for it, so any other value would be "
                "silently ignored."
            )
        if not self.vasp.binary:
            raise ConfigError("`vasp.binary` is required")
        if self.slurm.ntasks < 1:
            raise ConfigError(f"`slurm.ntasks` must be >= 1, got {self.slurm.ntasks}")

    # -- provenance -------------------------------------------------------
    def dump(self) -> dict:
        """Canonical, YAML-safe dict of the fully resolved configuration.

        Round-trips through :meth:`from_dict`, so the recorded file is a usable
        campaign config in its own right (the in-job runner loads it directly).
        """

        def sect(obj):
            return {fld.name: getattr(obj, fld.name) for fld in fields(obj)}

        system = sect(self.system)
        # Record the effective bounds, not ``null``: the file must describe the
        # scan that was run even if a later release changes the default.
        system["stoich_min"], system["stoich_max"] = self.system.stoich_range
        return {
            "root": self.root,
            "system": system,
            "incar": dict(self.incar),
            "incar_static": dict(self.incar_static),
            "magmom": sect(self.magmom),
            "assyst": sect(self.assyst),
            "filters": sect(self.filters),
            "slurm": sect(self.slurm),
            "vasp": sect(self.vasp),
        }

    def dump_to(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            yaml.safe_dump(self.dump(), fh, sort_keys=True, default_flow_style=False)
        return path

    # -- convenience ------------------------------------------------------
    @property
    def root_path(self) -> Path:
        return Path(self.root)

    def system_names(self) -> list[str]:
        return [system_name(sp) for sp in self.system.systems]

    def system_dir(self, species) -> Path:
        return self.root_path / system_name(species)


def system_name(species) -> str:
    """Directory name for a system: ``Fe``, ``Fe_C``, ``Fe_B_C``."""
    return "_".join(species)


def _validate_ibrion(incar: dict, ionic_steps: int) -> None:
    """Reject an INCAR that would let VASP default IBRION to 0 (MD) mid-relaxation.

    ``run_ASSYST_on_structure`` sets ``NSW=ionic_steps`` for its three relaxation
    stages, so whenever a relaxation actually runs (``ionic_steps > 0``) an
    unset IBRION is always wrong: VASP defaults it to 0 (molecular dynamics),
    which additionally requires POTIM and refuses to start without it --

        Fatal error! IBRION=0, but no entry for POTIM on file INCAR. MUST be
        specified!
        ----> I REFUSE TO CONTINUE WITH THIS SICK JOB ... BYE!!! <----

    catching that here, at config-load/submit time, avoids burning a queued
    SLURM job to discover it. The pre-port production INCAR
    (``calculate_assyst.py``) always set ``IBRION: 2`` explicitly; this only
    enforces what every real ASSYST campaign has already done. Deliberately no
    auto-default: injecting a physics choice into someone's INCAR silently is a
    worse failure mode than an explicit, actionable error.
    """
    if ionic_steps <= 0:
        return
    if "IBRION" not in incar:
        raise ConfigError(
            "`incar` has no IBRION set, but `assyst.ionic_steps` "
            f"({ionic_steps}) means run_ASSYST_on_structure's relaxation stages "
            "run with NSW > 0. VASP defaults an unset IBRION to 0 (molecular "
            "dynamics), which additionally requires POTIM and is never what an "
            "ASSYST relaxation wants -- VASP itself refuses the job with "
            "'Fatal error! IBRION=0, but no entry for POTIM on file INCAR. MUST "
            "be specified!'. Set `incar.IBRION: 2` (conjugate-gradient "
            "relaxation -- what ASSYST campaigns have always used)."
        )
    if int(incar["IBRION"]) == 0 and "POTIM" not in incar:
        raise ConfigError(
            "`incar.IBRION` is 0 (molecular dynamics) but `incar` has no "
            "POTIM. VASP itself refuses this: 'Fatal error! IBRION=0, but no "
            "entry for POTIM on file INCAR. MUST be specified!'. Set "
            "`incar.POTIM`, or use `incar.IBRION: 2` for a standard ASSYST "
            "relaxation."
        )


def _validate_incar_static(overrides: dict) -> None:
    """Reject ``incar_static`` entries that contradict what a statics job is.

    The accurate statics always run with ``NSW = 0`` and never carry ``ISIF``
    (``run_ASSYST_on_structure`` enforces both), so an ``NSW`` other than 0 or
    any ``ISIF`` here would be silently discarded.  Refusing is better than a
    config that reads as though the labels were relaxed.
    """
    if "ISIF" in overrides:
        raise ConfigError(
            "`incar_static.ISIF` is not allowed: the accurate statics never relax "
            "anything, and run_ASSYST_on_structure strips ISIF from their INCAR. "
            "ISIF belongs in `incar`, where it seeds the ISIF7 stage."
        )
    nsw = overrides.get("NSW")
    if nsw is not None and int(nsw) != 0:
        raise ConfigError(
            f"`incar_static.NSW` is {nsw}, but the accurate statics always run with "
            "NSW = 0 (run_ASSYST_on_structure forces it). Remove it; the relaxation "
            "step count is `assyst.ionic_steps`."
        )


def effective_static_overrides(overrides: dict | None) -> dict:
    """The tags the statics INCAR changes relative to ``incar``.

    :data:`DEFAULT_INCAR_STATIC` with ``overrides`` layered on top; a tag whose
    value is ``None`` is removed, which is how a config says "inherit this one
    from the relaxation INCAR" (``incar_static: {KSPACING: null}``).
    """
    merged = dict(DEFAULT_INCAR_STATIC)
    for key, value in (overrides or {}).items():
        key = str(key).upper()
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def _validate_moments(moments: dict) -> None:
    for symbol, value in (moments or {}).items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ConfigError(
                f"`magmom.moments.{symbol}` must be a number (initial moment in muB), "
                f"got {value!r}"
            )
    try:
        from pymatgen.core.periodic_table import Element
    except Exception:  # pragma: no cover - pymatgen always present in practice
        return
    for symbol in moments or {}:
        try:
            Element(symbol)
        except Exception:
            raise ConfigError(f"unknown element symbol in `magmom.moments`: {symbol!r}") from None


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def expand_environment(value, path: str):
    """Expand ``${VAR}`` / ``$VAR`` and a leading ``~`` in ``value``.

    Strings are expanded; lists and tuples element-wise; anything else (and a
    ``<EDIT ...>`` placeholder) is returned unchanged.  A reference to an unset
    variable raises :class:`ConfigError` naming the config path.
    """
    if isinstance(value, (list, tuple)):
        return [expand_environment(v, f"{path}[{i}]") for i, v in enumerate(value)]
    if not isinstance(value, str) or value.strip().startswith(PLACEHOLDER_PREFIX):
        return value

    def sub(match):
        name = match.group(1) or match.group(2)
        if name not in os.environ:
            raise ConfigError(
                f"`{path}` references environment variable ${{{name}}}, which is not "
                "set in this shell. Export it, or write the value out."
            )
        return os.environ[name]

    expanded = _ENV_REF.sub(sub, value)
    if expanded.startswith("~"):
        expanded = os.path.expanduser(expanded)
    return expanded


def merge_site(campaign: dict, site: dict) -> dict:
    """Deep-merge ``site`` over ``campaign``: mappings recurse, anything else is replaced.

    This is what ``--site`` does.  A site file carries what is specific to one
    machine -- ``slurm``, ``vasp``, perhaps ``root`` -- so the campaign YAML can
    stay pure physics and be submitted unchanged from several clusters.
    """
    out = dict(campaign)
    for key, value in (site or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_site(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_campaign_dict(path, sites=()) -> dict:
    """The campaign YAML as a dict, with each site YAML merged over it in order."""

    def read(p, what):
        p = Path(p)
        if not p.is_file():
            raise ConfigError(f"{what} not found: {p}")
        with p.open() as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"{what} must be a mapping, got {type(data).__name__}: {p}")
        return data

    data = read(path, "campaign config")
    for site in sites or ():
        data = merge_site(data, read(site, "site config"))
    return data


def _validate_symbols(systems) -> None:
    try:
        from pymatgen.core.periodic_table import Element
    except Exception:  # pragma: no cover - pymatgen always present in practice
        return
    for species in systems:
        if len(set(species)) != len(species):
            raise ConfigError(f"repeated element in `system.species` entry {species}")
        for sym in species:
            try:
                Element(sym)
            except Exception:
                raise ConfigError(f"unknown element symbol in `system.species`: {sym!r}") from None


# -- staleness guard ------------------------------------------------------
def recorded_config_path(root) -> Path:
    return Path(root) / RECORDED_CONFIG_NAME


def load_recorded_config(root) -> dict | None:
    path = recorded_config_path(root)
    if not path.is_file():
        return None
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def calculation_key(dumped: dict) -> dict:
    """The calculation-affecting subset of a dumped config.

    ``slurm`` is excluded: wall time, task count and partition change how the
    work reaches the scheduler, not what is computed, and forcing the
    destructive ``--from-scratch`` path after a ``--time`` bump would be worse
    than useless.
    """
    key = {k: v for k, v in dumped.items() if k not in _STALENESS_EXEMPT_SECTIONS}
    system = key.get("system")
    if isinstance(system, dict):
        key["system"] = _with_compositions(system)
    # Compare what the statics INCAR actually changes, not how it was spelled:
    # a config recorded before `incar_static` existed ran with exactly the
    # defaults, and `{KSPACING: 0.25}` written out is not a change either.
    raw = key.get("incar_static")
    key["incar_static"] = effective_static_overrides(raw if isinstance(raw, dict) else {})
    return key


def _with_compositions(system: dict) -> dict:
    """Replace ``stoich_min``/``stoich_max`` by the composition sets they imply.

    What the calculation depends on is *which compositions are scanned*, not
    the two numbers that happen to produce them.  Comparing the derived sets
    lets a config recorded by an older release (which wrote the then-default
    ``stoich_max: 12`` into every campaign, unary ones included) still match a
    config that now leaves the bound ``null``, while a genuinely different scan
    -- a binary whose ``max_atoms`` went from 12 to 20 -- is still caught, and
    is reported as ``system.compositions.<system>``.
    """
    out = {k: v for k, v in system.items() if k not in ("stoich_min", "stoich_max")}
    try:
        systems = _normalise_species(system.get("species"))
        min_atoms, max_atoms = int(system.get("min_atoms", 1)), int(system.get("max_atoms", 12))
        out["compositions"] = {
            system_name(sp): [
                list(c)
                for c in enumerate_compositions(
                    sp,
                    min_atoms,
                    max_atoms,
                    system.get("stoich_min"),
                    system.get("stoich_max"),
                )
            ]
            for sp in systems
        }
    except (ConfigError, TypeError, ValueError):
        # Malformed section: fall back to comparing the raw numbers.
        out["stoich_min"] = system.get("stoich_min")
        out["stoich_max"] = system.get("stoich_max")
    return out


def config_differences(new: dict, recorded: dict) -> list[str]:
    """Dotted paths at which the calculation-affecting configs disagree."""
    return _diff("", calculation_key(new), calculation_key(recorded))


def _diff(prefix: str, a, b) -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            sub = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_diff(sub, a.get(key, _MISSING), b.get(key, _MISSING)))
        return out
    if isinstance(b, str) and b.strip().startswith(PLACEHOLDER_PREFIX):
        return []
    # A key the recorded config predates (e.g. ``vasp.potcar_config``,
    # ``magmom.moments``) is a null/empty default, not a change.
    if isinstance(a, _Missing) and _is_empty(b) or isinstance(b, _Missing) and _is_empty(a):
        return []
    if _canon(a) != _canon(b):
        return [prefix or "<root>"]
    return []


class _Missing:
    def __repr__(self):  # pragma: no cover - debug aid
        return "<missing>"


def _is_empty(value) -> bool:
    return value is None or value == {} or value == []


_MISSING = _Missing()


def _canon(value):
    """Normalise for comparison: tuples/lists alike, ints/floats alike."""
    if isinstance(value, _Missing):
        return "<missing>"
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _canon(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def resolve_eq_volume_map(source) -> dict:
    """Build ``{symbol: equilibrium volume per atom}`` from ``eq_volume_source``.

    ``None`` or the bare bundled filename -> the packaged Materials Project
    Voronoi-volume table; an explicit mapping -> used as-is; any other string ->
    a CSV path with ``element`` and ``min_VorNN_tot_vol`` columns.
    """
    if isinstance(source, dict):
        return {str(k): float(v) for k, v in source.items()}
    if source is None:
        path = DEFAULT_EQ_VOLUME_CSV
    else:
        candidate = Path(str(source)).expanduser()
        path = candidate if candidate.is_file() else DEFAULT_EQ_VOLUME_CSV
        if not candidate.is_file() and candidate.name != DEFAULT_EQ_VOLUME_CSV.name:
            raise ConfigError(f"`system.eq_volume_source` not found: {source}")
    import pandas as pd

    frame = pd.read_csv(path)
    return {
        str(row["element"]): float(row["min_VorNN_tot_vol"])
        for _, row in frame.iterrows()
        if row.get("min_VorNN_tot_vol") == row.get("min_VorNN_tot_vol")
    }
