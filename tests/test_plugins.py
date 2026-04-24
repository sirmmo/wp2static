"""Tests for the plugin adapter registry and asset import step."""

from __future__ import annotations

from pathlib import Path

import pytest

from wp2static.plugins import (
    FinalTilesAdapter,
    PluginAdapter,
    RenderContext,
    adapter_for_shortcode,
    get_adapter,
    import_plugins,
    list_adapters,
)
from wp2static.targets import get_target
from wp2static.wpdata import Attachment, Gallery


# ---------------------------------------------------------------------------
# Fixture: a fake wp-content/plugins/ tree with a realistic file layout.
# ---------------------------------------------------------------------------

_PLUGIN_HEADER = """\
<?php
/*
Plugin Name: Final Tiles Grid Gallery Lite
Plugin URI: https://example.com/ft
Description: Grid gallery plugin with a [FinalTilesGallery] shortcode.
Version: 1.2.3
Author: Greentreelabs
Author URI: https://example.com/author
License: GPLv2
Text Domain: final-tiles-grid-gallery-lite
*/

add_shortcode('FinalTilesGallery', 'ft_render_gallery');
add_shortcode('FinalTilesButton', 'ft_render_button');
"""


def _make_plugins_tree(root: Path) -> Path:
    """Write a minimal plugins/ tree: one known plugin + one unknown one."""
    ft = root / "final-tiles-grid-gallery-lite"
    (ft / "css").mkdir(parents=True)
    (ft / "js").mkdir(parents=True)
    (ft / "images").mkdir(parents=True)
    (ft / "final-tiles-grid-gallery-lite.php").write_text(
        _PLUGIN_HEADER, encoding="utf-8",
    )
    (ft / "css" / "ftg.css").write_text("/* ftg */", encoding="utf-8")
    (ft / "css" / "lightbox.css").write_text("/* lightbox */", encoding="utf-8")
    (ft / "js" / "ftg.js").write_text("// ftg", encoding="utf-8")
    (ft / "images" / "spinner.gif").write_bytes(b"GIF89a")

    unknown = root / "some-random-plugin"
    (unknown / "css").mkdir(parents=True)
    (unknown / "some-random-plugin.php").write_text(
        "<?php /* Plugin Name: Random */\n", encoding="utf-8",
    )
    (unknown / "css" / "random.css").write_text("/* random */", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_exposes_known_slugs():
    slugs = list_adapters()
    assert "elementor" in slugs
    assert "final-tiles-grid-gallery-lite" in slugs


def test_get_adapter_returns_instance_for_known_slug():
    adapter = get_adapter("final-tiles-grid-gallery-lite")
    assert isinstance(adapter, FinalTilesAdapter)


def test_get_adapter_returns_none_for_unknown_slug():
    assert get_adapter("never-heard-of-it") is None


# ---------------------------------------------------------------------------
# Adapter behaviour
# ---------------------------------------------------------------------------

def test_parse_header_extracts_plugin_metadata(tmp_path: Path):
    plugins_src = _make_plugins_tree(tmp_path / "plugins")
    meta = FinalTilesAdapter().parse_header(
        plugins_src / "final-tiles-grid-gallery-lite",
    )
    assert meta.name == "Final Tiles Grid Gallery Lite"
    assert meta.version == "1.2.3"
    assert meta.author == "Greentreelabs"
    assert meta.license == "GPLv2"


def test_discover_shortcodes_finds_add_shortcode_calls(tmp_path: Path):
    plugins_src = _make_plugins_tree(tmp_path / "plugins")
    codes = FinalTilesAdapter().discover_shortcodes(
        plugins_src / "final-tiles-grid-gallery-lite",
    )
    assert "FinalTilesGallery" in codes
    # The scan must surface even shortcodes not declared on the adapter —
    # ``FinalTilesButton`` is only found by regex.
    assert "FinalTilesButton" in codes


def test_copy_assets_copies_known_subdirs(tmp_path: Path):
    plugins_src = _make_plugins_tree(tmp_path / "plugins")
    dst = tmp_path / "dst"
    n = FinalTilesAdapter().copy_assets(
        plugins_src / "final-tiles-grid-gallery-lite", dst,
    )
    assert n == 4
    assert (dst / "css" / "ftg.css").is_file()
    assert (dst / "css" / "lightbox.css").is_file()
    assert (dst / "js" / "ftg.js").is_file()
    assert (dst / "images" / "spinner.gif").is_file()
    # PHP files aren't assets — they stay behind.
    assert not (dst / "final-tiles-grid-gallery-lite.php").exists()


# ---------------------------------------------------------------------------
# import_plugins: end-to-end
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target_name,expected_root_parts", [
    ("hugo", ("static", "plugins")),
    ("jekyll", ("assets", "plugins")),
])
def test_import_plugins_writes_assets_under_target_root(
    tmp_path: Path, target_name: str, expected_root_parts: tuple[str, ...],
):
    plugins_src = _make_plugins_tree(tmp_path / "plugins")
    out_dir = tmp_path / "out"
    target = get_target(target_name)
    stats = import_plugins(plugins_src, out_dir, target)

    assert stats["imported"] == 1
    assert stats["plugins"][0]["slug"] == "final-tiles-grid-gallery-lite"
    assert stats["unknown"] == ["some-random-plugin"]

    dst = out_dir.joinpath(*expected_root_parts, "final-tiles-grid-gallery-lite")
    assert (dst / "css" / "ftg.css").is_file()
    assert (dst / "js" / "ftg.js").is_file()


def test_import_plugins_restricts_to_explicit_only_list(tmp_path: Path):
    plugins_src = _make_plugins_tree(tmp_path / "plugins")
    out_dir = tmp_path / "out"
    target = get_target("hugo")
    stats = import_plugins(
        plugins_src, out_dir, target, only=["elementor"],
    )
    # Only 'elementor' was requested — 'final-tiles-…' must not be imported,
    # even though its adapter is registered.
    assert stats["imported"] == 0
    assert not (out_dir / "static" / "plugins"
                / "final-tiles-grid-gallery-lite").exists()


def test_import_plugins_returns_empty_when_src_missing(tmp_path: Path):
    target = get_target("hugo")
    stats = import_plugins(tmp_path / "nope", tmp_path / "out", target)
    assert stats == {"imported": 0, "plugins": [], "unknown": []}


def test_import_plugins_records_shortcodes_on_each_plugin(tmp_path: Path):
    plugins_src = _make_plugins_tree(tmp_path / "plugins")
    out_dir = tmp_path / "out"
    target = get_target("hugo")
    stats = import_plugins(plugins_src, out_dir, target)
    ft = stats["plugins"][0]
    assert "FinalTilesGallery" in ft["shortcodes"]
    assert "FinalTilesButton" in ft["shortcodes"]


# ---------------------------------------------------------------------------
# finalize_theme: head partial now aggregates plugin CSS
# ---------------------------------------------------------------------------

def test_hugo_head_partial_includes_plugin_css_alongside_theme_css(
    tmp_path: Path,
):
    out_dir = tmp_path / "out"
    target = get_target("hugo")

    # Pretend theme migration already ran by planting a theme static tree.
    theme_static = target.theme_static_root(out_dir, "kale")
    theme_static.mkdir(parents=True)
    (theme_static / "style.css").write_text("/* theme */", encoding="utf-8")

    # Plant a plugin asset tree where import_plugins would put it.
    plugin_css = target.plugins_root(out_dir) / "final-tiles-grid-gallery-lite" / "css"
    plugin_css.mkdir(parents=True)
    (plugin_css / "ftg.css").write_text("/* ftg */", encoding="utf-8")

    result = target.finalize_theme(out_dir, "kale")
    assert result["head_css_links"] == 2

    partial = (out_dir / "themes" / "kale" / "layouts" / "partials"
               / "wp2static-head.html").read_text(encoding="utf-8")
    # Plugin CSS comes first (WordPress load order: plugins before theme).
    assert partial.index("/plugins/final-tiles-grid-gallery-lite/css/ftg.css") \
        < partial.index("/style.css")


# ---------------------------------------------------------------------------
# Shortcode resolution — FinalTilesAdapter
# ---------------------------------------------------------------------------

def _ctx(
    target_name: str = "hugo",
    attachments: dict | None = None,
    by_id: dict | None = None,
    by_slug: dict | None = None,
    base_url: str = "https://example.com",
) -> RenderContext:
    return RenderContext(
        attachments=attachments or {},
        galleries_by_id=by_id or {},
        galleries_by_slug=by_slug or {},
        base_url=base_url,
        uploads_prefix="/uploads",
        target=get_target(target_name),
    )


def test_finaltiles_adapter_claims_its_shortcode():
    adapter = adapter_for_shortcode("FinalTilesGallery")
    assert isinstance(adapter, FinalTilesAdapter)
    # Case-insensitive — authors vary the casing in content.
    assert adapter_for_shortcode("finaltilesgallery") is adapter
    assert adapter_for_shortcode("gallery") is None


def test_finaltiles_render_prefers_attachments_over_stored_urls():
    gallery = Gallery(
        gallery_id=1, slug="home", name="Home",
        image_ids=[10, 11],
        image_urls=["https://example.com/wp-content/uploads/skip-150x150.jpg"],
    )
    attachments = {
        10: Attachment(post_id=10, title="", url="", file="full-a.jpg"),
        11: Attachment(post_id=11, title="", url="", file="full-b.jpg"),
    }
    out = FinalTilesAdapter().render_shortcode(
        "FinalTilesGallery", {"id": "1"},
        _ctx(by_id={1: gallery}, attachments=attachments),
    )
    assert "/uploads/full-a.jpg,/uploads/full-b.jpg" in out
    assert "skip" not in out


def test_finaltiles_render_falls_back_to_stored_urls_when_no_ids():
    gallery = Gallery(
        gallery_id=2, slug="fallback", name="Fallback",
        image_ids=[], image_urls=[
            "https://example.com/wp-content/uploads/x-300x200.jpg",
        ],
    )
    out = FinalTilesAdapter().render_shortcode(
        "FinalTilesGallery", {"slug": "fallback"},
        _ctx(target_name="jekyll", by_slug={"fallback": gallery}),
    )
    # Thumbnail suffix stripped + URL rewritten to uploads prefix.
    assert "/uploads/x.jpg" in out
    assert 'title="Fallback"' in out


def test_finaltiles_render_missing_gallery_returns_empty():
    out = FinalTilesAdapter().render_shortcode(
        "FinalTilesGallery", {"id": "99"}, _ctx(),
    )
    assert out == ""


# ---------------------------------------------------------------------------
# Base-class smoke test: an empty adapter still functions for unknown plugins
# you might register manually.
# ---------------------------------------------------------------------------

def test_base_adapter_can_be_subclassed_without_overrides(tmp_path: Path):
    class _NoopAdapter(PluginAdapter):
        slug = "noop"

    plugin_dir = tmp_path / "noop"
    (plugin_dir / "css").mkdir(parents=True)
    (plugin_dir / "css" / "a.css").write_text("/* a */", encoding="utf-8")

    adapter = _NoopAdapter()
    assert adapter.discover_shortcodes(plugin_dir) == []
    copied = adapter.copy_assets(plugin_dir, tmp_path / "out")
    assert copied == 1
