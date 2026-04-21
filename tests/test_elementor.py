"""Tests for the Elementor renderer."""

from __future__ import annotations

import json
from dataclasses import dataclass

from wp2static import elementor


@dataclass
class _StubPost:
    post_id: int = 1
    elementor_mode: str = "builder"
    elementor_data: str = ""


def test_has_builder_content_requires_mode_and_data():
    assert not elementor.has_builder_content(_StubPost(elementor_mode="", elementor_data="[]"))
    assert not elementor.has_builder_content(_StubPost(elementor_mode="builder", elementor_data=""))
    assert elementor.has_builder_content(_StubPost(elementor_data="[]"))


def test_render_heading_honors_header_size():
    out = elementor._render_heading({"header_size": "h3", "title": "Hi <you>"})
    assert out.startswith("<h3 ")
    assert "Hi &lt;you&gt;" in out


def test_render_heading_with_link():
    out = elementor._render_heading({
        "title": "Click", "link": {"url": "https://example.com"}
    })
    assert '<a href="https://example.com">Click</a>' in out


def test_render_image_without_url_emits_comment():
    assert "<!--" in elementor._render_image({"image": {}})


def test_render_image_with_caption_wraps_in_figure():
    out = elementor._render_image({
        "image": {"url": "/x.jpg"},
        "caption": "Hello",
        "caption_source": "custom",
    })
    assert '<figure class="elementor-image">' in out
    assert '<figcaption>Hello</figcaption>' in out


def test_unknown_widget_becomes_comment():
    tree = [{
        "elType": "widget",
        "widgetType": "some-new-widget",
        "settings": {},
    }]
    post = _StubPost(elementor_data=json.dumps(tree))
    out = elementor.render(post)
    assert "unsupported elementor widget 'some-new-widget'" in out


def test_render_section_wraps_tree():
    tree = [{
        "elType": "section",
        "elements": [{
            "elType": "column",
            "settings": {"_column_size": 50},
            "elements": [{
                "elType": "widget",
                "widgetType": "heading",
                "settings": {"title": "Welcome"},
            }],
        }],
    }]
    post = _StubPost(elementor_data=json.dumps(tree))
    out = elementor.render(post)
    assert '<section class="elementor-section">' in out
    assert 'flex-basis:50%' in out
    assert ">Welcome</h2>" in out


def test_render_handles_wp_slashed_json():
    # Simulate the wp_slash() double-escaping that survives SQL parsing.
    tree = [{
        "elType": "widget",
        "widgetType": "html",
        "settings": {"html": '<b>"quoted"</b>'},
    }]
    raw = json.dumps(tree).replace('"', r'\"')
    post = _StubPost(elementor_data=raw)
    out = elementor.render(post)
    assert '<b>"quoted"</b>' in out


def test_render_empty_for_invalid_json():
    post = _StubPost(elementor_data="not json")
    assert elementor.render(post) == ""
