"""Internal helpers shared between target implementations."""

from __future__ import annotations

import yaml


def dump_yaml(data: dict) -> str:
    return yaml.safe_dump(
        data, default_flow_style=False, allow_unicode=True, sort_keys=False,
    )


def toml_value(v) -> str:
    """Render a single TOML scalar / list.

    Enough for flat front matter with string/list/int/bool values. Nothing
    here knows about nested tables — callers that need nesting emit the
    ``[table]`` header themselves.
    """
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def dump_toml_flat(data: dict) -> str:
    """Dump a flat dict of key=value TOML entries (one per line)."""
    lines = [f"{k} = {toml_value(v)}" for k, v in data.items()]
    return "\n".join(lines) + "\n"


def toml_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')
