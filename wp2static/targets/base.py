"""Abstract :class:`Target` — the per-SSG surface.

A ``Target`` is the single place every SSG-specific decision lives:

* **Output layout** — where posts, pages, uploads, theme assets land.
* **Front matter** — YAML/TOML/… delimiters and dump format.
* **Site artifacts** — ``_config.yml`` / ``hugo.toml`` equivalents,
  homepage files, menu/navigation files.
* **Content directives** — shortcode-like output for galleries.
* **Theme scaffold** — how a WordPress ``.php`` template is rewritten into
  the target's template language: per-rule replacement picks, include
  syntax, empty-rendering markers, control-flow balancing, layout-path
  mapping, missing-include stubbing, theme metadata file.

The protocol is intentionally wide rather than layered — the goal at this
stage is to *eliminate* ``if target == "hugo"`` branches from the rest of
the code base. A later phase can split this surface into narrower mixins
once we understand which bits vary across three or more SSGs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..wpdata import Post, Site


class Target(ABC):
    # CLI / directory name; also the key under :mod:`wp2static.templates`.
    name: str = ""

    # --- content rendering --------------------------------------------------

    @abstractmethod
    def frontmatter(self, data: dict) -> str:
        """Serialize ``data`` as front matter with delimiters."""

    @abstractmethod
    def default_front_matter(self, layout_kind: str | None) -> str:
        """Front-matter string prepended to a generated theme template.

        Hugo templates live under ``themes/<slug>/layouts`` and take no
        front matter; Jekyll layouts need a YAML header so Liquid renders.
        """

    @abstractmethod
    def gallery_directive(self, images: list[str], title: str = "") -> str:
        """Render a gallery shortcode for an in-body content block."""

    # --- output layout ------------------------------------------------------

    @abstractmethod
    def post_output_path(self, out_dir: Path, post: "Post", ext: str) -> Path:
        """Destination for a single post/page file."""

    @abstractmethod
    def uploads_dest(self, out_dir: Path) -> Path:
        """Directory under ``out_dir`` that the uploads tree is copied into."""

    @abstractmethod
    def theme_root(self, out_dir: Path, slug: str) -> Path:
        """Where the scaffolded theme sits within ``out_dir``.

        Hugo: ``<out_dir>/themes/<slug>``. Jekyll: ``<out_dir>`` itself.
        """

    @abstractmethod
    def theme_static_root(self, out_dir: Path, slug: str) -> Path:
        """Where static assets (css/js/images) from the source theme land."""

    @abstractmethod
    def plugins_root(self, out_dir: Path) -> Path:
        """Directory under which plugin static assets are imported.

        Hugo: ``<out_dir>/static/plugins`` — Hugo merges project-level
        ``static/`` with each theme's ``static/``, so plugin files become
        reachable as ``/plugins/<slug>/...`` without registering a theme.
        Jekyll: ``<out_dir>/assets/plugins``.
        """

    # --- top-level artifacts ------------------------------------------------

    @abstractmethod
    def write_index(
        self, out_dir: Path, site: "Site",
        front_page: "Post | None", front_body: str, markdown: bool,
    ) -> int:
        """Write the homepage (and any section-index siblings). Returns 0/1."""

    @abstractmethod
    def write_site_config(
        self, out_dir: Path, site: "Site", base_url: str,
    ) -> int:
        """Write ``hugo.toml`` / ``_config.yml`` if absent. Returns 0/1."""

    @abstractmethod
    def write_menus(self, out_dir: Path, site: "Site") -> int:
        """Write nav-menu definitions if absent. Returns 0/1."""

    # --- php transpile / theme scaffold ------------------------------------

    @abstractmethod
    def replacement_for(self, rule: Any) -> str:
        """Pick ``rule.replace_hugo`` / ``rule.replace_jekyll``.

        Duck-typed so the rule registry doesn't need to import this module.
        """

    @abstractmethod
    def include_directive(self, slug: str) -> str:
        """Render ``{% include foo.html %}`` / ``{{ partial `foo.html` . }}``."""

    @abstractmethod
    def marker(self, body: str, kind: str) -> str:
        """Render an empty-expanding marker in the SSG's comment syntax.

        Must be safe inside every HTML context — including attribute values
        — so the unmapped-PHP / dropped-orphan insertion points don't
        perturb the target's own parser.
        """

    @abstractmethod
    def balance_control_flow(self, text: str) -> str:
        """Turn unpaired ``{% endif %}`` / ``{{ end }}`` into markers."""

    @abstractmethod
    def layout_for(self, slug: str, php_path: Path) -> Path | None:
        """Map a source-theme ``.php`` path to its destination file, or
        ``None`` if the file should be skipped entirely (e.g. functions.php).
        """

    @abstractmethod
    def emit_theme_metadata(
        self, theme_root: Path, slug: str, meta: Any,
    ) -> None:
        """Write ``theme.toml`` / ``theme.yml`` for the scaffolded theme."""

    @abstractmethod
    def stub_missing_includes(self, out_dir: Path, slug: str) -> int:
        """Create empty stubs for include paths referenced but not emitted.

        Returns the number of stubs written.
        """

    def finalize_theme(self, out_dir: Path, slug: str) -> dict:
        """Run target-specific cleanup after ``.php`` → layout transpile.

        Default: no-op. Hugo uses this to emit a ``head`` partial listing
        the CSS files copied from the WP theme, so the fallback layouts
        don't render unstyled when the primary theme's transpiled
        templates don't pull in the site stylesheet.
        """
        return {}
