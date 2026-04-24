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
       ``/*wp2static unmapped: ...*/`` marker so it's visible.
    4. Emit a per-theme ``MIGRATION.md`` listing unmapped calls and
       things we intentionally dropped (``functions.php``, widgets,
       customizer mods).

This is **not** a full port — it produces a scaffold that needs a pass
from a human to become a working theme.

Target-specific details (layout paths, include syntax, markers, balancer,
metadata format, stubs) are owned by :class:`~wp2static.targets.Target`;
this module holds the source-side pieces that are the same regardless of
which SSG we emit for.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .targets import Target, get_target

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
    # Go template string literals use backticks here (not `"…"`) so they
    # never nest inside an HTML `"…"` attribute value — html/template's
    # context scanner otherwise treats the inner `"` as closing the attr.
    _rule(r"""language_attributes\s*\(\s*\)""",
          '''lang="{{ site.lang | default: 'en' }}"''',
          '''lang="{{ .Site.Language.Lang }}"'''),
    _rule(r"""body_class\s*\(\s*\)""",
          '''class="{{ page.body_class | default: 'page' }}"''',
          'class="{{ .Params.body_class | default `page` }}"'),
    _rule(r"""post_class\s*\(\s*\)""",
          '''class="{{ include.post_class | default: 'post' }}"''',
          'class="{{ .Params.post_class | default `post` }}"'),
    _rule(r"""home_url\s*\(\s*['"]/?['"]\s*\)""",
          r"{{ '/' | absolute_url }}", "{{ `/` | absURL }}"),
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
          '''{% if page.image %}<img src="{{ page.image | relative_url }}" alt="{{ page.title | escape }}">{% endif %}''',
          '''{{ with .Params.image }}<img src="{{ . | relURL }}" alt="{{ $.Title }}">{{ end }}'''),
    _rule(r"""has_post_thumbnail\s*\(\s*\)""",
          r"page.image", r".Params.image"),

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

    # get_search_form is close enough to a search include — include directive
    # is target-specific, so we defer to Target.include_directive via the
    # _GET_SEARCH_FORM_RE rewriter below rather than a straight substitution.
]


_GET_HEADER_RE = re.compile(r"""get_header\s*\(\s*(?:['"]([^'"]*)['"])?\s*\)""")
_GET_FOOTER_RE = re.compile(r"""get_footer\s*\(\s*(?:['"]([^'"]*)['"])?\s*\)""")
_GET_SIDEBAR_RE = re.compile(r"""get_sidebar\s*\(\s*(?:['"]([^'"]*)['"])?\s*\)""")
_GET_TEMPLATE_PART_RE = re.compile(
    r"""get_template_part\s*\(\s*['"]([^'"]+)['"]\s*(?:,\s*['"]([^'"]*)['"]\s*)?\)""",
)
_GET_SEARCH_FORM_RE = re.compile(r"""get_search_form\s*\(\s*\)""")


def _rewrite_template_parts(php: str, target: Target) -> str:
    def _tp(match: re.Match) -> str:
        name, part = match.group(1), match.group(2)
        slug = f"{name}-{part}" if part else name
        return target.include_directive(slug)
    php = _GET_TEMPLATE_PART_RE.sub(_tp, php)
    php = _GET_HEADER_RE.sub(
        lambda m: target.include_directive(
            f"header-{m.group(1)}" if m.group(1) else "header",
        ),
        php,
    )
    php = _GET_FOOTER_RE.sub(
        lambda m: target.include_directive(
            f"footer-{m.group(1)}" if m.group(1) else "footer",
        ),
        php,
    )
    php = _GET_SIDEBAR_RE.sub(
        lambda m: target.include_directive(
            f"sidebar-{m.group(1)}" if m.group(1) else "sidebar",
        ),
        php,
    )
    php = _GET_SEARCH_FORM_RE.sub(
        lambda m: target.include_directive("searchform"),
        php,
    )
    return php


# --- transpiler -------------------------------------------------------------

def _transpile_php(php: str, target: Target, unmapped: list[str]) -> str:
    """Rewrite a single ``<?php … ?>`` block.

    Things the rule table can handle are substituted in place; the remaining
    tokens — ``echo``, bare function calls, ``if`` on unknown expressions,
    variable assignments, theme-specific helpers like ``bard_options`` —
    are left inline as an HTML comment so they're visible in the output.
    """
    original = php.strip()
    php = _rewrite_template_parts(php, target)
    for rule in _RULES:
        php = rule.pattern.sub(target.replacement_for(rule), php)

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
    return target.marker(original.split("\n", 1)[0][:200], "unmapped")


_TEMPLATE_TOKEN_RE = re.compile(r"(\{\{.*?\}\}|\{%.*?%\}|<!--.*?-->)", re.DOTALL)
_QUOTED_STRING_RE = re.compile(r'"[^"]*"|\'[^\']*\'')
_PHP_RESIDUE_RE = re.compile(
    r"\$\w+"                                       # $variable
    r"|->\w|::\w"                                  # member / static access
    r"|\b(?:echo|print|if|else|elseif|endif|while|endwhile"
    r"|foreach|endforeach|for|endfor|function|return"
    r"|isset|empty|array)\b"                       # control flow & intrinsics
    r"|(?<![-\w.])\w+\s*\("                        # bare function call
)


def _looks_like_template_only(text: str) -> bool:
    """True if no identifiable PHP construct remains in ``text``.

    After the rule table runs, a translated block is a mix of plain text,
    HTML, SSG template tokens, and (sometimes) the attribute scaffolding
    a rule wraps around them — e.g. ``class="{{ .Params.body_class }}"``.
    We can't simply check "is there any non-template text?", because the
    mix is intentional. Instead, scrub template tokens, HTML comments,
    and quoted string literals, then look for anything that still reads
    as PHP: variables, member access, control keywords, or a bare
    function call. If none of those survive, the block transpiled
    cleanly and is safe to emit.
    """
    scrubbed = _TEMPLATE_TOKEN_RE.sub(" ", text)
    scrubbed = _QUOTED_STRING_RE.sub(" ", scrubbed)
    return _PHP_RESIDUE_RE.search(scrubbed) is None


def transpile_template(php: str, target) -> tuple[str, list[str]]:
    """Transpile a full ``.php`` file to a Jekyll / Hugo template string.

    ``target`` may be a target name (``"jekyll"`` / ``"hugo"``) or an
    already-resolved :class:`Target` instance.

    Returns ``(output, unmapped_calls)``.
    """
    tgt = get_target(target)
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
            out.append(_transpile_php(block, tgt, unmapped))
            break
        block = php[m.end():end.start()]
        out.append(_transpile_php(block, tgt, unmapped))
        i = end.end()
    body = "".join(out)
    body = tgt.balance_control_flow(body)
    return body, unmapped


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


# --- legacy module-level delegators ------------------------------------------
#
# The test suite (and any downstream callers) import a few helpers directly
# from this module. Keep thin wrappers so behaviour is unchanged; the real
# logic lives on the Target objects.

def _jekyll_layout_for(php_path: Path) -> Path | None:
    return get_target("jekyll").layout_for("", php_path)


def _hugo_layout_for(slug: str, php_path: Path) -> Path | None:
    return get_target("hugo").layout_for(slug, php_path)


def _stub_missing_includes(out_dir: Path, slug: str, target: str) -> int:
    return get_target(target).stub_missing_includes(out_dir, slug)


# --- public API --------------------------------------------------------------

def migrate_active_theme(
    site, themes_src: Path, out_dir: Path, target: str,
) -> dict:
    """Scaffold the active theme into ``out_dir``. Returns a stats dict."""
    tgt = get_target(target)
    slug = site.active_theme
    if not slug:
        log.warning("no active theme recorded in wp_options; skipping")
        return {"skipped": True}

    src = themes_src / slug
    if not src.is_dir():
        log.warning("active theme %r not found under %s", slug, themes_src)
        return {"skipped": True, "slug": slug}

    meta = parse_style_css(src / "style.css")
    log.info("migrating theme %r (%s) for %s", slug, meta.name or "no-name", tgt.name)

    theme_root = tgt.theme_root(out_dir, slug)
    static_root = tgt.theme_static_root(out_dir, slug)
    theme_root.mkdir(parents=True, exist_ok=True)

    assets_copied = _copy_static_assets(src, static_root)
    tgt.emit_theme_metadata(theme_root, slug, meta)

    all_unmapped: dict[str, list[str]] = {}
    templates_written = 0
    for php_path in sorted(src.rglob("*.php")):
        rel = php_path.relative_to(src)
        # skip plugin bundles shipped inside the theme (common WP practice)
        top = rel.parts[0] if rel.parts else ""
        if top in ("freemius", "plugins", "inc"):
            continue

        dst_rel = tgt.layout_for(slug, rel)
        if dst_rel is None:
            continue
        dst = out_dir / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        php_source = php_path.read_text(encoding="utf-8", errors="replace")
        body, unmapped = transpile_template(php_source, tgt)
        if unmapped:
            all_unmapped[str(rel)] = unmapped

        front = ""
        if tgt.name != "hugo":
            stem = rel.stem
            if stem in ("index", "single", "page", "archive",
                        "category", "tag", "search", "404"):
                front = tgt.default_front_matter("default")
        dst.write_text(front + body, encoding="utf-8")
        templates_written += 1

    stubs_written = tgt.stub_missing_includes(out_dir, slug)
    finalize_stats = tgt.finalize_theme(out_dir, slug)

    _write_migration_notes(theme_root, slug, meta, all_unmapped, tgt.name)

    return {
        "skipped": False,
        "stubs_written": stubs_written,
        "slug": slug,
        "name": meta.name,
        "target": tgt.name,
        "assets_copied": assets_copied,
        "templates_written": templates_written,
        "templates_with_unmapped": len(all_unmapped),
        **finalize_stats,
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
        "`wp2static unmapped` to find each occurrence in context."
    )
    (theme_root / "MIGRATION.md").write_text("\n".join(lines) + "\n",
                                             encoding="utf-8")
