"""Hugo target: Go html/template, TOML front matter, ``hugo.toml``."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._utils import dump_toml_flat, toml_escape
from .base import Target

if TYPE_CHECKING:
    from ..wpdata import NavMenu, NavMenuItem, Post, Site


_TAG_RE = re.compile(r"\{\{-?\s*(\w+)\b[^}]*-?\}\}")
_OPENS = {"if", "with", "range", "block", "define"}


# Top-level PHP templates that use the WordPress Loop
# (``if ( have_posts() ) : while ( have_posts() ) : the_post();``).
# The Loop has no equivalent in Hugo — there's no global iterator, pages
# are individual content files with ``.Content``. Transpiled versions
# produce empty output and, worse, *shadow* the fallback theme's working
# layouts. We skip them entirely and rely on ``wp2static-defaults`` (or a
# hand-written replacement) for these kinds. Header/footer/sidebar still
# transpile fine — they're structural, not loop-carrying.
_WP_LOOP_TEMPLATES = frozenset({
    "index.php", "single.php", "page.php",
    "archive.php", "category.php", "tag.php", "search.php",
})

_TOP_LEVEL_LAYOUTS = {
    "404.php": "layouts/404.html",
}

# Hugo partial calls use either double-quoted or backtick-quoted string
# literals for the partial path — ``{{ partial "foo.html" . }}`` or
# ``{{ partial `foo.html` . }}``. Match both so the stubber sees every
# referenced partial regardless of quoting style.
_PARTIAL_RE = re.compile(r'\{\{-?\s*partial\s+(?:"([^"]+)"|`([^`]+)`)')


class HugoTarget(Target):
    name = "hugo"

    # --- content rendering -------------------------------------------------

    def frontmatter(self, data: dict) -> str:
        return "+++\n" + dump_toml_flat(data) + "+++\n\n"

    def default_front_matter(self, layout_kind: str | None) -> str:
        # Hugo layouts don't take front matter.
        return ""

    def gallery_directive(self, images: list[str], title: str = "") -> str:
        images = [i for i in images if i]
        if not images:
            return ""
        joined = ",".join(images)
        esc_title = title.replace('"', "&quot;")
        if title:
            body = (
                f'{{{{< gallery images="{joined}" '
                f'title="{esc_title}" >}}}}'
            )
        else:
            body = f'{{{{< gallery images="{joined}" >}}}}'
        return f"\n\n{body}\n\n"

    # --- output layout -----------------------------------------------------

    def post_output_path(self, out_dir: Path, post: "Post", ext: str) -> Path:
        slug = post.slug or f"post-{post.post_id}"
        subdir = "posts" if post.post_type == "post" else ""
        return out_dir / "content" / subdir / f"{slug}{ext}"

    def uploads_dest(self, out_dir: Path) -> Path:
        return out_dir / "static" / "uploads"

    def theme_root(self, out_dir: Path, slug: str) -> Path:
        return out_dir / "themes" / slug

    def theme_static_root(self, out_dir: Path, slug: str) -> Path:
        return self.theme_root(out_dir, slug) / "static"

    def plugins_root(self, out_dir: Path) -> Path:
        return out_dir / "static" / "plugins"

    # --- top-level artifacts ----------------------------------------------

    def write_index(
        self, out_dir: Path, site: "Site",
        front_page: "Post | None", front_body: str, markdown: bool,
    ) -> int:
        title = front_page.title if front_page else (site.site_name or "Home")
        fm: dict = {"title": title}
        if front_page and front_page.date:
            fm["date"] = front_page.date.isoformat(sep=" ")
        if site.site_description and not front_page:
            fm["description"] = site.site_description
        body = front_body if front_page else ""
        # Match file extension to body format so Hugo's markdown renderer
        # doesn't accidentally process HTML content.
        index_ext = ".md" if markdown else ".html"
        main = out_dir / "content" / f"_index{index_ext}"
        main.parent.mkdir(parents=True, exist_ok=True)
        main.write_text(self.frontmatter(fm) + body + "\n", encoding="utf-8")
        # Section index for posts — lets Hugo render /posts/ via list.html.
        section = out_dir / "content" / "posts" / f"_index{index_ext}"
        section.parent.mkdir(parents=True, exist_ok=True)
        section.write_text(
            self.frontmatter({"title": "Posts"}) + "\n", encoding="utf-8",
        )
        return 1

    def write_site_config(
        self, out_dir: Path, site: "Site", base_url: str,
    ) -> int:
        cfg = out_dir / "hugo.toml"
        if cfg.exists():
            return 0
        title = site.site_name or "Site"
        primary = site.active_theme or ""
        description = site.site_description or ""
        # Hugo's `theme` key accepts an array — entries are tried in order,
        # so anything missing from the primary WordPress-migrated theme
        # falls through to wp2static-defaults (installed as a template).
        themes = [primary] if primary else []
        themes.append("wp2static-defaults")
        theme_list = ", ".join(f'"{toml_escape(t)}"' for t in themes)
        lines = [
            f'baseURL = "{base_url}/"' if base_url else 'baseURL = "/"',
            f'title = "{toml_escape(title)}"',
            'languageCode = "en-us"',
            f'theme = [{theme_list}]',
            "",
            # WP content carries raw HTML (figures, galleries, plugin
            # widgets) that must survive Markdown rendering — goldmark's
            # default is to strip it.
            "[markup.goldmark.renderer]",
            "  unsafe = true",
        ]
        if description:
            lines.append("[params]")
            lines.append(f'  description = "{toml_escape(description)}"')
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 1

    def write_menus(self, out_dir: Path, site: "Site") -> int:
        if not site.menus:
            return 0
        # File name is the Hugo config key. The singular `menu` key is the
        # classic map-of-name-to-entries shape. The plural `menus` key exists
        # too but expects a *flat* list where each entry carries its own menu
        # name — a different data model that we don't use.
        cfg = out_dir / "config" / "_default" / "menu.toml"
        if cfg.exists():
            return 0
        lines: list[str] = []
        for menu in site.menus:
            name = _menu_key(menu, site)
            lines.append(f"# WordPress menu: {menu.name} (slug: {menu.slug})")
            _emit_menu_entries(name, menu.items, "", 10, lines)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return 1

    # --- php transpile / theme scaffold -----------------------------------

    def replacement_for(self, rule: Any) -> str:
        return rule.replace_hugo

    def include_directive(self, slug: str) -> str:
        slug = slug.replace("\\", "/").strip("/")
        # Backtick-quoted so the partial call is safe even if it lands
        # inside an HTML ``"…"`` attribute value.
        return "{{ partial `" + slug + ".html` . }}"

    def marker(self, body: str, kind: str) -> str:
        body = (body.replace("{", " ").replace("}", " ")
                    .replace("*/", " ").replace("%}", " "))
        body = " ".join(body.split())
        return "{{/* wp2static " + kind + ": " + body + " */}}"

    def balance_control_flow(self, text: str) -> str:
        matches = list(_TAG_RE.finditer(text))
        if not matches:
            return text
        stack: list[int] = []
        drop: set[int] = set()
        for i, m in enumerate(matches):
            tag = m.group(1)
            if tag in _OPENS:
                stack.append(i)
            elif tag == "end":
                if stack:
                    stack.pop()
                else:
                    drop.add(i)
            elif tag == "else" and not stack:
                drop.add(i)
        drop.update(stack)
        if not drop:
            return text
        return _rewrite_matches(self, text, matches, drop)

    def layout_for(self, slug: str, php_path: Path) -> Path | None:
        name = php_path.name
        if name == "functions.php":
            return None
        # WP-Loop-carrying templates don't transpile — skip them and let
        # the wp2static-defaults fallback theme own those kinds.
        if len(php_path.parts) == 1 and name in _WP_LOOP_TEMPLATES:
            return None
        stem = php_path.stem
        theme_root = Path("themes") / slug
        if len(php_path.parts) == 1 and name in _TOP_LEVEL_LAYOUTS:
            return theme_root / _TOP_LEVEL_LAYOUTS[name]
        if len(php_path.parts) == 1 and (
            stem in ("header", "footer") or stem.startswith("sidebar")
        ):
            return theme_root / "layouts" / "partials" / f"{stem}.html"
        return theme_root / "layouts" / "partials" / php_path.with_suffix(".html")

    def emit_theme_metadata(
        self, theme_root: Path, slug: str, meta: Any,
    ) -> None:
        theme_toml = theme_root / "theme.toml"
        theme_toml.parent.mkdir(parents=True, exist_ok=True)
        tags_list = ", ".join('"' + toml_escape(t) + '"' for t in meta.tags)
        lines = [
            f'name = "{toml_escape(meta.name or slug)}"',
            f'license = "{toml_escape(meta.license) or "unknown"}"',
            'licenselink = ""',
            f'description = "{toml_escape(meta.description)}"',
            f'homepage = "{toml_escape(meta.uri)}"',
            f'tags = [{tags_list}]',
            'features = []',
            'min_version = "0.80.0"',
            '[author]',
            f'  name = "{toml_escape(meta.author)}"',
            f'  homepage = "{toml_escape(meta.author_uri)}"',
        ]
        theme_toml.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def finalize_theme(self, out_dir: Path, slug: str) -> dict:
        """Emit a ``wp2static-head`` partial listing theme + plugin CSS.

        Walks the primary theme's static tree and the project-level
        ``static/plugins/`` tree (populated earlier by the plugin import
        step) for ``*.css`` files and writes one ``<link rel="stylesheet">``
        per file. The partial lives inside the primary theme, so it wins
        the theme-array lookup over the empty default shipped with
        ``wp2static-defaults``.

        Plugin stylesheets come first so the theme's CSS still wins
        cascading conflicts — WordPress enqueues theme styles after plugin
        styles by default.
        """
        links: list[str] = []
        for rel in _walk_css(self.plugins_root(out_dir)):
            links.append(f'<link rel="stylesheet" href="/plugins/{rel}">')
        static_root = self.theme_static_root(out_dir, slug)
        for rel in _walk_css(static_root):
            links.append(f'<link rel="stylesheet" href="/{rel}">')
        if not links:
            return {"head_css_links": 0}
        partial = (
            out_dir / "themes" / slug / "layouts" / "partials"
            / "wp2static-head.html"
        )
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text("\n".join(links) + "\n", encoding="utf-8")
        return {"head_css_links": len(links)}

    def stub_missing_includes(self, out_dir: Path, slug: str) -> int:
        walk_root = out_dir / "themes" / slug / "layouts"
        include_root = walk_root / "partials"
        if not walk_root.is_dir():
            return 0
        refs: set[str] = set()
        for p in walk_root.rglob("*.html"):
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in _PARTIAL_RE.finditer(text):
                ref = next(g for g in m.groups() if g is not None)
                refs.add(ref.strip().strip('"\'`'))
        stubs = 0
        for ref in refs:
            dst = include_root / ref
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            body = f"{{{{/* wp2static stub for missing include {ref} */}}}}\n"
            dst.write_text(body, encoding="utf-8")
            stubs += 1
        return stubs


def _walk_css(root: Path) -> list[str]:
    """Yield POSIX-style relative paths of ``*.css`` files under ``root``."""
    if not root.is_dir():
        return []
    return [
        css.relative_to(root).as_posix()
        for css in sorted(root.rglob("*.css"))
    ]


def _rewrite_matches(
    target: Target, text: str, matches: list[re.Match], drop: set[int],
) -> str:
    out: list[str] = []
    last = 0
    for i, m in enumerate(matches):
        out.append(text[last:m.start()])
        out.append(
            target.marker(m.group(1), "dropped orphan")
            if i in drop else m.group(0)
        )
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def _menu_key(menu: "NavMenu", site: "Site") -> str:
    for slot, slug in site.menu_locations.items():
        if slug == menu.slug:
            return slot
    return menu.slug


def _emit_menu_entries(
    menu_name: str, items: list["NavMenuItem"], parent_id: str,
    weight: int, lines: list[str],
) -> None:
    for item in items:
        identifier = f"{menu_name}-{item.item_id}"
        lines.append(f"[[{menu_name}]]")
        lines.append(f'  name = "{toml_escape(item.label)}"')
        lines.append(f'  url = "{toml_escape(item.url)}"')
        lines.append(f'  identifier = "{identifier}"')
        lines.append(f"  weight = {weight}")
        if parent_id:
            lines.append(f'  parent = "{parent_id}"')
        lines.append("")
        weight += 10
        if item.children:
            _emit_menu_entries(menu_name, item.children, identifier, 10, lines)
