"""Tests for the WordPress schema loader."""

from __future__ import annotations

from datetime import datetime

from wp2static.wpdata import _parse_dt, load, wp_unslash


def test_wp_unslash_reverses_addslashes():
    assert wp_unslash(r'a\"b\'c\\d\0e') == "a\"b'c\\d\x00e"


def test_wp_unslash_leaves_other_backslashes():
    # \n is not one of the four PHP-escaped characters — must survive.
    assert wp_unslash(r"keep\n and \t too") == r"keep\n and \t too"


def test_parse_dt_handles_zero_date():
    assert _parse_dt("0000-00-00 00:00:00") == datetime(1970, 1, 1)


def test_parse_dt_handles_garbage():
    assert _parse_dt("not-a-date") == datetime(1970, 1, 1)


def test_load_classifies_posts_pages_attachments(tiny_dump):
    site = load(tiny_dump)
    assert [p.slug for p in site.posts] == ["first-post"]
    assert [p.slug for p in site.pages] == ["about"]
    assert 20 in site.attachments
    assert site.attachments[20].file == "2024/01/hero.jpg"


def test_load_reads_options_and_featured_image(tiny_dump):
    site = load(tiny_dump)
    assert site.base_url == "https://example.com"
    assert site.site_name == "Hello"
    assert site.site_description == "A site"
    assert site.active_theme == "kale"
    first = site.posts[0]
    assert first.featured_image is not None
    assert first.featured_image.file == "2024/01/hero.jpg"


def test_load_ignores_unwanted_tables(tiny_dump):
    # wp_comments is in the dump but the loader must not crash and must not
    # expose them anywhere.
    site = load(tiny_dump)
    assert not hasattr(site, "comments")
