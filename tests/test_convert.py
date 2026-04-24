"""Tests for HTML content transforms."""

from __future__ import annotations

from wp2static.convert import (
    clean_content,
    extract_upload_paths,
    resolve_galleries,
    rewrite_urls,
    strip_shortcodes,
    wpautop,
)
from wp2static.plugins.final_tiles import _strip_thumbnail_suffix
from wp2static.wpdata import Attachment, Gallery


def test_wpautop_wraps_plain_lines():
    out = wpautop("hello world\n\nsecond paragraph")
    assert out == "<p>hello world</p>\n\n<p>second paragraph</p>"


def test_wpautop_respects_existing_paragraphs():
    html = "<p>already wrapped</p>"
    assert wpautop(html) == html


def test_wpautop_skips_ssg_directives():
    html = '{{< gallery images="/a.jpg" >}}\n\nnext paragraph'
    out = wpautop(html)
    assert '<p>{{< gallery' not in out
    assert out.startswith('{{< gallery')
    assert "<p>next paragraph</p>" in out


def test_strip_shortcodes_drops_unknown_and_caption_to_figure():
    html = '[caption id="x"]<img src="a.jpg">My caption[/caption] [junk]rest'
    out = strip_shortcodes(html)
    assert '<figure class="wp-caption">' in out
    assert '<figcaption>My caption</figcaption>' in out
    assert "[junk]" not in out


def test_rewrite_urls_maps_uploads_and_root():
    html = (
        'see <a href="https://example.com/about">about</a> and '
        '<img src="https://example.com/wp-content/uploads/2024/01/x.jpg">'
    )
    out = rewrite_urls(html, "https://example.com", "/uploads")
    assert 'href="/about"' in out
    assert 'src="/uploads/2024/01/x.jpg"' in out


def test_extract_upload_paths_finds_references():
    html = (
        'nope https://other.com/wp-content/uploads/x.jpg '
        'yes //example.com/wp-content/uploads/2024/a.jpg '
        'yes https://example.com/wp-content/uploads/b.png'
    )
    paths = extract_upload_paths(html, "https://example.com")
    assert sorted(paths) == ["2024/a.jpg", "b.png"]


def test_strip_thumbnail_suffix():
    assert _strip_thumbnail_suffix("foo-150x150.jpg") == "foo.jpg"
    assert _strip_thumbnail_suffix("foo.jpg") == "foo.jpg"
    assert _strip_thumbnail_suffix("a-1x2-300x200.png") == "a-1x2.png"


def test_resolve_galleries_wp_gallery_to_hugo_directive():
    attachments = {
        5: Attachment(post_id=5, title="", url="", file="a.jpg"),
        6: Attachment(post_id=6, title="", url="", file="b.jpg"),
    }
    html = '[gallery ids="5,6"]'
    out = resolve_galleries(
        html,
        attachments=attachments,
        finaltiles_by_id={},
        finaltiles_by_slug={},
        base_url="https://example.com",
        uploads_prefix="/uploads",
        target="hugo",
    )
    assert '{{< gallery images="/uploads/a.jpg,/uploads/b.jpg" >}}' in out


def test_resolve_galleries_finaltiles_by_slug_falls_back_to_urls():
    gallery = Gallery(
        gallery_id=1,
        slug="homepage",
        name="Homepage",
        image_ids=[],  # no WP attachments → fall back to stored URLs
        image_urls=["https://example.com/wp-content/uploads/x-150x150.jpg"],
    )
    html = '[FinalTilesGallery slug="homepage"]'
    out = resolve_galleries(
        html,
        attachments={},
        finaltiles_by_id={},
        finaltiles_by_slug={"homepage": gallery},
        base_url="https://example.com",
        uploads_prefix="/uploads",
        target="jekyll",
    )
    # Thumbnail suffix stripped + URL rewritten to uploads-prefix.
    assert '/uploads/x.jpg' in out
    assert '{% include gallery.html' in out
    assert 'title="Homepage"' in out


def test_clean_content_pipeline_preserves_gallery_directive():
    attachments = {7: Attachment(post_id=7, title="", url="", file="z.jpg")}
    html = 'before\n\n[gallery ids="7"]\n\nafter [something]stripped[/something]'
    out = clean_content(
        html,
        base_url="https://example.com",
        attachments=attachments,
        target="hugo",
    )
    assert "<p>before</p>" in out
    assert '{{< gallery images="/uploads/z.jpg" >}}' in out
    assert "<p>after stripped</p>" in out or "<p>after </p>" in out
