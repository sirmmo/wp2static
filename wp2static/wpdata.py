"""Load WordPress core tables from a SQL dump into in-memory dataclasses."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .sqldump import iter_rows

log = logging.getLogger(__name__)

# Column order matches the canonical WordPress schema (as emitted by
# mysqldump for `wp_posts` etc.). These map positional tuples to named
# fields without needing the CREATE TABLE header.
POST_COLS = [
    "ID", "post_author", "post_date", "post_date_gmt",
    "post_content", "post_title", "post_excerpt", "post_status",
    "comment_status", "ping_status", "post_password", "post_name",
    "to_ping", "pinged", "post_modified", "post_modified_gmt",
    "post_content_filtered", "post_parent", "guid", "menu_order",
    "post_type", "post_mime_type", "comment_count",
]
POSTMETA_COLS = ["meta_id", "post_id", "meta_key", "meta_value"]
TERM_COLS = ["term_id", "name", "slug", "term_group"]
TERM_TAX_COLS = ["term_taxonomy_id", "term_id", "taxonomy", "description",
                 "parent", "count"]
TERM_REL_COLS = ["object_id", "term_taxonomy_id", "term_order"]

# FinalTiles Grid Gallery (Lite) plugin — present on the reference dump.
FT_GALLERY_COLS = ["Id", "configuration"]
FT_IMAGE_COLS = [
    "Id", "gid", "type", "imageId", "imagePath", "filters", "link",
    "title", "target", "blank", "description", "sortOrder", "group", "hidden",
]


def _row_to_dict(row: tuple, cols: list[str]) -> dict:
    # tolerate extra or missing columns (schema drift across WP versions)
    return dict(zip(cols, row))


@dataclass
class Term:
    term_id: int
    name: str
    slug: str


@dataclass
class Taxonomy:
    """A term-in-a-taxonomy (category or tag)."""
    term_taxonomy_id: int
    term_id: int
    taxonomy: str  # 'category' | 'post_tag' | ...


@dataclass
class Attachment:
    """A WordPress attachment post (post_type='attachment')."""
    post_id: int
    title: str
    url: str            # absolute URL from `guid`
    file: str | None    # relative path from `_wp_attached_file` meta


@dataclass
class Post:
    post_id: int
    post_type: str          # 'post' | 'page'
    title: str
    slug: str
    content_html: str
    excerpt: str
    date: datetime
    modified: datetime
    status: str
    categories: list[Term] = field(default_factory=list)
    tags: list[Term] = field(default_factory=list)
    featured_image: Attachment | None = None
    guid: str = ""
    elementor_data: str = ""        # raw _elementor_data JSON (WP-slashed)
    elementor_mode: str = ""        # _elementor_edit_mode: 'builder' when active


@dataclass
class Gallery:
    """A FinalTiles gallery: an ordered list of images plus a human name."""
    gallery_id: int
    slug: str
    name: str
    image_ids: list[int] = field(default_factory=list)     # WP attachment IDs
    image_urls: list[str] = field(default_factory=list)    # raw URLs (fallback)


@dataclass
class Site:
    posts: list[Post]
    pages: list[Post]
    attachments: dict[int, Attachment]
    galleries: dict[int, Gallery] = field(default_factory=dict)       # by Id
    galleries_by_slug: dict[str, Gallery] = field(default_factory=dict)
    base_url: str = ""     # from wp_options.siteurl
    active_theme: str = ""   # from wp_options.stylesheet (falls back to template)
    site_name: str = ""      # from wp_options.blogname
    site_description: str = ""   # from wp_options.blogdescription


def wp_unslash(value: str) -> str:
    """Reverse PHP's ``wp_slash`` (addslashes) on a value read from postmeta.

    WordPress wraps ``\\`` around ``'``, ``"`` and ``\\`` before INSERTing
    meta values, so after parsing the SQL we still have one layer of
    backslashes embedded in JSON blobs like ``_elementor_data``. Collapse
    ``\\X`` → ``X`` for the four characters PHP escapes.
    """
    out: list[str] = []
    i, n = 0, len(value)
    while i < n:
        c = value[i]
        if c == "\\" and i + 1 < n and value[i + 1] in "\"'\\0":
            nxt = value[i + 1]
            out.append("\x00" if nxt == "0" else nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    s = str(value)
    if s.startswith("0000-"):
        return datetime(1970, 1, 1)
    # mysqldump format: 'YYYY-MM-DD HH:MM:SS'
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        log.warning("could not parse datetime %r, falling back to epoch", s)
        return datetime(1970, 1, 1)


def load(dump_path: Path, table_prefix: str = "wp_") -> Site:
    """Read the dump and return a populated :class:`Site`."""
    wanted = {
        table_prefix + t for t in (
            "posts", "postmeta", "terms", "term_taxonomy",
            "term_relationships", "options",
            "FinalTiles_gallery", "FinalTiles_gallery_images",
        )
    }

    raw_posts: dict[int, dict] = {}
    postmeta: dict[int, dict[str, str]] = {}
    terms: dict[int, Term] = {}
    taxonomies: dict[int, Taxonomy] = {}
    object_terms: dict[int, list[int]] = {}  # post_id -> [term_taxonomy_id]
    base_url = ""
    active_theme = ""
    site_name = ""
    site_description = ""
    ft_galleries: dict[int, dict] = {}       # Id -> {"slug", "name"}
    ft_images: dict[int, list[dict]] = {}    # gid -> [{"sort", "imageId", "url"}]

    for table, row in iter_rows(dump_path, tables=wanted):
        suffix = table[len(table_prefix):]
        if suffix == "posts":
            d = _row_to_dict(row, POST_COLS)
            raw_posts[d["ID"]] = d
        elif suffix == "postmeta":
            d = _row_to_dict(row, POSTMETA_COLS)
            postmeta.setdefault(d["post_id"], {})[d["meta_key"]] = d["meta_value"]
        elif suffix == "terms":
            d = _row_to_dict(row, TERM_COLS)
            terms[d["term_id"]] = Term(d["term_id"], d["name"], d["slug"])
        elif suffix == "term_taxonomy":
            d = _row_to_dict(row, TERM_TAX_COLS)
            taxonomies[d["term_taxonomy_id"]] = Taxonomy(
                d["term_taxonomy_id"], d["term_id"], d["taxonomy"],
            )
        elif suffix == "term_relationships":
            d = _row_to_dict(row, TERM_REL_COLS)
            object_terms.setdefault(d["object_id"], []).append(d["term_taxonomy_id"])
        elif suffix == "options":
            # options schema: (option_id, option_name, option_value, autoload)
            if len(row) < 3:
                continue
            name, value = row[1], row[2]
            if name == "siteurl":
                base_url = str(value).rstrip("/")
            elif name == "stylesheet":
                active_theme = str(value)
            elif name == "template" and not active_theme:
                active_theme = str(value)
            elif name == "blogname":
                site_name = str(value)
            elif name == "blogdescription":
                site_description = str(value)
        elif suffix == "FinalTiles_gallery":
            d = _row_to_dict(row, FT_GALLERY_COLS)
            try:
                cfg = json.loads(d.get("configuration") or "{}")
            except (TypeError, ValueError):
                cfg = {}
            ft_galleries[d["Id"]] = {
                "slug": (cfg.get("slug") or "").strip(),
                "name": (cfg.get("name") or "").strip(),
            }
        elif suffix == "FinalTiles_gallery_images":
            d = _row_to_dict(row, FT_IMAGE_COLS)
            ft_images.setdefault(d["gid"], []).append({
                "sort": d.get("sortOrder") or 0,
                "imageId": d.get("imageId") or 0,
                "url": d.get("imagePath") or "",
            })

    log.info("loaded %d posts, %d terms, %d taxonomies",
             len(raw_posts), len(terms), len(taxonomies))

    # Build attachment index first — needed to resolve featured images.
    attachments: dict[int, Attachment] = {}
    for pid, d in raw_posts.items():
        if d["post_type"] != "attachment":
            continue
        meta = postmeta.get(pid, {})
        attachments[pid] = Attachment(
            post_id=pid,
            title=d["post_title"],
            url=d["guid"],
            file=meta.get("_wp_attached_file"),
        )

    def _resolve_terms(post_id: int, taxonomy_name: str) -> list[Term]:
        out = []
        for ttid in object_terms.get(post_id, ()):
            tax = taxonomies.get(ttid)
            if tax is None or tax.taxonomy != taxonomy_name:
                continue
            term = terms.get(tax.term_id)
            if term:
                out.append(term)
        return out

    posts: list[Post] = []
    pages: list[Post] = []
    for pid, d in raw_posts.items():
        ptype = d["post_type"]
        if ptype not in ("post", "page"):
            continue
        if d["post_status"] != "publish":
            continue
        meta = postmeta.get(pid, {})
        thumb_id = meta.get("_thumbnail_id")
        featured = None
        if thumb_id:
            try:
                featured = attachments.get(int(thumb_id))
            except (TypeError, ValueError):
                featured = None
        post = Post(
            post_id=pid,
            post_type=ptype,
            title=d["post_title"],
            slug=d["post_name"] or f"post-{pid}",
            content_html=d["post_content"] or "",
            excerpt=d["post_excerpt"] or "",
            date=_parse_dt(d["post_date"]),
            modified=_parse_dt(d["post_modified"]),
            status=d["post_status"],
            categories=_resolve_terms(pid, "category"),
            tags=_resolve_terms(pid, "post_tag"),
            featured_image=featured,
            guid=d["guid"],
            elementor_data=meta.get("_elementor_data", "") or "",
            elementor_mode=meta.get("_elementor_edit_mode", "") or "",
        )
        (pages if ptype == "page" else posts).append(post)

    posts.sort(key=lambda p: p.date)
    pages.sort(key=lambda p: p.title)

    galleries: dict[int, Gallery] = {}
    galleries_by_slug: dict[str, Gallery] = {}
    for gid, meta in ft_galleries.items():
        rows = sorted(ft_images.get(gid, ()), key=lambda r: r["sort"])
        gallery = Gallery(
            gallery_id=gid,
            slug=meta["slug"] or f"gallery-{gid}",
            name=meta["name"] or f"Gallery {gid}",
            image_ids=[int(r["imageId"]) for r in rows if r["imageId"]],
            image_urls=[r["url"] for r in rows if r["url"]],
        )
        galleries[gid] = gallery
        if gallery.slug:
            galleries_by_slug[gallery.slug] = gallery

    log.info("loaded %d FinalTiles galleries", len(galleries))

    return Site(
        posts=posts, pages=pages, attachments=attachments,
        galleries=galleries, galleries_by_slug=galleries_by_slug,
        base_url=base_url,
        active_theme=active_theme,
        site_name=site_name,
        site_description=site_description,
    )
