"""Abstract :class:`PluginAdapter` — per-plugin identity and render hooks.

A ``PluginAdapter`` is the single place a plugin's migration knowledge
lives:

* **Identification** — which folder slug it recognises, which shortcodes
  it owns, and how to parse the plugin header.
* **Asset import** — copy the plugin's static tree into the output.
* **Content replacement** — Elementor-style: some plugins store the
  rendered page in post meta rather than ``post_content``. The adapter
  opts in with :meth:`replaces_post_content` and returns the HTML from
  :meth:`render_post_content`.
* **Shortcode resolution** — FinalTiles-style: plugin-owned shortcodes
  (``[FinalTilesGallery …]``) resolve against site data (attachments,
  gallery tables) and render a target-specific directive. The adapter
  claims the shortcode name via :meth:`owns_shortcode` and returns HTML
  from :meth:`render_shortcode`.

Default implementations are all no-ops so a bare adapter with just a
``slug`` is enough to opt a plugin into the asset-import pipeline.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..targets import Target
    from ..wpdata import Attachment, Gallery, Post

log = logging.getLogger(__name__)


_ASSET_DIRS = ("assets", "css", "js", "images", "img", "fonts", "svg")

# Plugin header fields are stored in a ``/* … */`` block at the top of the
# plugin's main PHP file, in the same shape as theme ``style.css`` headers.
_HEADER_FIELDS = {
    "plugin name": "name",
    "plugin uri": "uri",
    "description": "description",
    "version": "version",
    "author": "author",
    "author uri": "author_uri",
    "license": "license",
    "text domain": "text_domain",
}

_SHORTCODE_RE = re.compile(
    r"""add_shortcode\s*\(\s*['"]([a-zA-Z0-9_\-]+)['"]""",
)


@dataclass
class PluginMeta:
    slug: str = ""
    name: str = ""
    uri: str = ""
    description: str = ""
    version: str = ""
    author: str = ""
    author_uri: str = ""
    license: str = ""
    text_domain: str = ""
    shortcodes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RenderContext:
    """Everything an adapter needs to resolve a shortcode against site data.

    Passed to :meth:`PluginAdapter.render_shortcode`. Fields mirror the
    args the old ``convert.resolve_galleries`` accepted; bundling them here
    keeps the adapter API narrow as new plugins land.
    """
    attachments: "dict[int, Attachment]"
    galleries_by_id: "dict[int, Gallery]"
    galleries_by_slug: "dict[str, Gallery]"
    base_url: str
    uploads_prefix: str
    target: "Target"


class PluginAdapter:
    """Base adapter. Default behaviour is generic asset copy + header parse.

    Subclasses override ``slug`` and any of the render hooks they need.
    """

    slug: str = ""

    # Shortcodes owned by this plugin, for MIGRATION.md. Subclasses declare
    # these statically; the importer also regex-scans the plugin's PHP so
    # undeclared shortcodes still get noted.
    shortcodes: tuple[str, ...] = ()

    def parse_header(self, plugin_src: Path) -> PluginMeta:
        """Extract header metadata from the plugin's main PHP file."""
        main_php = _find_main_php(plugin_src, self.slug)
        meta = PluginMeta(slug=self.slug)
        if main_php is None:
            return meta
        text = main_php.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"/\*(.*?)\*/", text, re.DOTALL)
        if not m:
            return meta
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            attr = _HEADER_FIELDS.get(key.strip().lower())
            if attr:
                setattr(meta, attr, val.strip())
        return meta

    def discover_shortcodes(self, plugin_src: Path) -> list[str]:
        """Scan PHP files for ``add_shortcode`` registrations."""
        found: set[str] = set(self.shortcodes)
        for php in plugin_src.rglob("*.php"):
            try:
                text = php.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in _SHORTCODE_RE.finditer(text):
                found.add(m.group(1))
        return sorted(found)

    def copy_assets(self, plugin_src: Path, plugin_dst: Path) -> int:
        """Copy the plugin's static asset tree. Returns file count."""
        total = 0
        for name in _ASSET_DIRS:
            candidate = plugin_src / name
            if not candidate.is_dir():
                continue
            for file in candidate.rglob("*"):
                if not file.is_file():
                    continue
                rel = file.relative_to(plugin_src)
                out = plugin_dst / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, out)
                total += 1
        return total

    # --- content replacement (Elementor-style) ---------------------------

    def replaces_post_content(self, post: "Post") -> bool:
        """Return True if this adapter wants to render ``post`` from plugin
        data (post meta / CPTs) instead of the post's ``post_content``.
        """
        return False

    def render_post_content(self, post: "Post") -> str:
        """Return rendered HTML for a post this adapter owns. Called only
        when :meth:`replaces_post_content` returned ``True``."""
        return ""

    # --- shortcode resolution (FinalTiles-style) -------------------------

    def owns_shortcode(self, name: str) -> bool:
        """Return True if this adapter renders the ``[name ...]`` shortcode."""
        return False

    def render_shortcode(
        self, name: str, attrs: dict[str, str], ctx: RenderContext,
    ) -> str:
        """Render a shortcode this adapter claimed via ``owns_shortcode``."""
        return ""


def _find_main_php(plugin_src: Path, slug: str) -> Path | None:
    """Return the plugin's main PHP file — the one carrying the header.

    WordPress convention: ``<slug>/<slug>.php``. Fall back to any top-level
    ``.php`` whose comment block contains a ``Plugin Name`` field.
    """
    canonical = plugin_src / f"{slug}.php"
    if canonical.is_file():
        return canonical
    for php in sorted(plugin_src.glob("*.php")):
        text = php.read_text(encoding="utf-8", errors="replace")[:2000]
        if re.search(r"plugin name\s*:", text, re.IGNORECASE):
            return php
    return None
