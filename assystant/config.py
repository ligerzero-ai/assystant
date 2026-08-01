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
    "recorded_config_path",
    "load_recorded_config",
    "calculation_key",
    "config_differences",
]

#: Name of the resolved-config file written into the campaign root by ``run``.
RECORDED_CONFIG_NAME = "assystant_config.yaml"

#: Sections excluded from the staleness comparison.  ``slurm`` describes how the
#: work reaches the scheduler (wall time, task count, partition), not what is
#: calculated, so changing it must not force the destructive ``--from-scratch``
#: path.  Everything else -- system, incar, magmom, assyst, filters, vasp -- can
#: change the numbers and therefore invalidates an existing run directory.
_STALENESS_EXEMPT_SECTIONS = ("slurm",)

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
    stoich_min: int = 1
    stoich_max: int = 12
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
        raise ConfigError(
            f"`system.spacegroups` must be a [min, max] pair, got {raw!r}"
        ) from None
    return [int(lo), int(hi)]


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

    def host_for(self, species: list[str]) -> str:
        return self.host if self.host is not None else species[0]


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


@dataclass
class CampaignConfig:
    root: str = ""
    system: SystemConfig = field(default_factory=SystemConfig)
    incar: dict = field(default_factory=dict)
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
        return cls(
            root=str(data.get("root", "") or ""),
            system=_from_section(SystemConfig, "system", data.get("system")),
            incar={str(k).upper(): v for k, v in incar.items()},
            magmom=_from_section(MagmomConfig, "magmom", data.get("magmom")),
            assyst=_from_section(AssystConfig, "assyst", data.get("assyst")),
            filters=_from_section(FiltersConfig, "filters", data.get("filters")),
            slurm=_from_section(SlurmConfig, "slurm", data.get("slurm")),
            vasp=_from_section(VaspConfig, "vasp", data.get("vasp")),
        )

    @classmethod
    def from_yaml(cls, path) -> "CampaignConfig":
        path = Path(path)
        if not path.is_file():
            raise ConfigError(f"campaign config not found: {path}")
        with path.open() as fh:
            return cls.from_dict(yaml.safe_load(fh) or {})

    # -- overrides and validation ----------------------------------------
    def resolve(self, overrides: dict | None = None) -> "CampaignConfig":
        """Return a new, validated config with dotted-path ``overrides`` applied.

        Precedence is CLI flag > YAML > dataclass default, which falls out of
        applying the overrides on top of the parsed YAML.  ``root`` is made
        absolute so every downstream path is absolute.
        """
        out = copy.deepcopy(self)
        for dotted, value in (overrides or {}).items():
            if value is None:
                continue
            out._set_dotted(dotted, value)
        if out.root:
            out.root = str(Path(out.root).expanduser().resolve())
        out.validate()
        return out

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
        if s.stoich_min < 1 or s.stoich_max < s.stoich_min:
            raise ConfigError(
                f"`system.stoich_min`/`stoich_max` must satisfy 1 <= min <= max, "
                f"got {s.stoich_min}/{s.stoich_max}"
            )
        if not (1 <= s.sg_min <= s.sg_max <= 230):
            raise ConfigError(
                f"`system.spacegroups` must satisfy 1 <= min <= max <= 230, "
                f"got {s.spacegroups}"
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

        return {
            "root": self.root,
            "system": sect(self.system),
            "incar": dict(self.incar),
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
    return {k: v for k, v in dumped.items() if k not in _STALENESS_EXEMPT_SECTIONS}


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
    if _canon(a) != _canon(b):
        return [prefix or "<root>"]
    return []


class _Missing:
    def __repr__(self):  # pragma: no cover - debug aid
        return "<missing>"


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
