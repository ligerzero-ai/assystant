"""assystant -- run an ASSYST campaign end to end from one YAML file.

``gen`` generates PyXtal structures, ``run`` submits one SLURM job per
structure, ``status`` reports progress, ``collect`` concatenates the results
into a single training dataset.
"""

from .config import (
    CampaignConfig,
    ConfigError,
    StaleConfigError,
    system_name,
)
from .generate import (
    enumerate_compositions,
    parse_struct_dir,
    poscar_hash,
    struct_dir_name,
)
from .incar import get_incar_magmom_string, stack_element_string
from .submit import RESULT_FILENAME

__version__ = "0.1.1"

__all__ = [
    "CampaignConfig",
    "ConfigError",
    "StaleConfigError",
    "system_name",
    "enumerate_compositions",
    "parse_struct_dir",
    "poscar_hash",
    "struct_dir_name",
    "get_incar_magmom_string",
    "stack_element_string",
    "RESULT_FILENAME",
    "__version__",
]
