"""Tests for the Elementor adapter's content rendering."""

from __future__ import annotations

import json
from dataclasses import dataclass

from wp2static.plugins import ElementorAdapter, adapter_for_post
from wp2static.plugins import elementor as elementor_mod


@dataclass
class _StubPost:
    post_id: int = 1
    elementor_mode: str = "builder"
    elementor_data: str = ""


def test_adapter_opts_in_when_mode_and_data_present():
    adapter = ElementorAdapter()
    assert not adapter.replaces_post_content(
        _StubPost(elementor_mode="", elementor_data="[]"),
    )
    assert not adapter.replaces_post_content(
        _StubPost(elementor_mode="builder", elementor_data=""),
    )
    assert adapter.replaces_post_content(_StubPost(elementor_data="[]"))


def test_registry_dispatches_elementor_posts():
    # A builder post should route through ElementorAdapter via the registry.
    post = _StubPost(elementor_data="[]")
    adapter = adapter_for_post(post)
    assert isinstance(adapter, ElementorAdapter)
    assert adapter_for_post(_StubPost(elementor_mode="")) is None


def test_render_heading_honors_header_size():
    out = elementor_mod._render_heading(
        {"header_size": "h3", "title": "Hi <you>"},
    )
    assert out.startswith("<h3 ")
    assert "Hi &lt;you&gt;" in out


def test_render_heading_with_link():
    out = elementor_mod._render_heading({
        "title": "Click", "link": {"url": "https://example.com"},
    })
    assert '<a href="https://example.com">Click</a>' in out


def test_render_image_without_url_emits_comment():
    assert "<!--" in elementor_mod._render_image({"image": {}})


def test_render_image_with_caption_wraps_in_figure():
    out = elementor_mod._render_image({
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
    out = ElementorAdapter().render_post_content(post)
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
    out = ElementorAdapter().render_post_content(post)
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
    out = ElementorAdapter().render_post_content(post)
    assert '<b>"quoted"</b>' in out


def test_render_empty_for_invalid_json():
    post = _StubPost(elementor_data="not json")
    assert ElementorAdapter().render_post_content(post) == ""
