"""Tests for the site emitter (front matter, index pages, output layout)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from wp2static.emit import EmitOptions, emit
from wp2static.wpdata import Post, Site


def _make_site(**overrides) -> Site:
    home = Post(
        post_id=1, post_type="page", title="Home",
        slug="home", content_html="<p>welcome</p>", excerpt="",
        date=datetime(2024, 1, 1), modified=datetime(2024, 1, 1),
        status="publish",
    )
    about = Post(
        post_id=2, post_type="page", title="About",
        slug="about", content_html="<p>about us</p>", excerpt="",
        date=datetime(2024, 1, 2), modified=datetime(2024, 1, 2),
        status="publish",
    )
    first = Post(
        post_id=3, post_type="post", title="First",
        slug="first-post", content_html="<p>hello</p>", excerpt="",
        date=datetime(2024, 1, 3), modified=datetime(2024, 1, 3),
        status="publish",
    )
    site = Site(
        posts=[first], pages=[home, about], attachments={},
        base_url="https://example.com", site_name="Example",
        site_description="An example site",
    )
    for k, v in overrides.items():
        setattr(site, k, v)
    return site


@pytest.mark.parametrize("target,expected_path", [
    ("jekyll", "index.html"),
    ("hugo", "content/_index.html"),
])
def test_emit_writes_index(tmp_path: Path, target: str, expected_path: str):
    site = _make_site()
    stats = emit(site, EmitOptions(
        out_dir=tmp_path, target=target, install_templates=False,
    ))
    index = tmp_path / expected_path
    assert index.is_file()
    assert stats["indexes_written"] == 1
    body = index.read_text(encoding="utf-8")
    assert "Example" in body  # site_name used as title fallback


def test_hugo_also_writes_posts_section_index(tmp_path: Path):
    site = _make_site()
    emit(site, EmitOptions(
        out_dir=tmp_path, target="hugo", install_templates=False,
    ))
    posts_idx = tmp_path / "content" / "posts" / "_index.html"
    assert posts_idx.is_file()
    assert "Posts" in posts_idx.read_text(encoding="utf-8")


def test_front_page_replaces_index_and_suppresses_page_file(tmp_path: Path):
    site = _make_site(show_on_front="page", page_on_front=1)
    stats = emit(site, EmitOptions(
        out_dir=tmp_path, target="hugo", install_templates=False,
    ))
    index = (tmp_path / "content" / "_index.html").read_text(encoding="utf-8")
    assert "welcome" in index
    assert "Home" in index
    # The "Home" page must not also land under content/home.html
    assert not (tmp_path / "content" / "home.html").exists()
    # The other page should still be emitted normally.
    assert (tmp_path / "content" / "about.html").is_file()
    # pages count reflects the suppressed front page.
    assert stats["pages"] == 1


def test_jekyll_front_page_uses_home_layout(tmp_path: Path):
    site = _make_site(show_on_front="page", page_on_front=1)
    emit(site, EmitOptions(
        out_dir=tmp_path, target="jekyll", install_templates=False,
    ))
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "layout: home" in index
    assert "welcome" in index
    assert not (tmp_path / "home.html").exists()
