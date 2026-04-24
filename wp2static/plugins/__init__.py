"""Plugin adapter registry and import orchestration.

Each WordPress plugin we want to migrate gets a :class:`PluginAdapter`
subclass under this package. Adapters are discovered by **folder slug**,
matching the directory name under ``wp-content/plugins/``.

Workflow inside :func:`import_plugins`:

    1. Walk ``plugins_src`` for direct children.
    2. For each child whose name matches a registered adapter, instantiate
       the adapter and run its import (header parse, shortcode scan,
       asset copy). Unknown plugins are skipped but recorded so the
       migration report can surface them.
    3. Assets land under :meth:`Target.plugins_root` — ``static/plugins``
       on Hugo, ``assets/plugins`` on Jekyll.

In addition to asset import, adapters can opt into:

* **Post-content replacement** (``replaces_post_content`` /
  ``render_post_content``) — Elementor-style builders that store output
  in post meta rather than ``post_content``.
* **Shortcode resolution** (``owns_shortcode`` / ``render_shortcode``) —
  plugin-owned shortcodes resolved against site data.

:func:`adapter_for_post` and :func:`adapter_for_shortcode` do the
registry dispatch for the rendering pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .base import PluginAdapter, PluginMeta, RenderContext
from .elementor import ElementorAdapter
from .final_tiles import FinalTilesAdapter

if TYPE_CHECKING:
    from ..targets import Target
    from ..wpdata import Post

log = logging.getLogger(__name__)


_REGISTRY: dict[str, PluginAdapter] = {
    ElementorAdapter.slug: ElementorAdapter(),
    FinalTilesAdapter.slug: FinalTilesAdapter(),
}


def get_adapter(slug: str) -> PluginAdapter | None:
    return _REGISTRY.get(slug)


def list_adapters() -> list[str]:
    return sorted(_REGISTRY)


def iter_adapters() -> list[PluginAdapter]:
    """Registered adapter instances, in declaration order."""
    return list(_REGISTRY.values())


def adapter_for_post(post: "Post") -> PluginAdapter | None:
    """First adapter that wants to replace ``post``'s content, if any."""
    for adapter in _REGISTRY.values():
        if adapter.replaces_post_content(post):
            return adapter
    return None


def adapter_for_shortcode(name: str) -> PluginAdapter | None:
    """First adapter that owns the ``[name …]`` shortcode, if any."""
    for adapter in _REGISTRY.values():
        if adapter.owns_shortcode(name):
            return adapter
    return None


def import_plugins(
    plugins_src: Path,
    out_dir: Path,
    target: "Target",
    only: list[str] | None = None,
) -> dict:
    """Copy known plugins from ``plugins_src`` into the output tree.

    ``only`` — if given, restricts the import to those slugs. Otherwise all
    registered plugins present in ``plugins_src`` are imported.

    Returns a stats dict with one ``plugins`` entry per imported plugin.
    """
    if not plugins_src.is_dir():
        log.warning("plugins dir not found: %s", plugins_src)
        return {"imported": 0, "plugins": [], "unknown": []}

    allowed: set[str] | None = set(only) if only else None
    imported: list[dict] = []
    unknown: list[str] = []

    plugins_dst_root = target.plugins_root(out_dir)

    for plugin_dir in sorted(p for p in plugins_src.iterdir() if p.is_dir()):
        slug = plugin_dir.name
        if allowed is not None and slug not in allowed:
            continue
        adapter = get_adapter(slug)
        if adapter is None:
            unknown.append(slug)
            continue
        meta = adapter.parse_header(plugin_dir)
        meta.shortcodes = adapter.discover_shortcodes(plugin_dir)
        dst = plugins_dst_root / slug
        assets = adapter.copy_assets(plugin_dir, dst)
        log.info("imported plugin %r (%d assets)", slug, assets)
        imported.append({
            "slug": slug,
            "name": meta.name,
            "version": meta.version,
            "shortcodes": meta.shortcodes,
            "assets_copied": assets,
        })

    return {
        "imported": len(imported),
        "plugins": imported,
        "unknown": unknown,
    }


__all__ = [
    "PluginAdapter",
    "PluginMeta",
    "RenderContext",
    "ElementorAdapter",
    "FinalTilesAdapter",
    "get_adapter",
    "list_adapters",
    "iter_adapters",
    "adapter_for_post",
    "adapter_for_shortcode",
    "import_plugins",
]
