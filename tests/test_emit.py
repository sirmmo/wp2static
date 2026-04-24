"""Tests for the site emitter (front matter, index pages, output layout)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from wp2static.emit import EmitOptions, emit
from wp2static.wpdata import NavMenu, NavMenuItem, Post, Site


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


def test_hugo_writes_site_config_with_wp_options(tmp_path: Path):
    site = _make_site(active_theme="kale")
    stats = emit(site, EmitOptions(
        out_dir=tmp_path, target="hugo", install_templates=False,
    ))
    cfg = tmp_path / "hugo.toml"
    assert cfg.is_file()
    assert stats["config_written"] == 1
    body = cfg.read_text(encoding="utf-8")
    assert 'baseURL = "https://example.com/"' in body
    assert 'title = "Example"' in body
    # Primary theme + fallback so missing kinds render via wp2static-defaults.
    assert 'theme = ["kale", "wp2static-defaults"]' in body
    assert 'description = "An example site"' in body


def test_jekyll_writes_site_config_with_wp_options(tmp_path: Path):
    site = _make_site()
    stats = emit(site, EmitOptions(
        out_dir=tmp_path, target="jekyll", install_templates=False,
    ))
    cfg = tmp_path / "_config.yml"
    assert cfg.is_file()
    assert stats["config_written"] == 1
    body = cfg.read_text(encoding="utf-8")
    assert "title: Example" in body
    assert "url: https://example.com" in body


def test_site_config_is_not_overwritten_if_user_has_customised_it(tmp_path: Path):
    (tmp_path / "hugo.toml").write_text(
        'title = "Custom"\n', encoding="utf-8",
    )
    site = _make_site(active_theme="kale")
    stats = emit(site, EmitOptions(
        out_dir=tmp_path, target="hugo", install_templates=False,
    ))
    assert stats["config_written"] == 0
    assert (tmp_path / "hugo.toml").read_text(encoding="utf-8") == 'title = "Custom"\n'


def _make_menu() -> NavMenu:
    about_child = NavMenuItem(
        item_id=3, label="First Post", url="/posts/first/", parent_id=2, order=3,
    )
    about = NavMenuItem(
        item_id=2, label="About", url="/about/", order=2, target="_blank",
        children=[about_child],
    )
    home = NavMenuItem(item_id=1, label="Home", url="/", order=1)
    return NavMenu(term_id=10, name="Primary", slug="primary", items=[home, about])


def test_hugo_writes_menus_config(tmp_path: Path):
    site = _make_site(
        menus=[_make_menu()],
        menu_locations={"main": "primary"},
    )
    stats = emit(site, EmitOptions(
        out_dir=tmp_path, target="hugo", install_templates=False,
    ))
    cfg = tmp_path / "config" / "_default" / "menu.toml"
    assert cfg.is_file()
    assert stats["menus_written"] == 1
    body = cfg.read_text(encoding="utf-8")
    # File is `menu.toml` (singular) so its contents land under Hugo's
    # `menu` key, which is the classic map-of-name-to-entries shape.
    # The plural `menus` key expects a flat list with a `menu` field
    # per entry — a different model we don't emit.
    assert "[[main]]" in body
    assert "[[menu.main]]" not in body
    assert '  name = "Home"' in body
    assert '  url = "/"' in body
    assert '  name = "About"' in body
    assert '  url = "/about/"' in body
    # Child entry carries a parent identifier so Hugo renders the nesting.
    assert '  parent = "main-2"' in body
    assert '  name = "First Post"' in body


def test_hugo_falls_back_to_slug_without_menu_location(tmp_path: Path):
    site = _make_site(menus=[_make_menu()])
    emit(site, EmitOptions(
        out_dir=tmp_path, target="hugo", install_templates=False,
    ))
    body = (tmp_path / "config" / "_default" / "menu.toml"
            ).read_text(encoding="utf-8")
    assert "[[primary]]" in body


def test_jekyll_writes_data_navigation(tmp_path: Path):
    site = _make_site(
        menus=[_make_menu()],
        menu_locations={"main": "primary"},
    )
    stats = emit(site, EmitOptions(
        out_dir=tmp_path, target="jekyll", install_templates=False,
    ))
    data_file = tmp_path / "_data" / "navigation.yml"
    assert data_file.is_file()
    assert stats["menus_written"] == 1
    doc = yaml.safe_load(data_file.read_text(encoding="utf-8"))
    assert list(doc.keys()) == ["main"]
    assert [e["name"] for e in doc["main"]] == ["Home", "About"]
    about = doc["main"][1]
    assert about["url"] == "/about/"
    assert about["target"] == "_blank"
    assert [c["name"] for c in about["children"]] == ["First Post"]


def test_menus_file_not_overwritten_if_exists(tmp_path: Path):
    (tmp_path / "config" / "_default").mkdir(parents=True)
    (tmp_path / "config" / "_default" / "menu.toml").write_text(
        "# custom\n", encoding="utf-8",
    )
    site = _make_site(menus=[_make_menu()])
    stats = emit(site, EmitOptions(
        out_dir=tmp_path, target="hugo", install_templates=False,
    ))
    assert stats["menus_written"] == 0
    assert (tmp_path / "config" / "_default" / "menu.toml"
            ).read_text(encoding="utf-8") == "# custom\n"


def test_menus_skipped_when_site_has_no_menus(tmp_path: Path):
    site = _make_site()
    stats = emit(site, EmitOptions(
        out_dir=tmp_path, target="hugo", install_templates=False,
    ))
    assert stats["menus_written"] == 0
    assert not (tmp_path / "config" / "_default" / "menu.toml").exists()


def test_jekyll_front_page_uses_home_layout(tmp_path: Path):
    site = _make_site(show_on_front="page", page_on_front=1)
    emit(site, EmitOptions(
        out_dir=tmp_path, target="jekyll", install_templates=False,
    ))
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "layout: home" in index
    assert "welcome" in index
    assert not (tmp_path / "home.html").exists()
