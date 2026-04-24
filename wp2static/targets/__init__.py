"""Target registry — one module per supported SSG.

Each :class:`Target` owns its file layout, front-matter format, template
language, URL scheme, PHP-rule replacements, and theme-scaffold rules.
The rest of ``wp2static`` iterates the neutral :class:`Site` / :class:`Post`
dataclasses and delegates every target-specific decision to this object.

Adding a new SSG is, in principle, one new module under this package plus
one entry in :data:`_TARGETS`.
"""

from __future__ import annotations

from .base import Target
from .hugo import HugoTarget
from .jekyll import JekyllTarget

_TARGETS: dict[str, Target] = {
    "jekyll": JekyllTarget(),
    "hugo": HugoTarget(),
}


def get_target(name: str | Target) -> Target:
    """Return the registered :class:`Target` for ``name``.

    Accepts a :class:`Target` instance too so callers can pass either a
    string (CLI / options) or an already-resolved target interchangeably.
    """
    if isinstance(name, Target):
        return name
    try:
        return _TARGETS[name]
    except KeyError as exc:
        known = ", ".join(sorted(_TARGETS))
        raise ValueError(f"unknown target {name!r}; known: {known}") from exc


def list_targets() -> list[str]:
    return sorted(_TARGETS)


__all__ = ["Target", "HugoTarget", "JekyllTarget", "get_target", "list_targets"]
