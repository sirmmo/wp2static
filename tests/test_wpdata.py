"""Tests for the WordPress schema loader."""

from __future__ import annotations

from datetime import datetime

from wp2static.wpdata import (
    _parse_dt, _parse_nav_menu_locations, _strip_base, load, wp_unslash,
)


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


def test_parse_nav_menu_locations_extracts_slot_mapping():
    blob = (
        'a:2:{s:10:"custom_css";s:0:"";'
        's:18:"nav_menu_locations";a:2:'
        '{s:7:"primary";i:5;s:6:"footer";i:9;}}'
    )
    assert _parse_nav_menu_locations(blob) == {"primary": 5, "footer": 9}


def test_strip_base_rewrites_matching_scheme():
    assert _strip_base("https://foo.com/about/", "https://foo.com") == "/about/"


def test_strip_base_tolerates_http_vs_https_mismatch():
    # WP often stores a http:// menu URL after the site moved to https
    # (and vice versa). Either scheme should resolve as internal.
    assert _strip_base("http://foo.com/about/", "https://foo.com") == "/about/"
    assert _strip_base("https://foo.com", "http://foo.com") == "/"


def test_strip_base_leaves_external_urls_alone():
    assert (_strip_base("https://other.com/x", "https://foo.com")
            == "https://other.com/x")


def test_parse_nav_menu_locations_missing_returns_empty():
    assert _parse_nav_menu_locations("") == {}
    assert _parse_nav_menu_locations("a:0:{}") == {}


def test_load_builds_nav_menus_with_hierarchy(tiny_dump):
    site = load(tiny_dump)
    assert len(site.menus) == 1
    menu = site.menus[0]
    assert menu.name == "Primary"
    assert menu.slug == "primary-menu"
    labels = [it.label for it in menu.items]
    assert labels == ["Home", "About"]
    about = menu.items[1]
    assert [c.label for c in about.children] == ["First post"]


def test_load_resolves_menu_item_urls(tiny_dump):
    site = load(tiny_dump)
    menu = site.menus[0]
    home, about = menu.items
    # custom URLs that point back at the site get the base stripped so they
    # survive the migration.
    assert home.url == "/"
    # post_type=page → /{slug}/
    assert about.url == "/about/"
    # post_type=post → /posts/{slug}/
    assert about.children[0].url == "/posts/first-post/"


def test_load_falls_back_to_linked_title_when_label_empty(tiny_dump):
    site = load(tiny_dump)
    about = site.menus[0].items[1]
    # The nav_menu_item row has an empty post_title, so the loader must
    # reach into the linked page (ID 11) for the label.
    assert about.label == "About"
    assert about.target == "_blank"


def test_load_reads_menu_locations_from_theme_mods(tiny_dump):
    site = load(tiny_dump)
    assert site.menu_locations == {"header": "primary-menu"}
