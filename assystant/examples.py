"""Bundled campaign templates: list them, read them, write them out.

The templates ship inside the wheel (``assystant/templates/*.yaml``) and are
located with :mod:`importlib.resources`, so ``assystant example`` works from a
plain ``pip install`` with no repository checkout.  Unlike the cluster configs
in the repository's ``examples/`` directory they carry no site-specific paths:
``gen`` and ``run --dry-run`` work as shipped, and the values only a real
``run`` needs are ``"<EDIT ...>"`` placeholders that :func:`placeholder_fields`
detects.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

__all__ = [
    "list_templates",
    "template_description",
    "template_text",
    "write_template",
    "placeholder_fields",
    "PLACEHOLDER_PREFIX",
]

#: Marker that opens every value a template leaves for the user to fill in.
PLACEHOLDER_PREFIX = "<EDIT"


def _templates():
    return files("assystant") / "templates"


def list_templates() -> list[str]:
    """Names of the bundled templates, without the ``.yaml`` suffix."""
    return sorted(
        entry.name[: -len(".yaml")]
        for entry in _templates().iterdir()
        if entry.name.endswith(".yaml")
    )


def _resolve_name(name: str) -> str:
    stem = name[: -len(".yaml")] if name.endswith(".yaml") else name
    available = list_templates()
    if stem not in available:
        raise KeyError(
            f"no bundled template named {name!r}; available: {', '.join(available)}"
        )
    return stem


def template_text(name: str) -> str:
    """The YAML text of bundled template ``name`` (``.yaml`` suffix optional)."""
    stem = _resolve_name(name)
    return (_templates() / f"{stem}.yaml").read_text(encoding="utf-8")


def template_description(name: str) -> str:
    """The first comment line of a template, used as its one-line summary."""
    for line in template_text(name).splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def write_template(name: str, dest, *, force: bool = False) -> Path:
    """Write bundled template ``name`` to ``dest``; refuse to overwrite unless ``force``."""
    dest = Path(dest)
    if dest.exists() and not force:
        raise FileExistsError(f"{dest} already exists (pass --force to overwrite)")
    text = template_text(name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


def placeholder_fields(cfg) -> list[str]:
    """Dotted paths of config values still holding an ``"<EDIT ...>"`` placeholder."""

    def walk(prefix, value):
        if isinstance(value, str):
            if value.strip().startswith(PLACEHOLDER_PREFIX):
                yield prefix
        elif isinstance(value, dict):
            for key, sub in value.items():
                yield from walk(f"{prefix}.{key}" if prefix else str(key), sub)
        elif isinstance(value, (list, tuple)):
            for i, sub in enumerate(value):
                yield from walk(f"{prefix}[{i}]", sub)

    return list(walk("", cfg.dump()))
