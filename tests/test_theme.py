"""Tests for the theme scaffolder."""

from __future__ import annotations

from pathlib import Path

from wp2static.theme import (
    _hugo_layout_for,
    _jekyll_layout_for,
    _stub_missing_includes,
    parse_style_css,
    transpile_template,
)


def test_parse_style_css_reads_header_fields(tmp_path: Path):
    css = tmp_path / "style.css"
    css.write_text(
        "/*\n"
        "Theme Name: Demo Theme\n"
        "Author: Marco\n"
        "Description: A demo.\n"
        "Version: 1.2.3\n"
        "Tags: blog, responsive, minimal\n"
        "*/\n"
        "body { margin: 0; }\n",
        encoding="utf-8",
    )
    meta = parse_style_css(css)
    assert meta.name == "Demo Theme"
    assert meta.author == "Marco"
    assert meta.version == "1.2.3"
    assert meta.tags == ["blog", "responsive", "minimal"]


def test_parse_style_css_missing_file_returns_blank_meta(tmp_path: Path):
    meta = parse_style_css(tmp_path / "nope.css")
    assert meta.name == ""
    assert meta.tags == []


def test_jekyll_layout_for_maps_top_level_templates():
    assert _jekyll_layout_for(Path("index.php")) == Path("_layouts/home.html")
    assert _jekyll_layout_for(Path("single.php")) == Path("_layouts/post.html")
    assert _jekyll_layout_for(Path("404.php")) == Path("_layouts/404.html")
    assert _jekyll_layout_for(Path("header.php")) == Path("_includes/header.html")
    # functions.php is never emitted
    assert _jekyll_layout_for(Path("functions.php")) is None
    # nested becomes an include preserving structure
    assert _jekyll_layout_for(Path("templates/content.php")) == Path(
        "_includes/templates/content.html"
    )
    # nested sidebar/header/footer files keep their full path so the
    # corresponding {% include templates/sidebars/sidebar-left.html %}
    # emitted from get_template_part('templates/sidebars/sidebar','left')
    # resolves to an existing file.
    assert _jekyll_layout_for(Path("templates/sidebars/sidebar-left.php")) == Path(
        "_includes/templates/sidebars/sidebar-left.html"
    )
    assert _jekyll_layout_for(Path("templates/header/featured-slider.php")) == Path(
        "_includes/templates/header/featured-slider.html"
    )


def test_hugo_layout_for_puts_things_under_theme_root():
    assert _hugo_layout_for("kale", Path("single.php")) == Path(
        "themes/kale/layouts/_default/single.html"
    )
    assert _hugo_layout_for("kale", Path("header.php")) == Path(
        "themes/kale/layouts/partials/header.html"
    )
    assert _hugo_layout_for("kale", Path("templates/sidebars/sidebar-left.php")) == Path(
        "themes/kale/layouts/partials/templates/sidebars/sidebar-left.html"
    )
    assert _hugo_layout_for("kale", Path("functions.php")) is None


def test_transpile_rewrites_core_tags_for_jekyll():
    php = (
        "<html>\n"
        "<head><title><?php bloginfo('name'); ?></title></head>\n"
        "<body>\n"
        "<h1><?php the_title(); ?></h1>\n"
        "<div><?php the_content(); ?></div>\n"
        "</body>\n"
        "</html>\n"
    )
    out, unmapped = transpile_template(php, "jekyll")
    assert "{{ site.title }}" in out
    assert "{{ page.title }}" in out
    assert "{{ content }}" in out
    assert unmapped == []


def test_transpile_rewrites_core_tags_for_hugo():
    php = "<title><?php bloginfo('name'); ?></title><?php the_content(); ?>"
    out, _ = transpile_template(php, "hugo")
    assert "{{ .Site.Title }}" in out
    assert "{{ .Content }}" in out


def test_hugo_replacements_use_safe_string_quoting():
    # Go's html/template parser rejects `\"` inside `{{ … }}` ("unexpected
    # `\\` in command") and also gets confused when a Go-template string
    # literal inside a `{{ … }}` action uses the same quote character as
    # the surrounding HTML attribute value. Use backticks for Go-template
    # string literals so the rule output survives every HTML context.
    php = (
        "<html <?php language_attributes(); ?>>\n"
        "<body <?php body_class(); ?>>\n"
        "<a href='<?php home_url('/'); ?>'>home</a>\n"
        "<?php the_post_thumbnail(); ?>\n"
        "<?php get_search_form(); ?>\n"
        "</body></html>"
    )
    out, _ = transpile_template(php, "hugo")
    assert "\\\"" not in out
    # Spot-check that the usual patterns are there in their clean shape.
    assert 'lang="{{ .Site.Language.Lang }}"' in out
    assert "{{ `/` | absURL }}" in out
    assert "{{ partial `searchform.html` . }}" in out
    # The body_class rule must not nest `"page"` inside `class="…"`.
    assert 'class="{{ .Params.body_class | default `page` }}"' in out


def test_transpile_expands_get_header_and_template_parts():
    php = "<?php get_header(); ?>\n<?php get_template_part('content', 'single'); ?>"
    jekyll, _ = transpile_template(php, "jekyll")
    assert "{% include header.html %}" in jekyll
    assert "{% include content-single.html %}" in jekyll
    hugo, _ = transpile_template(php, "hugo")
    assert "{{ partial `header.html` . }}" in hugo
    assert "{{ partial `content-single.html` . }}" in hugo


def test_transpile_marks_unmapped_calls():
    php = "<?php bard_options('layout'); ?>"
    out, unmapped = transpile_template(php, "jekyll")
    assert "wp2static unmapped" in out
    assert any("bard_options" in call for call in unmapped)


def test_transpile_drops_orphan_jekyll_endif():
    # The canonical bad-shape: a PHP block with unmappable logic around the
    # loop swallows the `if (have_posts()):` opener as an unmapped comment,
    # but the later standalone `<?php endif; ?>` does translate — leaving a
    # dangling `{% endif %}` that Liquid rejects. The balancer must rewrite
    # the orphan into a marker that doesn't itself read as a tag to any
    # parser (Liquid, Go html/template, or HTML's attribute tokeniser).
    php = (
        "<?php if (have_posts()) : while (have_posts()) : the_post();\n"
        "  bard_options('x'); endwhile; else: ?>\n"
        "<p>no posts</p>\n"
        "<?php endif; ?>"
    )
    out, _ = transpile_template(php, "jekyll")
    assert "wp2static dropped orphan: endif" in out
    # No live `{% endif %}` survives anywhere in the output.
    assert "{% endif %}" not in out
    # The drop marker uses Liquid's own comment tag so it's safe inside
    # HTML attribute values and renders to empty at build time.
    assert "{% comment %}" in out
    assert "{% endcomment %}" in out


def test_transpile_drops_orphan_hugo_end():
    php = "<?php endif; ?>"   # rule table emits {{ end }} with no opener
    out, _ = transpile_template(php, "hugo")
    assert "wp2static dropped orphan: end" in out
    assert "{{ end }}" not in out
    # Uses Go's template-comment syntax, which Hugo renders to empty.
    assert out.strip().startswith("{{/*")
    assert out.strip().endswith("*/}}")


def test_unmapped_php_marker_cannot_break_out_of_its_own_comment():
    # Liquid's `{% comment %}` wrapper renders to empty and so swallows
    # any HTML / template syntax inside — *unless* the body itself
    # contains a `{% endcomment %}` or a stray `%}` that ends the comment
    # tag early. Defang the characters that could close the comment so a
    # hostile PHP source string can't escape it.
    php = "<?php echo '{% boom %} {{ kaboom }} %} endcomment'; bard_options('x'); ?>"
    out, _ = transpile_template(php, "jekyll")
    assert "wp2static unmapped" in out
    # No `%}` or `{%` anywhere except in the comment delimiters we emit.
    cleaned = out.replace("{% comment %}", "").replace("{% endcomment %}", "")
    assert "%}" not in cleaned
    assert "{%" not in cleaned


def test_unmapped_php_marker_cannot_break_out_of_its_own_comment_hugo():
    # Same story for Hugo: `*/` inside the Go-template comment would
    # terminate it. Make sure nothing resembling `*/` escapes.
    php = "<?php /* */ echo 'oops */'; bard_options('x'); ?>"
    out, _ = transpile_template(php, "hugo")
    assert "wp2static unmapped" in out
    cleaned = out.replace("{{/*", "").replace("*/}}", "")
    assert "*/" not in cleaned


def test_unmapped_php_marker_is_attribute_safe_on_hugo():
    # The Hugo html/template parser refuses `<` or `"` inside attribute
    # values, so any unmapped-PHP marker that ends up in an attribute
    # value must render to something parser-safe. A Go-template comment
    # (`{{/* … */}}`) is expanded to the empty string before html/template
    # tokenises the HTML, so it's safe anywhere in the output.
    php = '<div class="<?php weird_fn(); ?>">hi</div>'
    out, _ = transpile_template(php, "hugo")
    import re as _re
    m = _re.search(r'class="([^"]*)"', out)
    assert m is not None
    value = m.group(1)
    assert "<" not in value
    assert '"' not in value
    assert "wp2static unmapped" in value
    # The marker must use Go's template-comment syntax so Hugo renders it
    # to an empty string at build time.
    assert value.startswith("{{/*")
    assert value.endswith("*/}}")


def test_unmapped_php_marker_is_attribute_safe_on_jekyll():
    # Liquid's comment tag renders to the empty string, so a `{% comment %}`
    # marker is safe in any HTML context — including inside an attribute
    # value where a raw `<!-- -->` wrapper would otherwise leave literal
    # comment characters in the rendered HTML.
    php = '<div class="<?php weird_fn(); ?>">hi</div>'
    out, _ = transpile_template(php, "jekyll")
    import re as _re
    m = _re.search(r'class="([^"]*)"', out)
    assert m is not None
    value = m.group(1)
    assert value.startswith("{% comment %}")
    assert value.endswith("{% endcomment %}")
    assert "wp2static unmapped" in value


def test_stub_missing_includes_jekyll(tmp_path: Path):
    # A layout references two includes; one already exists, the other must
    # be stubbed out so Jekyll can build.
    (tmp_path / "_layouts").mkdir()
    (tmp_path / "_layouts" / "home.html").write_text(
        "{% include searchform.html %}\n"
        "{% include templates/sidebars/sidebar-left.html %}\n",
        encoding="utf-8",
    )
    (tmp_path / "_includes" / "templates" / "sidebars").mkdir(parents=True)
    (tmp_path / "_includes" / "templates" / "sidebars" / "sidebar-left.html").write_text(
        "real sidebar", encoding="utf-8",
    )
    count = _stub_missing_includes(tmp_path, "demo", "jekyll")
    assert count == 1
    stub = tmp_path / "_includes" / "searchform.html"
    assert stub.is_file()
    body = stub.read_text(encoding="utf-8")
    assert "stub for missing include" in body
    # The stub must render to empty — it might be included inside an
    # attribute value, so a raw `<!-- -->` marker would leak into output.
    assert body.startswith("{% comment %}")
    assert "{% endcomment %}" in body
    # The real one is not overwritten.
    real = (tmp_path / "_includes" / "templates" / "sidebars" / "sidebar-left.html")
    assert real.read_text(encoding="utf-8") == "real sidebar"


def test_stub_missing_includes_hugo(tmp_path: Path):
    # Mix both quoting styles: the theme transpiler emits backtick-quoted
    # partial paths (so string literals don't collide with HTML `"…"`
    # attribute quotes), but hand-written partials sometimes use double
    # quotes. The stubber must see both forms.
    layouts = tmp_path / "themes" / "demo" / "layouts"
    layouts.mkdir(parents=True)
    (layouts / "_default").mkdir()
    (layouts / "_default" / "single.html").write_text(
        '{{ partial "header.html" . }}\n'
        "{{ partial `searchform.html` . }}\n",
        encoding="utf-8",
    )
    count = _stub_missing_includes(tmp_path, "demo", "hugo")
    assert count == 2
    for name in ("header.html", "searchform.html"):
        stub = layouts / "partials" / name
        assert stub.is_file(), name
        body = stub.read_text(encoding="utf-8")
        assert body.startswith("{{/*")
        assert "*/}}" in body


def test_transpile_preserves_balanced_control_flow():
    php = (
        "<?php if (have_posts()) : while (have_posts()) : the_post(); ?>\n"
        "<h2><?php the_title(); ?></h2>\n"
        "<?php endwhile; endif; ?>"
    )
    out, _ = transpile_template(php, "jekyll")
    # Balanced open + close are preserved; nothing is dropped.
    assert "wp2static dropped orphan" not in out
    assert "{% for page in paginator.posts %}" in out
    assert "{% endfor %}" in out
