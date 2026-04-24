"""Adapter for the ``final-tiles-grid-gallery-lite`` plugin.

Resolves the ``[FinalTilesGallery id=… | slug=…]`` shortcode against the
``FinalTiles_gallery`` / ``FinalTiles_gallery_images`` tables that
:mod:`wp2static.wpdata` loaded into ``site.galleries`` /
``site.galleries_by_slug``. Also handles identification and asset import
(inherited) so the gallery's JS/CSS (slick, lightbox, etc.) ships
alongside the static site.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from .base import PluginAdapter, RenderContext

log = logging.getLogger(__name__)


def _attachment_url(attachment, uploads_prefix: str) -> str:
    """Return the uploads-relative URL for an attachment, preferring `file`."""
    if attachment.file:
        return f"{uploads_prefix.rstrip('/')}/{attachment.file}"
    return attachment.url


def _strip_thumbnail_suffix(url: str) -> str:
    """Turn ``foo-150x150.jpg`` into ``foo.jpg`` (WP thumbnail naming)."""
    return re.sub(r"-\d+x\d+(\.[a-zA-Z0-9]+)$", r"\1", url)


def _local_from_url(url: str, base_url: str, uploads_prefix: str) -> str:
    """If ``url`` points at this site's uploads, rewrite to local path."""
    host = urlparse(base_url).netloc if base_url else ""
    if not host:
        return url
    pat = re.compile(
        rf"(?:https?:)?//{re.escape(host)}/wp-content/uploads/",
        re.IGNORECASE,
    )
    if pat.search(url):
        return pat.sub(uploads_prefix.rstrip("/") + "/", url)
    return url


class FinalTilesAdapter(PluginAdapter):
    slug = "final-tiles-grid-gallery-lite"
    shortcodes = ("FinalTilesGallery",)

    def owns_shortcode(self, name: str) -> bool:
        # Match case-insensitively; WP shortcodes are case-sensitive at
        # runtime, but authors commonly vary the casing in content.
        return name.lower() == "finaltilesgallery"

    def render_shortcode(
        self, name: str, attrs: dict[str, str], ctx: RenderContext,
    ) -> str:
        gid_raw = attrs.get("id") or ""
        slug = attrs.get("slug") or ""
        gallery = None
        if gid_raw.isdigit():
            gallery = ctx.galleries_by_id.get(int(gid_raw))
        if gallery is None and slug:
            gallery = ctx.galleries_by_slug.get(slug)
        if gallery is None:
            log.warning(
                "FinalTilesGallery %r/%r not found in dump", gid_raw, slug,
            )
            return ""
        # Prefer attachment-resolved URLs (full-size), fall back to the
        # plugin's stored imagePath (typically the thumbnail URL).
        urls: list[str] = []
        for att_id in gallery.image_ids:
            att = ctx.attachments.get(att_id)
            if att is None:
                continue
            urls.append(_attachment_url(att, ctx.uploads_prefix))
        if not urls:
            urls = [
                _strip_thumbnail_suffix(
                    _local_from_url(u, ctx.base_url, ctx.uploads_prefix),
                )
                for u in gallery.image_urls
            ]
        return ctx.target.gallery_directive(urls, title=gallery.name)
