"""INCAR assembly, including the MAGMOM string.

``stack_element_string`` and ``get_incar_magmom_string`` are ports of
``stackElementString`` / ``get_INCAR_magmom_string`` from
``/u/hmai/2024_12_05_DFTPyxtalAssystNodes/calculate_assyst.py``.
"""

from __future__ import annotations

__all__ = [
    "stack_element_string",
    "get_incar_magmom_string",
    "build_incar",
]


def stack_element_string(structure):
    """Run-length encode a structure's site order.

    Returns ``(element_list, element_count)``, e.g. ``Fe Fe C Fe`` ->
    ``(["Fe", "C", "Fe"], [2, 1, 1])``.  The *order* matters: MAGMOM is
    positional, so consecutive runs must be preserved rather than the species
    totals aggregated.
    """
    elements = [site.species_string for site in structure]
    grouped = []
    previous = None
    count = 0
    for element in elements + [None]:
        if element == previous:
            count += 1
        else:
            if previous is not None:
                grouped.append((previous, count))
            count = 1
            previous = element
    if not grouped:
        return [], []
    element_list, element_count = zip(*grouped)
    return list(element_list), list(element_count)


def get_incar_magmom_string(structure, host_ele="Fe", host_magmom=3, solute_magmom=-0.5):
    """VASP MAGMOM string: ``host_magmom`` on the host, ``solute_magmom`` elsewhere."""
    element_list, element_count = stack_element_string(structure)
    parts = []
    for element, count in zip(element_list, element_count):
        moment = host_magmom if element == host_ele else solute_magmom
        parts.append(f"{count}*{moment}")
    return " ".join(parts)


def build_incar(base_tags: dict, structure, magmom_cfg, system_tag: str, host: str):
    """Assemble the campaign INCAR for one structure.

    ``host`` is the already-resolved host element (``magmom.host``, or the
    system's first species when that is ``null``).  ``SYSTEM`` is stamped with
    the run-directory name so the tag identifies the job in the OUTCAR.
    ``MAGMOM`` is written only when ``ISPIN == 2``: with ``ISPIN=1`` it is inert
    for VASP, and writing it anyway (as the pre-2026 scripts did) makes a
    nonmagnetic INCAR read as though it were magnetic.
    """
    from pymatgen.io.vasp.inputs import Incar

    incar = Incar.from_dict(dict(base_tags))
    incar["SYSTEM"] = system_tag
    if int(incar.get("ISPIN", 1)) == 2:
        incar["MAGMOM"] = get_incar_magmom_string(
            structure,
            host_ele=host,
            host_magmom=magmom_cfg.host_moment,
            solute_magmom=magmom_cfg.solute_moment,
        )
    else:
        incar.pop("MAGMOM", None)
    return incar
