"""Write posts/pages/uploads into a Jekyll or Hugo site tree."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import elementor
from .convert import clean_content, extract_upload_paths
from .wpdata import Post, Site

log = logging.getLogger(__name__)

_TEMPLATE_ROOT = Path(__file__).parent / "templates"


@dataclass
class EmitOptions:
    out_dir: Path
    uploads_src: Path | None = None   # local path to wp-content/uploads
    target: str = "jekyll"            # 'jekyll' | 'hugo'
    markdown: bool = False            # convert body HTML → Markdown
    base_url: str = ""                # site URL (from wp_options.siteurl)
    install_templates: bool = True    # copy starter gallery templates into out_dir


def _dump_yaml(data: dict) -> str:
    return yaml.safe_dump(
        data, default_flow_style=False, allow_unicode=True, sort_keys=False,
    )


def _dump_toml(data: dict) -> str:
    """Tiny TOML emitter — enough for flat front matter with string/list/int."""
    out = []
    for k, v in data.items():
        out.append(f"{k} = {_toml_value(v)}")
    return "\n".join(out) + "\n"


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    # strings
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _frontmatter(data: dict, target: str) -> str:
    if target == "hugo":
        return "+++\n" + _dump_toml(data) + "+++\n\n"
    return "---\n" + _dump_yaml(data) + "---\n\n"


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


def _post_output_path(opts: EmitOptions, post: Post, ext: str) -> Path:
    slug = post.slug or f"post-{post.post_id}"
    if opts.target == "hugo":
        subdir = "posts" if post.post_type == "post" else ""
        return opts.out_dir / "content" / subdir / f"{slug}{ext}"
    # jekyll
    if post.post_type == "post":
        datestr = post.date.strftime("%Y-%m-%d")
        return opts.out_dir / "_posts" / f"{datestr}-{slug}{ext}"
    return opts.out_dir / f"{slug}{ext}"


def _uploads_dest(opts: EmitOptions) -> Path:
    if opts.target == "hugo":
        return opts.out_dir / "static" / "uploads"
    return opts.out_dir / "assets" / "uploads"


def _front_page(site: Site) -> Post | None:
    """Return the page configured as the static front page, if any."""
    if site.show_on_front != "page" or not site.page_on_front:
        return None
    for page in site.pages:
        if page.post_id == site.page_on_front:
            return page
    return None


def _write_index(
    opts: EmitOptions, site: Site, front_page: Post | None, front_body: str,
) -> int:
    """Write the site's homepage file. Returns 1 if written, else 0."""
    title = front_page.title if front_page else (site.site_name or "Home")
    if opts.target == "hugo":
        fm: dict = {"title": title}
        if front_page and front_page.date:
            fm["date"] = front_page.date.isoformat(sep=" ")
        if site.site_description and not front_page:
            fm["description"] = site.site_description
        body = front_body if front_page else ""
        # Match the file extension to the body format so Hugo's markdown
        # renderer doesn't accidentally process HTML content.
        index_ext = ".md" if opts.markdown else ".html"
        _write_file(
            opts.out_dir / "content" / f"_index{index_ext}",
            _frontmatter(fm, "hugo") + body + "\n",
        )
        # Section index for posts — lets Hugo render /posts/ via list.html.
        _write_file(
            opts.out_dir / "content" / "posts" / f"_index{index_ext}",
            _frontmatter({"title": "Posts"}, "hugo") + "\n",
        )
        return 1
    # jekyll
    fm = {"layout": "home", "title": title}
    if site.site_description and not front_page:
        fm["description"] = site.site_description
    body = front_body if front_page else ""
    _write_file(
        opts.out_dir / "index.html",
        _frontmatter(fm, "jekyll") + body + "\n",
    )
    return 1


def emit(site: Site, opts: EmitOptions) -> dict:
    """Write the full site tree. Returns a small stats dict."""
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
        if elementor.has_builder_content(post):
            rendered = elementor.render(post)
            if rendered:
                # Elementor replaces the classic content when builder mode
                # is on; the post_content field is usually empty or a
                # shortcode stub in that case.
                source_html = rendered
                elementor_posts += 1
        body = clean_content(
            source_html,
            base_url=base_url,
            uploads_prefix="/uploads",
            markdown=opts.markdown,
            attachments=site.attachments,
            finaltiles_by_id=site.galleries,
            finaltiles_by_slug=site.galleries_by_slug,
            target=opts.target,
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
        path = _post_output_path(opts, post, ext)
        _write_file(path, _frontmatter(fm, opts.target) + body + "\n")
        if post.post_type == "post":
            written_posts += 1
        else:
            written_pages += 1

    indexes_written = _write_index(opts, site, front_page, front_body)

    templates_copied = 0
    if opts.install_templates:
        src_root = _TEMPLATE_ROOT / opts.target
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
        dest_root = _uploads_dest(opts)
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
    }
