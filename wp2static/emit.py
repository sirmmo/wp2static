"""Write posts/pages/uploads into a Jekyll or Hugo site tree.

This module is intentionally thin: every target-specific decision lives in
a :class:`~wp2static.targets.base.Target`, which this orchestrator looks
up and delegates to. Adding a new SSG changes no code here — only a new
file under :mod:`wp2static.targets`.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from .convert import clean_content, extract_upload_paths
from .plugins import adapter_for_post
from .targets import Target, get_target
from .wpdata import Post, Site

log = logging.getLogger(__name__)

_TEMPLATE_ROOT = Path(__file__).parent / "templates"


@dataclass
class EmitOptions:
    out_dir: Path
    uploads_src: Path | None = None   # local path to wp-content/uploads
    target: str = "jekyll"            # target name or Target instance
    markdown: bool = False            # convert body HTML → Markdown
    base_url: str = ""                # site URL (from wp_options.siteurl)
    install_templates: bool = True    # copy starter gallery templates


def _post_frontmatter(post: Post, ext: str) -> dict:
    fm: dict = {
        "title": post.title,
        "date": post.date.isoformat(sep=" "),
        "slug": post.slug,
    }
    if post.modified and post.modified != post.date:
        fm["lastmod"] = post.modified.isoformat(sep=" ")
    if post.categories:
        fm["categories"] = [t.name for t in post.categories]
    if post.tags:
        fm["tags"] = [t.name for t in post.tags]
    if post.featured_image and post.featured_image.file:
        fm["image"] = f"/uploads/{post.featured_image.file}"
    if post.excerpt:
        fm["excerpt"] = post.excerpt
    if ext == ".md":
        fm["layout"] = "post"
    return fm


def _file_ext(markdown: bool) -> str:
    return ".md" if markdown else ".html"


def _write_file(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _front_page(site: Site) -> Post | None:
    """Return the page configured as the static front page, if any."""
    if site.show_on_front != "page" or not site.page_on_front:
        return None
    for page in site.pages:
        if page.post_id == site.page_on_front:
            return page
    return None


def emit(site: Site, opts: EmitOptions) -> dict:
    """Write the full site tree. Returns a small stats dict."""
    target: Target = get_target(opts.target)
    ext = _file_ext(opts.markdown)
    base_url = opts.base_url or site.base_url

    referenced_uploads: set[str] = set()
    written_posts = 0
    written_pages = 0
    elementor_posts = 0
    front_page = _front_page(site)
    front_body = ""

    for post in site.posts + site.pages:
        source_html = post.content_html
        # Some plugins (Elementor, etc.) store rendered output in post
        # meta rather than ``post_content`` — let the registry decide.
        content_adapter = adapter_for_post(post)
        if content_adapter is not None:
            rendered = content_adapter.render_post_content(post)
            if rendered:
                source_html = rendered
                if content_adapter.slug == "elementor":
                    elementor_posts += 1
        body = clean_content(
            source_html,
            base_url=base_url,
            uploads_prefix="/uploads",
            markdown=opts.markdown,
            attachments=site.attachments,
            finaltiles_by_id=site.galleries,
            finaltiles_by_slug=site.galleries_by_slug,
            target=target,
        )
        fm = _post_frontmatter(post, ext)
        # Scan both the original WP content and the Elementor-rendered HTML so
        # builder pages' images are copied alongside classic-editor posts.
        referenced_uploads.update(extract_upload_paths(post.content_html, base_url))
        referenced_uploads.update(extract_upload_paths(source_html, base_url))
        if post.featured_image and post.featured_image.file:
            referenced_uploads.add(post.featured_image.file)
        if front_page is not None and post.post_id == front_page.post_id:
            front_body = body
            continue  # don't also write this page under content/<slug>.html
        path = target.post_output_path(opts.out_dir, post, ext)
        _write_file(path, target.frontmatter(fm) + body + "\n")
        if post.post_type == "post":
            written_posts += 1
        else:
            written_pages += 1

    indexes_written = target.write_index(
        opts.out_dir, site, front_page, front_body, opts.markdown,
    )
    config_written = target.write_site_config(opts.out_dir, site, base_url)
    menus_written = target.write_menus(opts.out_dir, site)

    templates_copied = 0
    if opts.install_templates:
        src_root = _TEMPLATE_ROOT / target.name
        if src_root.is_dir():
            for src in src_root.rglob("*"):
                if not src.is_file():
                    continue
                rel = src.relative_to(src_root)
                dst = opts.out_dir / rel
                if dst.exists():
                    continue  # never overwrite user-customised templates
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                templates_copied += 1

    copied = 0
    if opts.uploads_src:
        dest_root = target.uploads_dest(opts.out_dir)
        for rel in sorted(referenced_uploads):
            src = opts.uploads_src / rel
            if not src.is_file():
                log.warning("referenced upload missing on disk: %s", rel)
                continue
            dst = dest_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1

    return {
        "posts": written_posts,
        "pages": written_pages,
        "elementor_posts": elementor_posts,
        "uploads_referenced": len(referenced_uploads),
        "uploads_copied": copied,
        "templates_copied": templates_copied,
        "indexes_written": indexes_written,
        "config_written": config_written,
        "menus_written": menus_written,
    }
