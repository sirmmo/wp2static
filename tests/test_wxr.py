"""Tests for the WXR (WordPress XML export) loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from wp2static.wxr import load


@pytest.fixture
def tiny_wxr(tmp_path: Path) -> Path:
    xml = tmp_path / "export.xml"
    xml.write_text(textwrap.dedent(
        """\
        <?xml version="1.0" encoding="UTF-8" ?>
        <rss version="2.0"
            xmlns:excerpt="http://wordpress.org/export/1.2/excerpt/"
            xmlns:content="http://purl.org/rss/1.0/modules/content/"
            xmlns:wp="http://wordpress.org/export/1.2/">
        <channel>
          <title>Hello</title>
          <description>A site</description>
          <wp:base_site_url>https://example.com</wp:base_site_url>
          <wp:base_blog_url>https://example.com</wp:base_blog_url>

          <wp:category>
            <wp:term_id>1</wp:term_id>
            <wp:category_nicename><![CDATA[news]]></wp:category_nicename>
            <wp:cat_name><![CDATA[News]]></wp:cat_name>
          </wp:category>
          <wp:tag>
            <wp:term_id>2</wp:term_id>
            <wp:tag_slug><![CDATA[blockchain]]></wp:tag_slug>
            <wp:tag_name><![CDATA[Blockchain]]></wp:tag_name>
          </wp:tag>
          <wp:term>
            <wp:term_id>5</wp:term_id>
            <wp:term_taxonomy>nav_menu</wp:term_taxonomy>
            <wp:term_slug><![CDATA[primary]]></wp:term_slug>
            <wp:term_name><![CDATA[Primary]]></wp:term_name>
          </wp:term>

          <item>
            <title>First post</title>
            <guid isPermaLink="false">https://example.com/?p=10</guid>
            <content:encoded><![CDATA[Hello <b>world</b>]]></content:encoded>
            <excerpt:encoded><![CDATA[]]></excerpt:encoded>
            <wp:post_id>10</wp:post_id>
            <wp:post_date><![CDATA[2024-01-01 10:00:00]]></wp:post_date>
            <wp:post_modified><![CDATA[2024-01-02 10:00:00]]></wp:post_modified>
            <wp:post_name><![CDATA[first-post]]></wp:post_name>
            <wp:status><![CDATA[publish]]></wp:status>
            <wp:post_parent>0</wp:post_parent>
            <wp:menu_order>0</wp:menu_order>
            <wp:post_type><![CDATA[post]]></wp:post_type>
            <category domain="category" nicename="news"><![CDATA[News]]></category>
            <category domain="post_tag" nicename="blockchain"><![CDATA[Blockchain]]></category>
            <wp:postmeta>
              <wp:meta_key><![CDATA[_thumbnail_id]]></wp:meta_key>
              <wp:meta_value><![CDATA[20]]></wp:meta_value>
            </wp:postmeta>
          </item>

          <item>
            <title>About</title>
            <guid isPermaLink="false">https://example.com/about</guid>
            <content:encoded><![CDATA[About body.]]></content:encoded>
            <excerpt:encoded><![CDATA[]]></excerpt:encoded>
            <wp:post_id>11</wp:post_id>
            <wp:post_date><![CDATA[2024-02-01 10:00:00]]></wp:post_date>
            <wp:post_modified><![CDATA[2024-02-01 10:00:00]]></wp:post_modified>
            <wp:post_name><![CDATA[about]]></wp:post_name>
            <wp:status><![CDATA[publish]]></wp:status>
            <wp:post_parent>0</wp:post_parent>
            <wp:menu_order>0</wp:menu_order>
            <wp:post_type><![CDATA[page]]></wp:post_type>
          </item>

          <item>
            <title>hero.jpg</title>
            <guid isPermaLink="false">https://example.com/?attachment_id=20</guid>
            <content:encoded><![CDATA[]]></content:encoded>
            <excerpt:encoded><![CDATA[]]></excerpt:encoded>
            <wp:post_id>20</wp:post_id>
            <wp:post_date><![CDATA[2024-01-01 10:00:00]]></wp:post_date>
            <wp:post_modified><![CDATA[2024-01-01 10:00:00]]></wp:post_modified>
            <wp:post_name><![CDATA[hero]]></wp:post_name>
            <wp:status><![CDATA[inherit]]></wp:status>
            <wp:post_parent>10</wp:post_parent>
            <wp:menu_order>0</wp:menu_order>
            <wp:post_type><![CDATA[attachment]]></wp:post_type>
            <wp:attachment_url><![CDATA[https://example.com/uploads/2024/01/hero.jpg]]></wp:attachment_url>
            <wp:postmeta>
              <wp:meta_key><![CDATA[_wp_attached_file]]></wp:meta_key>
              <wp:meta_value><![CDATA[2024/01/hero.jpg]]></wp:meta_value>
            </wp:postmeta>
          </item>

          <item>
            <title>Home</title>
            <wp:post_id>100</wp:post_id>
            <wp:post_date><![CDATA[2024-03-01 10:00:00]]></wp:post_date>
            <wp:post_modified><![CDATA[2024-03-01 10:00:00]]></wp:post_modified>
            <wp:post_name><![CDATA[home-item]]></wp:post_name>
            <wp:status><![CDATA[publish]]></wp:status>
            <wp:post_parent>0</wp:post_parent>
            <wp:menu_order>1</wp:menu_order>
            <wp:post_type><![CDATA[nav_menu_item]]></wp:post_type>
            <category domain="nav_menu" nicename="primary"><![CDATA[Primary]]></category>
            <wp:postmeta><wp:meta_key><![CDATA[_menu_item_type]]></wp:meta_key><wp:meta_value><![CDATA[custom]]></wp:meta_value></wp:postmeta>
            <wp:postmeta><wp:meta_key><![CDATA[_menu_item_menu_item_parent]]></wp:meta_key><wp:meta_value><![CDATA[0]]></wp:meta_value></wp:postmeta>
            <wp:postmeta><wp:meta_key><![CDATA[_menu_item_object_id]]></wp:meta_key><wp:meta_value><![CDATA[100]]></wp:meta_value></wp:postmeta>
            <wp:postmeta><wp:meta_key><![CDATA[_menu_item_object]]></wp:meta_key><wp:meta_value><![CDATA[custom]]></wp:meta_value></wp:postmeta>
            <wp:postmeta><wp:meta_key><![CDATA[_menu_item_url]]></wp:meta_key><wp:meta_value><![CDATA[https://example.com/]]></wp:meta_value></wp:postmeta>
          </item>

          <item>
            <title></title>
            <wp:post_id>101</wp:post_id>
            <wp:post_date><![CDATA[2024-03-01 10:00:00]]></wp:post_date>
            <wp:post_modified><![CDATA[2024-03-01 10:00:00]]></wp:post_modified>
            <wp:post_name><![CDATA[about-item]]></wp:post_name>
            <wp:status><![CDATA[publish]]></wp:status>
            <wp:post_parent>0</wp:post_parent>
            <wp:menu_order>2</wp:menu_order>
            <wp:post_type><![CDATA[nav_menu_item]]></wp:post_type>
            <category domain="nav_menu" nicename="primary"><![CDATA[Primary]]></category>
            <wp:postmeta><wp:meta_key><![CDATA[_menu_item_type]]></wp:meta_key><wp:meta_value><![CDATA[post_type]]></wp:meta_value></wp:postmeta>
            <wp:postmeta><wp:meta_key><![CDATA[_menu_item_object_id]]></wp:meta_key><wp:meta_value><![CDATA[11]]></wp:meta_value></wp:postmeta>
            <wp:postmeta><wp:meta_key><![CDATA[_menu_item_object]]></wp:meta_key><wp:meta_value><![CDATA[page]]></wp:meta_value></wp:postmeta>
          </item>
        </channel>
        </rss>
        """
    ), encoding="utf-8")
    return xml


def test_load_reads_site_header(tiny_wxr):
    site = load(tiny_wxr)
    assert site.base_url == "https://example.com"
    assert site.site_name == "Hello"
    assert site.site_description == "A site"
    # WXR has no wp_options; the active theme is unknown by design.
    assert site.active_theme == ""


def test_load_classifies_posts_pages_attachments(tiny_wxr):
    site = load(tiny_wxr)
    assert [p.slug for p in site.posts] == ["first-post"]
    assert [p.slug for p in site.pages] == ["about"]
    assert 20 in site.attachments
    assert site.attachments[20].file == "2024/01/hero.jpg"
    # Attachment URL prefers <wp:attachment_url> over the <guid> so the
    # migrated site can actually find the file on disk.
    assert site.attachments[20].url == "https://example.com/uploads/2024/01/hero.jpg"


def test_load_resolves_featured_image_and_terms(tiny_wxr):
    site = load(tiny_wxr)
    first = site.posts[0]
    assert first.featured_image is not None
    assert first.featured_image.file == "2024/01/hero.jpg"
    assert [t.slug for t in first.categories] == ["news"]
    assert [t.slug for t in first.tags] == ["blockchain"]


def test_load_builds_nav_menus_with_linked_titles(tiny_wxr):
    site = load(tiny_wxr)
    assert len(site.menus) == 1
    menu = site.menus[0]
    assert menu.slug == "primary"
    labels = [it.label for it in menu.items]
    # nav_menu_item 101 has an empty <title> — fall back to the linked page.
    assert labels == ["Home", "About"]
    assert menu.items[0].url == "/"
    assert menu.items[1].url == "/about/"


def test_load_skips_draft_posts_and_nav_items_only_published(tmp_path):
    xml = tmp_path / "drafts.xml"
    xml.write_text(textwrap.dedent(
        """\
        <?xml version="1.0"?>
        <rss version="2.0" xmlns:wp="http://wordpress.org/export/1.2/"
             xmlns:content="http://purl.org/rss/1.0/modules/content/"
             xmlns:excerpt="http://wordpress.org/export/1.2/excerpt/">
        <channel>
          <title>T</title><description>D</description>
          <wp:base_site_url>https://x.test</wp:base_site_url>
          <item>
            <title>Draft</title>
            <content:encoded><![CDATA[]]></content:encoded>
            <excerpt:encoded><![CDATA[]]></excerpt:encoded>
            <wp:post_id>1</wp:post_id>
            <wp:post_date><![CDATA[2024-01-01 10:00:00]]></wp:post_date>
            <wp:post_modified><![CDATA[2024-01-01 10:00:00]]></wp:post_modified>
            <wp:post_name><![CDATA[draft]]></wp:post_name>
            <wp:status><![CDATA[draft]]></wp:status>
            <wp:post_type><![CDATA[post]]></wp:post_type>
          </item>
        </channel></rss>
        """
    ), encoding="utf-8")
    site = load(xml)
    assert site.posts == []
    assert site.pages == []


def test_load_tolerates_truncated_file(tmp_path):
    # Simulate the real-world 'WP critical-error mid-export' shape: a
    # published post followed by garbage that can never close correctly.
    xml = tmp_path / "broken.xml"
    xml.write_text(textwrap.dedent(
        """\
        <?xml version="1.0"?>
        <rss version="2.0" xmlns:wp="http://wordpress.org/export/1.2/"
             xmlns:content="http://purl.org/rss/1.0/modules/content/"
             xmlns:excerpt="http://wordpress.org/export/1.2/excerpt/">
        <channel>
          <title>T</title><description>D</description>
          <wp:base_site_url>https://x.test</wp:base_site_url>
          <item>
            <title>Good</title>
            <content:encoded><![CDATA[hi]]></content:encoded>
            <excerpt:encoded><![CDATA[]]></excerpt:encoded>
            <wp:post_id>7</wp:post_id>
            <wp:post_date><![CDATA[2024-01-01 10:00:00]]></wp:post_date>
            <wp:post_modified><![CDATA[2024-01-01 10:00:00]]></wp:post_modified>
            <wp:post_name><![CDATA[good]]></wp:post_name>
            <wp:status><![CDATA[publish]]></wp:status>
            <wp:post_type><![CDATA[post]]></wp:post_type>
          </item>
          <item>
            <wp:postmeta>
              <wp:meta_key><![CDATA[broken]]></wp:meta_key>
              <wp:meta_value><!DOCTYPE html>
        """
    ), encoding="utf-8")
    site = load(xml)
    assert [p.slug for p in site.posts] == ["good"]
