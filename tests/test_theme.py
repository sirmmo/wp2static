"""Tests for the theme scaffolder."""

from __future__ import annotations

from pathlib import Path

from wp2static.theme import (
    _hugo_layout_for,
    _jekyll_layout_for,
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


def test_hugo_layout_for_puts_things_under_theme_root():
    assert _hugo_layout_for("kale", Path("single.php")) == Path(
        "themes/kale/layouts/_default/single.html"
    )
    assert _hugo_layout_for("kale", Path("header.php")) == Path(
        "themes/kale/layouts/partials/header.html"
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


def test_transpile_expands_get_header_and_template_parts():
    php = "<?php get_header(); ?>\n<?php get_template_part('content', 'single'); ?>"
    jekyll, _ = transpile_template(php, "jekyll")
    assert "{% include header.html %}" in jekyll
    assert "{% include content-single.html %}" in jekyll
    hugo, _ = transpile_template(php, "hugo")
    assert '{{ partial "header.html" . }}' in hugo
    assert '{{ partial "content-single.html" . }}' in hugo


def test_transpile_marks_unmapped_calls():
    php = "<?php bard_options('layout'); ?>"
    out, unmapped = transpile_template(php, "jekyll")
    assert "wp2static: unmapped PHP" in out
    assert any("bard_options" in call for call in unmapped)
