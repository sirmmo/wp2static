"""Jekyll target: Liquid templates, YAML front matter, ``_config.yml``."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._utils import dump_yaml, toml_escape
from .base import Target

if TYPE_CHECKING:
    from ..wpdata import Post, Site


# --- control-flow balancing primitives --------------------------------------

_TAG_RE = re.compile(r"\{%-?\s*(\w+)\b[^%]*-?%\}")
_OPENS = {"if", "for", "unless", "case", "capture", "tablerow",
          "comment", "raw"}
_CLOSE_FOR = {
    "endif": "if", "endfor": "for", "endunless": "unless",
    "endcase": "case", "endcapture": "capture", "endtablerow": "tablerow",
    "endcomment": "comment", "endraw": "raw",
}
_CONTINUATIONS = {"else", "elsif", "when"}


_TOP_LEVEL_LAYOUTS = {
    "index.php": "_layouts/home.html",
    "single.php": "_layouts/post.html",
    "page.php": "_layouts/page.html",
    "archive.php": "_layouts/archive.html",
    "category.php": "_layouts/category.html",
    "tag.php": "_layouts/tag.html",
    "search.php": "_layouts/search.html",
    "404.php": "_layouts/404.html",
}

# `{% include foo.html %}` / `{% include foo/bar.html %}`. The path segment
# ends at the first whitespace or `%`. Liquid tolerates a trailing dash.
_INCLUDE_RE = re.compile(r"\{%-?\s*include\s+([^\s%}]+)")


class JekyllTarget(Target):
    name = "jekyll"

    # --- content rendering -------------------------------------------------

    def frontmatter(self, data: dict) -> str:
        return "---\n" + dump_yaml(data) + "---\n\n"

    def default_front_matter(self, layout_kind: str | None) -> str:
        if not layout_kind:
            return ""
        return f"---\nlayout: {layout_kind}\n---\n"

    def gallery_directive(self, images: list[str], title: str = "") -> str:
        images = [i for i in images if i]
        if not images:
            return ""
        joined = ",".join(images)
        esc_title = title.replace('"', "&quot;")
        if title:
            body = (
                f'{{% include gallery.html images="{joined}" '
                f'title="{esc_title}" %}}'
            )
        else:
            body = f'{{% include gallery.html images="{joined}" %}}'
        return f"\n\n{body}\n\n"

    # --- output layout -----------------------------------------------------

    def post_output_path(self, out_dir: Path, post: "Post", ext: str) -> Path:
        slug = post.slug or f"post-{post.post_id}"
        if post.post_type == "post":
            datestr = post.date.strftime("%Y-%m-%d")
            return out_dir / "_posts" / f"{datestr}-{slug}{ext}"
        return out_dir / f"{slug}{ext}"

    def uploads_dest(self, out_dir: Path) -> Path:
        return out_dir / "assets" / "uploads"

    def theme_root(self, out_dir: Path, slug: str) -> Path:
        return out_dir

    def theme_static_root(self, out_dir: Path, slug: str) -> Path:
        return out_dir / "assets" / "theme"

    def plugins_root(self, out_dir: Path) -> Path:
        return out_dir / "assets" / "plugins"

    # --- top-level artifacts ----------------------------------------------

    def write_index(
        self, out_dir: Path, site: "Site",
        front_page: "Post | None", front_body: str, markdown: bool,
    ) -> int:
        title = front_page.title if front_page else (site.site_name or "Home")
        fm: dict = {"layout": "home", "title": title}
        if site.site_description and not front_page:
            fm["description"] = site.site_description
        body = front_body if front_page else ""
        path = out_dir / "index.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.frontmatter(fm) + body + "\n", encoding="utf-8")
        return 1

    def write_site_config(
        self, out_dir: Path, site: "Site", base_url: str,
    ) -> int:
        cfg = out_dir / "_config.yml"
        if cfg.exists():
            return 0
        data = {
            "title": site.site_name or "Site",
            "description": site.site_description or "",
            "url": base_url,
        }
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(dump_yaml(data), encoding="utf-8")
        return 1

    def write_menus(self, out_dir: Path, site: "Site") -> int:
        if not site.menus:
            return 0
        data_file = out_dir / "_data" / "navigation.yml"
        if data_file.exists():
            return 0
        data: dict[str, list[dict]] = {}
        for menu in site.menus:
            key = _menu_key(menu, site)
            data[key] = [_item_to_dict(it) for it in menu.items]
        data_file.parent.mkdir(parents=True, exist_ok=True)
        data_file.write_text(dump_yaml(data), encoding="utf-8")
        return 1

    # --- php transpile / theme scaffold -----------------------------------

    def replacement_for(self, rule: Any) -> str:
        return rule.replace_jekyll

    def include_directive(self, slug: str) -> str:
        slug = slug.replace("\\", "/").strip("/")
        return f'{{% include {slug}.html %}}'

    def marker(self, body: str, kind: str) -> str:
        body = (body.replace("{", " ").replace("}", " ")
                    .replace("*/", " ").replace("%}", " "))
        body = " ".join(body.split())
        return "{% comment %}wp2static " + kind + ": " + body + "{% endcomment %}"

    def balance_control_flow(self, text: str) -> str:
        matches = list(_TAG_RE.finditer(text))
        if not matches:
            return text
        stack: list[int] = []   # indices of still-open tags
        drop: set[int] = set()
        for i, m in enumerate(matches):
            tag = m.group(1)
            if tag in _OPENS:
                stack.append(i)
            elif tag in _CLOSE_FOR:
                want = _CLOSE_FOR[tag]
                if stack and matches[stack[-1]].group(1) == want:
                    stack.pop()
                else:
                    drop.add(i)
            elif tag in _CONTINUATIONS and not stack:
                drop.add(i)
        drop.update(stack)
        if not drop:
            return text
        return _rewrite_matches(self, text, matches, drop)

    def layout_for(self, slug: str, php_path: Path) -> Path | None:
        name = php_path.name
        if name == "functions.php":
            return None
        if len(php_path.parts) == 1 and name in _TOP_LEVEL_LAYOUTS:
            return Path(_TOP_LEVEL_LAYOUTS[name])
        # Top-level header.php / footer.php / sidebar*.php → bare includes.
        # Nested variants keep their full path so the `get_template_part(...)`
        # call that references them resolves to the same layout the WP theme
        # expects.
        stem = php_path.stem
        if len(php_path.parts) == 1 and (
            stem in ("header", "footer") or stem.startswith("sidebar")
        ):
            return Path("_includes") / f"{stem}.html"
        return Path("_includes") / php_path.with_suffix(".html")

    def emit_theme_metadata(
        self, theme_root: Path, slug: str, meta: Any,
    ) -> None:
        yml = theme_root / "theme.yml"
        yml.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"name: {meta.name or slug}",
            f'description: "{toml_escape(meta.description)}"',
            f"version: {meta.version or '0.0.0'}",
            f'author: "{toml_escape(meta.author)}"',
            f"homepage: {meta.uri}",
            f"license: {meta.license or 'unknown'}",
            f"tags: [{', '.join(meta.tags)}]",
        ]
        yml.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def stub_missing_includes(self, out_dir: Path, slug: str) -> int:
        include_root = out_dir / "_includes"
        if not out_dir.is_dir():
            return 0
        refs: set[str] = set()
        for p in out_dir.rglob("*.html"):
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in _INCLUDE_RE.finditer(text):
                refs.add(m.group(1).strip().strip('"\'`'))
        stubs = 0
        for ref in refs:
            dst = include_root / ref
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            body = (f"{{% comment %}}wp2static stub for missing include {ref}"
                    "{% endcomment %}\n")
            dst.write_text(body, encoding="utf-8")
            stubs += 1
        return stubs


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


def _menu_key(menu, site) -> str:
    """Return the slot name the theme registered this menu under, else slug."""
    for slot, slug in site.menu_locations.items():
        if slug == menu.slug:
            return slot
    return menu.slug


def _item_to_dict(item) -> dict:
    entry: dict = {"name": item.label, "url": item.url}
    if item.target:
        entry["target"] = item.target
    if item.children:
        entry["children"] = [_item_to_dict(c) for c in item.children]
    return entry
