"""Adapter for the Elementor page-builder plugin.

Elementor stores each page/post as a JSON tree in the ``_elementor_data``
post meta (not in ``post_content``). We walk the tree and emit plain HTML
with ``elementor-*`` class names — enough for a static target to display,
style, or further rewrite.

Coverage is intentionally narrow: the widgets actually seen in the source
dump get first-class renderers; anything else falls through to an HTML
comment so it's visible during review. Extend ``_WIDGETS`` to handle more.
"""

from __future__ import annotations

import html
import json
import logging
from typing import TYPE_CHECKING, Callable

from ..wpdata import wp_unslash
from .base import PluginAdapter

if TYPE_CHECKING:
    from ..wpdata import Post

log = logging.getLogger(__name__)


def _esc(s: object) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _attr(name: str, value: object) -> str:
    if value is None or value == "":
        return ""
    return f' {name}="{_esc(value)}"'


# --- widget renderers --------------------------------------------------------

def _render_heading(settings: dict) -> str:
    tag = (settings.get("header_size") or "h2").lower()
    if tag not in ("h1", "h2", "h3", "h4", "h5", "h6", "div", "span", "p"):
        tag = "h2"
    link = (settings.get("link") or {}).get("url") or ""
    title = settings.get("title", "") or ""
    inner = _esc(title)
    if link:
        inner = f'<a href="{_esc(link)}">{inner}</a>'
    return f'<{tag} class="elementor-heading-title">{inner}</{tag}>'


def _render_text_editor(settings: dict) -> str:
    body = settings.get("editor", "") or ""
    return f'<div class="elementor-text-editor">{body}</div>'


def _render_image(settings: dict) -> str:
    img = settings.get("image") or {}
    url = img.get("url") or settings.get("image_src") or ""
    alt = settings.get("alt") or settings.get("caption") or ""
    link_cfg = settings.get("link") or {}
    link_url = link_cfg.get("url", "") if isinstance(link_cfg, dict) else ""
    caption = settings.get("caption", "") if settings.get("caption_source") else ""
    if not url:
        return "<!-- wp2static: elementor image had no url -->"
    tag = f'<img src="{_esc(url)}" alt="{_esc(alt)}" loading="lazy">'
    if link_url:
        tag = f'<a href="{_esc(link_url)}">{tag}</a>'
    if caption:
        return (
            '<figure class="elementor-image">'
            f'{tag}<figcaption>{_esc(caption)}</figcaption></figure>'
        )
    return f'<figure class="elementor-image">{tag}</figure>'


def _render_spacer(settings: dict) -> str:
    space = (settings.get("space") or {}).get("size", 20)
    return f'<div class="elementor-spacer" style="height:{_esc(space)}px"></div>'


def _render_divider(_settings: dict) -> str:
    return '<hr class="elementor-divider">'


def _render_social_icons(settings: dict) -> str:
    items = settings.get("social_icon_list") or []
    out = ['<ul class="elementor-social-icons">']
    for item in items:
        url = (item.get("link") or {}).get("url", "#")
        icon = item.get("social") or item.get("social_icon") or ""
        if isinstance(icon, dict):
            icon = icon.get("value") or ""
        label = str(icon).split("-")[-1] or "link"
        out.append(
            f'  <li><a href="{_esc(url)}" class="elementor-social-icon" '
            f'data-icon="{_esc(icon)}">{_esc(label)}</a></li>'
        )
    out.append("</ul>")
    return "\n".join(out)


def _render_button(settings: dict) -> str:
    text = settings.get("text", "Click here") or ""
    link = (settings.get("link") or {}).get("url") or "#"
    return (
        f'<a class="elementor-button" href="{_esc(link)}">{_esc(text)}</a>'
    )


def _render_html(settings: dict) -> str:
    return settings.get("html", "") or ""


def _render_shortcode(settings: dict) -> str:
    # Left unprocessed on purpose — the main content pipeline already
    # handles [gallery] etc. by resolving or stripping them.
    return settings.get("shortcode", "") or ""


def _render_video(settings: dict) -> str:
    url = (settings.get("youtube_url")
           or settings.get("vimeo_url")
           or (settings.get("hosted_url") or {}).get("url") or "")
    if not url:
        return "<!-- wp2static: elementor video had no url -->"
    return (
        '<div class="elementor-video">'
        f'<iframe src="{_esc(url)}" allowfullscreen loading="lazy"></iframe>'
        '</div>'
    )


def _render_icon_list(settings: dict) -> str:
    items = settings.get("icon_list") or []
    out = ['<ul class="elementor-icon-list">']
    for item in items:
        text = item.get("text") or ""
        link = (item.get("link") or {}).get("url") or ""
        if link:
            out.append(f'  <li><a href="{_esc(link)}">{_esc(text)}</a></li>')
        else:
            out.append(f"  <li>{_esc(text)}</li>")
    out.append("</ul>")
    return "\n".join(out)


_WIDGETS: dict[str, Callable[[dict], str]] = {
    "heading": _render_heading,
    "text-editor": _render_text_editor,
    "image": _render_image,
    "spacer": _render_spacer,
    "divider": _render_divider,
    "social-icons": _render_social_icons,
    "button": _render_button,
    "html": _render_html,
    "shortcode": _render_shortcode,
    "video": _render_video,
    "icon-list": _render_icon_list,
}


# --- tree walk --------------------------------------------------------------

def _render_widget(node: dict) -> str:
    wtype = node.get("widgetType", "")
    settings = node.get("settings") or {}
    renderer = _WIDGETS.get(wtype)
    if renderer is None:
        log.info("unsupported elementor widget: %s", wtype)
        return f'<!-- wp2static: unsupported elementor widget {wtype!r} -->'
    try:
        return renderer(settings)
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("elementor widget %s failed: %s", wtype, exc)
        return f'<!-- wp2static: error rendering widget {wtype!r}: {exc} -->'


def _render_element(node: dict) -> str:
    el = node.get("elType")
    if el == "section":
        return _render_section(node)
    if el == "column":
        return _render_column(node)
    if el == "widget":
        return _render_widget(node)
    return ""


def _render_column(node: dict) -> str:
    settings = node.get("settings") or {}
    size = settings.get("_column_size")
    style = f' style="flex-basis:{size}%"' if size else ""
    inner = "".join(_render_element(c) for c in node.get("elements") or [])
    return f'<div class="elementor-column"{style}>{inner}</div>'


def _render_section(node: dict) -> str:
    settings = node.get("settings") or {}
    layout = settings.get("layout") or ""
    classes = ["elementor-section"]
    if layout:
        classes.append(f"elementor-section--{layout}")
    inner = "".join(_render_element(c) for c in node.get("elements") or [])
    cls = " ".join(classes)
    return f'<section class="{cls}">{inner}</section>'


def _render_tree(post: "Post") -> str:
    raw = post.elementor_data
    if not raw:
        return ""
    try:
        # ``_elementor_data`` is stored JSON with an extra layer of PHP
        # slashes from ``wp_slash``; strip them before parsing.
        tree = json.loads(wp_unslash(raw))
    except json.JSONDecodeError as exc:
        log.warning("post %s: could not parse _elementor_data: %s",
                    post.post_id, exc)
        return ""
    parts = [_render_element(node) for node in tree]
    return (
        '<div class="elementor-content">\n'
        + "\n".join(p for p in parts if p)
        + "\n</div>"
    )


# --- adapter ----------------------------------------------------------------

class ElementorAdapter(PluginAdapter):
    slug = "elementor"
    # Elementor's own ``[elementor-template]`` is the only shortcode the
    # frontend exposes; inner widgets are rendered from stored JSON.
    shortcodes = ("elementor-template",)

    def replaces_post_content(self, post: "Post") -> bool:
        return post.elementor_mode == "builder" and bool(post.elementor_data)

    def render_post_content(self, post: "Post") -> str:
        return _render_tree(post)
