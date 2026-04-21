"""Scaffold a Jekyll or Hugo theme from the active WordPress theme.

The scope is deliberately modest:

    1. Copy the WordPress theme directory's static assets (``css``,
       ``js``, ``images``, ``fonts``, ``assets``) into the target tree.
    2. Parse ``style.css``'s header block for name / version / author /
       description, then write a target-appropriate metadata file.
    3. Transpile each ``.php`` template to the target's template
       language (Liquid for Jekyll, Go html/template for Hugo) using a
       hand-written token map for the most common WordPress template
       tags.  Anything outside that map is preserved as an
       ``<!-- wp2static: unmapped ... -->`` comment so it's visible.
    4. Emit a per-theme ``MIGRATION.md`` listing unmapped calls and
       things we intentionally dropped (``functions.php``, widgets,
       customizer mods).

This is **not** a full port — it produces a scaffold that needs a pass
from a human to become a working theme.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_ASSET_DIRS = ("assets", "css", "js", "images", "img", "fonts")
_PHP_OPEN = re.compile(r"<\?(?:php|=)?\b", re.IGNORECASE)
_PHP_CLOSE = re.compile(r"\?>")


# --- style.css metadata parsing ---------------------------------------------

@dataclass
class ThemeMeta:
    name: str = ""
    uri: str = ""
    author: str = ""
    author_uri: str = ""
    description: str = ""
    version: str = ""
    license: str = ""
    tags: list[str] = field(default_factory=list)
    text_domain: str = ""


_META_FIELDS = {
    "theme name": "name",
    "theme uri": "uri",
    "author": "author",
    "author uri": "author_uri",
    "description": "description",
    "version": "version",
    "license": "license",
    "text domain": "text_domain",
    "tags": "tags",
}


def parse_style_css(path: Path) -> ThemeMeta:
    """Extract the ``/* … */`` theme header block from ``style.css``."""
    if not path.is_file():
        return ThemeMeta()
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"/\*(.*?)\*/", text, re.DOTALL)
    if not m:
        return ThemeMeta()
    meta = ThemeMeta()
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        field_name = _META_FIELDS.get(key.strip().lower())
        if not field_name:
            continue
        val = val.strip()
        if field_name == "tags":
            setattr(meta, field_name, [t.strip() for t in val.split(",") if t.strip()])
        else:
            setattr(meta, field_name, val)
    return meta


# --- PHP token map (target-aware) -------------------------------------------

@dataclass
class _Rule:
    pattern: re.Pattern
    replace_jekyll: str
    replace_hugo: str


def _rule(pattern: str, jekyll: str, hugo: str) -> _Rule:
    return _Rule(re.compile(pattern, re.DOTALL), jekyll, hugo)


# Rules are applied in order, so most-specific patterns go first. Each rule's
# replacement uses backrefs (\1, \2) to capture arguments. Where a rule can't
# be expressed as a simple regex substitution, we handle it in code below.
_RULES: list[_Rule] = [
    # localisation wrappers: __/_e/_ex/esc_html_e/esc_attr_e/esc_html__
    _rule(r"""(?:esc_html_e|esc_attr_e|_e)\s*\(\s*['"]([^'"]*)['"]\s*(?:,\s*['"][^'"]*['"]\s*)?\)\s*;?""",
          r"\1", r"\1"),
    _rule(r"""(?:esc_html__|esc_attr__|__)\s*\(\s*['"]([^'"]*)['"]\s*(?:,\s*['"][^'"]*['"]\s*)?\)""",
          r"\1", r"\1"),
    # esc_url/esc_attr/esc_html/wp_kses_post — pass the inner expression through
    _rule(r"""(?:esc_url|esc_attr|esc_html|wp_kses_post|absint|intval|trim)\s*\(\s*(.+?)\s*\)""",
          r"\1", r"\1"),

    # bloginfo / get_bloginfo
    _rule(r"""(?:get_)?bloginfo\s*\(\s*['"]name['"]\s*\)""",
          r"{{ site.title }}", r"{{ .Site.Title }}"),
    _rule(r"""(?:get_)?bloginfo\s*\(\s*['"]description['"]\s*\)""",
          r"{{ site.description }}", r"{{ .Site.Params.description }}"),
    _rule(r"""(?:get_)?bloginfo\s*\(\s*['"]charset['"]\s*\)""",
          r"{{ site.charset | default: 'utf-8' }}", r"utf-8"),
    _rule(r"""(?:get_)?bloginfo\s*\(\s*['"]url['"]\s*\)""",
          r"{{ site.url }}", r"{{ .Site.BaseURL }}"),
    _rule(r"""(?:get_)?bloginfo\s*\(\s*['"]language['"]\s*\)""",
          r"{{ site.lang | default: 'en' }}", r"{{ .Site.Language.Lang }}"),

    # language_attributes, body_class, post_class, site_url, home_url
    _rule(r"""language_attributes\s*\(\s*\)""",
          r"""lang="{{ site.lang | default: 'en' }}\"""",
          r"""lang=\"{{ .Site.Language.Lang }}\""""),
    _rule(r"""body_class\s*\(\s*\)""",
          r"""class="{{ page.body_class | default: 'page' }}\"""",
          r"""class=\"{{ .Params.body_class | default \"page\" }}\""""),
    _rule(r"""post_class\s*\(\s*\)""",
          r"""class="{{ include.post_class | default: 'post' }}\"""",
          r"""class=\"{{ .Params.post_class | default \"post\" }}\""""),
    _rule(r"""home_url\s*\(\s*['"]/?['"]\s*\)""",
          r"{{ '/' | absolute_url }}", r"""{{ \"/\" | absURL }}"""),
    _rule(r"""site_url\s*\(\s*\)""",
          r"{{ site.url }}", r"{{ .Site.BaseURL }}"),

    # post-level tags
    _rule(r"""the_title\s*\(\s*\)""",
          r"{{ page.title }}", r"{{ .Title }}"),
    _rule(r"""get_the_title\s*\(\s*\)""",
          r"{{ page.title }}", r"{{ .Title }}"),
    _rule(r"""the_content\s*\(\s*[^)]*\)""",
          r"{{ content }}", r"{{ .Content }}"),
    _rule(r"""the_excerpt\s*\(\s*\)""",
          r"{{ page.excerpt | default: page.content | strip_html | truncatewords: 40 }}",
          r"{{ .Summary }}"),
    _rule(r"""the_ID\s*\(\s*\)""",
          r"{{ page.id | default: page.path }}", r"{{ .File.UniqueID }}"),
    _rule(r"""the_permalink\s*\(\s*\)""",
          r"{{ page.url | absolute_url }}", r"{{ .Permalink }}"),
    _rule(r"""get_permalink\s*\(\s*\)""",
          r"{{ page.url | absolute_url }}", r"{{ .Permalink }}"),
    _rule(r"""the_post_thumbnail\s*\(\s*[^)]*\)""",
          r"{% if page.image %}<img src=\"{{ page.image | relative_url }}\" alt=\"{{ page.title | escape }}\">{% endif %}",
          r"{{ with .Params.image }}<img src=\"{{ . | relURL }}\" alt=\"{{ $.Title }}\">{{ end }}"),
    _rule(r"""has_post_thumbnail\s*\(\s*\)""",
          r"page.image", r".Params.image"),

    # template parts / layout helpers — handled separately below so we can
    # concatenate the two args; leave a placeholder that _compose can spot.
    # (Nothing here; see _rewrite_template_parts.)

    # wp_head / wp_footer / wp_body_open — static sites don't need these
    _rule(r"""wp_head\s*\(\s*\)""",
          r"<!-- wp2static: site head -->",
          r"{{ hugo.Generator }}"),
    _rule(r"""wp_footer\s*\(\s*\)""",
          r"", r""),
    _rule(r"""wp_body_open\s*\(\s*\)""",
          r"", r""),

    # loop — only the canonical shape. Unusual loops fall through.
    _rule(r"""if\s*\(\s*have_posts\s*\(\s*\)\s*\)\s*:\s*while\s*\(\s*have_posts\s*\(\s*\)\s*\)\s*:\s*the_post\s*\(\s*\)\s*;""",
          r"{% for page in paginator.posts %}",
          r"{{ range .Paginator.Pages }}"),
    _rule(r"""endwhile\s*;\s*endif\s*;""",
          r"{% endfor %}", r"{{ end }}"),
    _rule(r"""endwhile\s*;""",
          r"{% endfor %}", r"{{ end }}"),
    _rule(r"""endif\s*;""",
          r"{% endif %}", r"{{ end }}"),
    _rule(r"""else\s*:""",
          r"{% else %}", r"{{ else }}"),

    # conditionals we can translate
    _rule(r"""is_home\s*\(\s*\)""",
          r"page.url == '/'", r".IsHome"),
    _rule(r"""is_front_page\s*\(\s*\)""",
          r"page.url == '/'", r".IsHome"),
    _rule(r"""is_page\s*\(\s*\)""",
          r"page.kind == 'page'", r".IsPage"),
    _rule(r"""is_single\s*\(\s*\)""",
          r"page.collection == 'posts'", r".IsPage"),
    _rule(r"""is_paged\s*\(\s*\)""",
          r"paginator.page > 1", r"gt .Paginator.PageNumber 1"),

    # get_search_form is close enough to a search include
    _rule(r"""get_search_form\s*\(\s*\)""",
          r"{% include searchform.html %}",
          r"""{{ partial \"searchform.html\" . }}"""),
]


_GET_HEADER_RE = re.compile(r"""get_header\s*\(\s*(?:['"]([^'"]*)['"])?\s*\)""")
_GET_FOOTER_RE = re.compile(r"""get_footer\s*\(\s*(?:['"]([^'"]*)['"])?\s*\)""")
_GET_SIDEBAR_RE = re.compile(r"""get_sidebar\s*\(\s*(?:['"]([^'"]*)['"])?\s*\)""")
_GET_TEMPLATE_PART_RE = re.compile(
    r"""get_template_part\s*\(\s*['"]([^'"]+)['"]\s*(?:,\s*['"]([^'"]*)['"]\s*)?\)""",
)


def _include(target: str, slug: str) -> str:
    slug = slug.replace("\\", "/").strip("/")
    if target == "hugo":
        return f'{{{{ partial "{slug}.html" . }}}}'
    return f'{{% include {slug}.html %}}'


def _rewrite_template_parts(php: str, target: str) -> str:
    def _tp(match: re.Match) -> str:
        name, part = match.group(1), match.group(2)
        slug = f"{name}-{part}" if part else name
        return _include(target, slug)
    php = _GET_TEMPLATE_PART_RE.sub(_tp, php)
    php = _GET_HEADER_RE.sub(
        lambda m: _include(target, f"header-{m.group(1)}" if m.group(1) else "header"),
        php,
    )
    php = _GET_FOOTER_RE.sub(
        lambda m: _include(target, f"footer-{m.group(1)}" if m.group(1) else "footer"),
        php,
    )
    php = _GET_SIDEBAR_RE.sub(
        lambda m: _include(target, f"sidebar-{m.group(1)}" if m.group(1) else "sidebar"),
        php,
    )
    return php


# --- transpiler -------------------------------------------------------------

def _transpile_php(php: str, target: str, unmapped: list[str]) -> str:
    """Rewrite a single ``<?php … ?>`` block.

    Things the rule table can handle are substituted in place; the remaining
    tokens — ``echo``, bare function calls, ``if`` on unknown expressions,
    variable assignments, theme-specific helpers like ``bard_options`` —
    are left inline as an HTML comment so they're visible in the output.
    """
    original = php.strip()
    php = _rewrite_template_parts(php, target)
    for rule in _RULES:
        repl = rule.replace_hugo if target == "hugo" else rule.replace_jekyll
        php = rule.pattern.sub(repl, php)

    # Clean up some PHP artefacts we can safely drop.
    php = re.sub(r"\becho\s+", "", php)
    php = re.sub(r";\s*$", "", php.strip())

    # Everything that survived is opaque to us. If the block reduced to pure
    # whitespace, drop it; otherwise record the residue.
    residue = php.strip()
    if not residue:
        return ""
    # If only directives / Liquid / Hugo tags remain, keep them.
    if _looks_like_template_only(residue):
        return residue
    unmapped.append(original.split("\n", 1)[0].strip()[:200])
    safe = (original
            .replace("{%", "{ %").replace("%}", "% }")
            .replace("{{", "{ {").replace("}}", "} }")
            .replace("-->", "--&gt;"))
    return f"<!-- wp2static: unmapped PHP: {safe} -->"


_TEMPLATE_TOKEN_RE = re.compile(r"(\{\{.*?\}\}|\{%.*?%\}|<!--.*?-->)", re.DOTALL)


def _looks_like_template_only(text: str) -> bool:
    """True if ``text`` is made up entirely of SSG template tags / comments."""
    stripped = _TEMPLATE_TOKEN_RE.sub("", text).strip()
    return stripped == ""


def transpile_template(php: str, target: str) -> tuple[str, list[str]]:
    """Transpile a full ``.php`` file to a Jekyll / Hugo template string.

    Returns ``(output, unmapped_calls)``.
    """
    unmapped: list[str] = []
    out = []
    i = 0
    n = len(php)
    while i < n:
        m = _PHP_OPEN.search(php, i)
        if not m:
            out.append(php[i:])
            break
        out.append(php[i:m.start()])
        end = _PHP_CLOSE.search(php, m.end())
        if not end:
            # Unterminated <?php — treat the rest as PHP.
            block = php[m.end():]
            out.append(_transpile_php(block, target, unmapped))
            break
        block = php[m.end():end.start()]
        out.append(_transpile_php(block, target, unmapped))
        i = end.end()
    body = "".join(out)
    body = _balance_control_flow(body, target)
    return body, unmapped


# --- control-flow balancing -------------------------------------------------
#
# Rule-based PHP translation can leave orphan control tags in the output: e.g.
# a ``<?php if (have_posts()): while... endwhile; else: ?>`` block that also
# contains untranslatable PHP collapses into one unmapped comment, but the
# paired ``<?php endif; ?>`` later on is standalone and *does* translate — so
# the resulting template has an ``{% endif %}`` with no opener. Liquid and
# Go's html/template both refuse to parse that. We scan the rendered output,
# pair opens with closes, and convert anything unpaired into a comment.

_JEKYLL_TAG_RE = re.compile(r"\{%-?\s*(\w+)\b[^%]*-?%\}")
_JEKYLL_OPENS = {"if", "for", "unless", "case", "capture", "tablerow",
                 "comment", "raw"}
_JEKYLL_CLOSE_FOR = {
    "endif": "if", "endfor": "for", "endunless": "unless",
    "endcase": "case", "endcapture": "capture", "endtablerow": "tablerow",
    "endcomment": "comment", "endraw": "raw",
}
_JEKYLL_CONTINUATIONS = {"else", "elsif", "when"}

_HUGO_TAG_RE = re.compile(r"\{\{-?\s*(\w+)\b[^}]*-?\}\}")
_HUGO_OPENS = {"if", "with", "range", "block", "define"}


def _balance_control_flow(text: str, target: str) -> str:
    if target == "hugo":
        return _balance_hugo(text)
    return _balance_jekyll(text)


def _drop_tag(match: re.Match) -> str:
    # Both Liquid and Go html/template parse tags inside HTML comments, so
    # the dropped tag must be defanged — otherwise the "orphan endif" note
    # keeps tripping the very parser we were trying to protect from it.
    orig = (match.group(0)
            .replace("{%", "{ %").replace("%}", "% }")
            .replace("{{", "{ {").replace("}}", "} }")
            .replace("-->", "--&gt;"))
    return f"<!-- wp2static: dropped orphan {orig} -->"


def _balance_jekyll(text: str) -> str:
    matches = list(_JEKYLL_TAG_RE.finditer(text))
    if not matches:
        return text
    stack: list[int] = []   # indices into ``matches`` for still-open tags
    drop: set[int] = set()
    for i, m in enumerate(matches):
        tag = m.group(1)
        if tag in _JEKYLL_OPENS:
            stack.append(i)
        elif tag in _JEKYLL_CLOSE_FOR:
            want = _JEKYLL_CLOSE_FOR[tag]
            if stack and matches[stack[-1]].group(1) == want:
                stack.pop()
            else:
                drop.add(i)
        elif tag in _JEKYLL_CONTINUATIONS and not stack:
            drop.add(i)
    drop.update(stack)   # any unclosed openers
    if not drop:
        return text
    return _rewrite_matches(text, matches, drop)


def _balance_hugo(text: str) -> str:
    matches = list(_HUGO_TAG_RE.finditer(text))
    if not matches:
        return text
    stack: list[int] = []
    drop: set[int] = set()
    for i, m in enumerate(matches):
        tag = m.group(1)
        if tag in _HUGO_OPENS:
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
    return _rewrite_matches(text, matches, drop)


def _rewrite_matches(
    text: str, matches: list[re.Match], drop: set[int],
) -> str:
    out: list[str] = []
    last = 0
    for i, m in enumerate(matches):
        out.append(text[last:m.start()])
        out.append(_drop_tag(m) if i in drop else m.group(0))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


# --- asset + output layout --------------------------------------------------

def _copy_static_assets(src: Path, dst: Path) -> int:
    """Copy known asset subdirectories, returning the count of files copied."""
    total = 0
    for name in _ASSET_DIRS:
        candidate = src / name
        if not candidate.is_dir():
            continue
        target_subdir = dst / name
        for file in candidate.rglob("*"):
            if not file.is_file():
                continue
            rel = file.relative_to(candidate)
            out = target_subdir / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, out)
            total += 1
    # top-level style.css and rtl.css are the theme's main stylesheets
    for style in ("style.css", "rtl.css"):
        p = src / style
        if p.is_file():
            out = dst / style
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, out)
            total += 1
    return total


def _jekyll_layout_for(php_path: Path) -> Path | None:
    """Map a WP template file to a Jekyll layout/include path.

    Top-level templates become ``_layouts/<name>.html``; anything under
    ``templates/…`` or other subdirs becomes an include.
    """
    name = php_path.name
    if name == "functions.php":
        return None  # never transpile
    top_level_layouts = {
        "index.php": "_layouts/home.html",
        "single.php": "_layouts/post.html",
        "page.php": "_layouts/page.html",
        "archive.php": "_layouts/archive.html",
        "category.php": "_layouts/category.html",
        "tag.php": "_layouts/tag.html",
        "search.php": "_layouts/search.html",
        "404.php": "_layouts/404.html",
    }
    if len(php_path.parts) == 1 and name in top_level_layouts:
        return Path(top_level_layouts[name])
    # Top-level header.php / footer.php / sidebar*.php → bare includes so
    # `get_header()` / `get_sidebar('left')` can find them.  Nested files
    # (e.g. `templates/sidebars/sidebar-left.php`) keep their full path so
    # the `get_template_part(...)` call that references them resolves to
    # the same layout the WP theme expects.
    stem = php_path.stem
    if len(php_path.parts) == 1 and (
        stem in ("header", "footer") or stem.startswith("sidebar")
    ):
        return Path("_includes") / f"{stem}.html"
    # everything else (incl. templates/**) → _includes preserving structure
    return Path("_includes") / php_path.with_suffix(".html")


def _hugo_layout_for(slug: str, php_path: Path) -> Path | None:
    name = php_path.name
    if name == "functions.php":
        return None
    stem = php_path.stem
    theme_root = Path("themes") / slug
    top_level_layouts = {
        "index.php": "layouts/_default/list.html",
        "single.php": "layouts/_default/single.html",
        "page.php": "layouts/_default/page.html",
        "archive.php": "layouts/_default/archive.html",
        "category.php": "layouts/_default/taxonomy.html",
        "tag.php": "layouts/_default/term.html",
        "search.php": "layouts/_default/search.html",
        "404.php": "layouts/404.html",
    }
    if len(php_path.parts) == 1 and name in top_level_layouts:
        return theme_root / top_level_layouts[name]
    if len(php_path.parts) == 1 and (stem in ("header", "footer")
                                     or stem.startswith("sidebar")):
        return theme_root / "layouts" / "partials" / f"{stem}.html"
    # templates/** → partials/** (same structure)
    return theme_root / "layouts" / "partials" / php_path.with_suffix(".html")


def _default_front_matter(target: str, layout_kind: str | None) -> str:
    if not layout_kind:
        return ""
    if target == "hugo":
        return ""  # Hugo layouts don't have front matter
    return f"---\nlayout: {layout_kind}\n---\n"


def _emit_metadata(target: str, dst: Path, slug: str, meta: ThemeMeta) -> None:
    if target == "hugo":
        theme_toml = dst / "theme.toml"
        theme_toml.parent.mkdir(parents=True, exist_ok=True)
        tags_list = ", ".join('"' + _escape_toml(t) + '"' for t in meta.tags)
        lines = [
            f'name = "{_escape_toml(meta.name or slug)}"',
            f'license = "{_escape_toml(meta.license) or "unknown"}"',
            'licenselink = ""',
            f'description = "{_escape_toml(meta.description)}"',
            f'homepage = "{_escape_toml(meta.uri)}"',
            f'tags = [{tags_list}]',
            'features = []',
            'min_version = "0.80.0"',
            '[author]',
            f'  name = "{_escape_toml(meta.author)}"',
            f'  homepage = "{_escape_toml(meta.author_uri)}"',
        ]
        theme_toml.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        yml = dst / "theme.yml"
        yml.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"name: {meta.name or slug}",
            f'description: "{_escape_toml(meta.description)}"',
            f"version: {meta.version or '0.0.0'}",
            f'author: "{_escape_toml(meta.author)}"',
            f"homepage: {meta.uri}",
            f"license: {meta.license or 'unknown'}",
            f"tags: [{', '.join(meta.tags)}]",
        ]
        yml.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _escape_toml(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


# --- public API --------------------------------------------------------------

def migrate_active_theme(
    site, themes_src: Path, out_dir: Path, target: str,
) -> dict:
    """Scaffold the active theme into ``out_dir``. Returns a stats dict."""
    slug = site.active_theme
    if not slug:
        log.warning("no active theme recorded in wp_options; skipping")
        return {"skipped": True}

    src = themes_src / slug
    if not src.is_dir():
        log.warning("active theme %r not found under %s", slug, themes_src)
        return {"skipped": True, "slug": slug}

    meta = parse_style_css(src / "style.css")
    log.info("migrating theme %r (%s) for %s", slug, meta.name or "no-name", target)

    if target == "hugo":
        theme_root = out_dir / "themes" / slug
        static_root = theme_root / "static"
    else:
        theme_root = out_dir
        static_root = out_dir / "assets" / "theme"
    theme_root.mkdir(parents=True, exist_ok=True)

    assets_copied = _copy_static_assets(src, static_root)
    _emit_metadata(target, theme_root, slug, meta)

    all_unmapped: dict[str, list[str]] = {}
    templates_written = 0
    for php_path in sorted(src.rglob("*.php")):
        rel = php_path.relative_to(src)
        # skip plugin bundles shipped inside the theme (common WP practice)
        top = rel.parts[0] if rel.parts else ""
        if top in ("freemius", "plugins", "inc"):
            continue

        dst_rel = (
            _hugo_layout_for(slug, rel)
            if target == "hugo"
            else _jekyll_layout_for(rel)
        )
        if dst_rel is None:
            continue
        dst = out_dir / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        php_source = php_path.read_text(encoding="utf-8", errors="replace")
        body, unmapped = transpile_template(php_source, target)
        if unmapped:
            all_unmapped[str(rel)] = unmapped

        front = ""
        if target != "hugo":
            stem = rel.stem
            if stem in ("index", "single", "page", "archive",
                        "category", "tag", "search", "404"):
                front = _default_front_matter(target, "default")
        dst.write_text(front + body, encoding="utf-8")
        templates_written += 1

    _write_migration_notes(theme_root, slug, meta, all_unmapped, target)

    return {
        "skipped": False,
        "slug": slug,
        "name": meta.name,
        "target": target,
        "assets_copied": assets_copied,
        "templates_written": templates_written,
        "templates_with_unmapped": len(all_unmapped),
    }


def _write_migration_notes(
    theme_root: Path, slug: str, meta: ThemeMeta,
    unmapped: dict[str, list[str]], target: str,
) -> None:
    lines = [
        f"# {meta.name or slug} — migration notes",
        "",
        "This theme was scaffolded by `wp2static`. It is a starting point,",
        "not a drop-in replacement for the WordPress theme.",
        "",
        "## Dropped on purpose",
        "",
        "- `functions.php` — WordPress runtime hooks / action filters.",
        "- Theme-specific option panels (Customizer / Redux / Kirki).",
        "- Widget areas and dynamic_sidebar calls.",
        "- Freemius / plugin bundles shipped inside the theme.",
        "",
        "## PHP calls that couldn't be translated",
        "",
    ]
    if not unmapped:
        lines.append("_None — every PHP block mapped cleanly._")
    else:
        for path, calls in sorted(unmapped.items()):
            lines.append(f"### `{path}`")
            lines.append("")
            for call in calls:
                lines.append(f"- `{call}`")
            lines.append("")
    lines.append("")
    lines.append(
        "Search the generated templates for "
        "`wp2static: unmapped` to find each occurrence in context."
    )
    (theme_root / "MIGRATION.md").write_text("\n".join(lines) + "\n",
                                             encoding="utf-8")
