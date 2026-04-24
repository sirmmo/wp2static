"""Load WordPress core tables from a SQL dump into in-memory dataclasses."""

from __future__ import annotations

import html
import json
import logging
import re
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
class NavMenuItem:
    """A single entry in a WordPress ``nav_menu``.

    ``url`` is a resolved, target-agnostic link path (leading-slash, trailing
    slash for internal routes) that both Jekyll and Hugo can consume
    directly. ``children`` is populated after all items are loaded.
    """
    item_id: int
    label: str
    url: str
    parent_id: int = 0       # WP menu-item parent (0 = top-level)
    order: int = 0
    target: str = ""         # HTML target attribute — '_blank' or ''
    children: list["NavMenuItem"] = field(default_factory=list)


@dataclass
class NavMenu:
    term_id: int
    name: str
    slug: str
    items: list[NavMenuItem] = field(default_factory=list)  # top-level only


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
    show_on_front: str = ""  # 'posts' | 'page' — from wp_options.show_on_front
    page_on_front: int = 0   # post ID used as the static front page
    page_for_posts: int = 0  # post ID used as the blog index
    menus: list[NavMenu] = field(default_factory=list)
    # theme-registered menu slot → menu slug (e.g. 'primary' → 'main-menu').
    menu_locations: dict[str, str] = field(default_factory=dict)


def _html_unescape(value: str) -> str:
    """HTML-entity decode — ``&amp;`` → ``&``.

    WP `wp_options` stores display strings already HTML-encoded (legacy
    kses/sanitize behaviour); passing them through raw means templates
    that escape on output render ``&amp;`` as visible text.
    """
    return html.unescape(value)


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


# Pull `nav_menu_locations` out of a PHP-serialized ``theme_mods_<theme>``
# blob without implementing a full ``unserialize()``. The shape we're after
# is always ``s:19:"nav_menu_locations";a:N:{ s:…:"<slot>";i:<term_id>; … }``
# and no nested arrays are expected inside that block.
_THEME_MODS_LOC_RE = re.compile(
    r's:\d+:"nav_menu_locations";a:\d+:\{([^{}]*)\}',
)
_LOC_ENTRY_RE = re.compile(r's:\d+:"([^"]+)";i:(\d+);')


def _parse_nav_menu_locations(serialized: str) -> dict[str, int]:
    m = _THEME_MODS_LOC_RE.search(serialized or "")
    if not m:
        return {}
    return {
        entry.group(1): int(entry.group(2))
        for entry in _LOC_ENTRY_RE.finditer(m.group(1))
    }


def _strip_base(url: str, base_url: str) -> str:
    """If ``url`` points back at the site, strip the base so the link is
    served correctly after migration. Otherwise return it unchanged.

    WordPress databases often preserve ``http://`` menu URLs even after
    the site moved to ``https://`` (or vice versa), so we accept the
    alternate scheme as a match — any link to our own hostname is
    internal regardless of scheme.
    """
    if not url:
        return ""
    if not base_url:
        return url
    candidates = [base_url]
    if base_url.startswith("https://"):
        candidates.append("http://" + base_url[len("https://"):])
    elif base_url.startswith("http://"):
        candidates.append("https://" + base_url[len("http://"):])
    for base in candidates:
        if url.startswith(base):
            return url[len(base):] or "/"
    return url


def _resolve_menu_item_url(
    meta: dict[str, str],
    posts_by_id: dict[int, "Post"],
    pages_by_id: dict[int, "Post"],
    terms: dict[int, "Term"],
    base_url: str,
) -> str:
    """Compute a target-agnostic link path for a single nav_menu_item.

    WordPress records the link type in ``_menu_item_type`` and the linked
    entity in ``_menu_item_object_id`` / ``_menu_item_object``; for
    ``custom`` items the literal URL is stored in ``_menu_item_url``. We
    mirror the URL shapes a vanilla Hugo/Jekyll config produces so that
    the emitted menu entries land on the pages the migrator actually
    wrote to disk.
    """
    item_type = (meta.get("_menu_item_type") or "").strip()
    obj = (meta.get("_menu_item_object") or "").strip()
    raw_id = meta.get("_menu_item_object_id") or "0"
    try:
        object_id = int(raw_id)
    except (TypeError, ValueError):
        object_id = 0
    if item_type == "custom":
        return _strip_base(meta.get("_menu_item_url", "") or "/", base_url)
    if item_type == "post_type":
        if obj == "page":
            page = pages_by_id.get(object_id)
            if page:
                return f"/{page.slug}/"
        else:
            post = posts_by_id.get(object_id)
            if post:
                return f"/posts/{post.slug}/"
        return "/"
    if item_type == "taxonomy":
        term = terms.get(object_id)
        slug = term.slug if term else ""
        if not slug:
            return "/"
        if obj == "category":
            return f"/categories/{slug}/"
        if obj == "post_tag":
            return f"/tags/{slug}/"
        return f"/{obj}/{slug}/"
    if item_type == "post_type_archive":
        return f"/{obj or 'posts'}/"
    return "/"


def _menu_item_label(
    title: str,
    meta: dict[str, str],
    posts_by_id: dict[int, "Post"],
    pages_by_id: dict[int, "Post"],
    terms: dict[int, "Term"],
) -> str:
    """Prefer the author-set label (``post_title``), otherwise fall back
    to the linked entity's name so items appear even when the editor
    never typed a custom label in the Menus screen.
    """
    if title:
        return title
    item_type = (meta.get("_menu_item_type") or "").strip()
    try:
        object_id = int(meta.get("_menu_item_object_id") or "0")
    except (TypeError, ValueError):
        object_id = 0
    if item_type == "post_type":
        obj = (meta.get("_menu_item_object") or "").strip()
        source = pages_by_id if obj == "page" else posts_by_id
        linked = source.get(object_id)
        if linked:
            return linked.title
    if item_type == "taxonomy":
        term = terms.get(object_id)
        if term:
            return term.name
    return ""


def _build_menus(
    *,
    raw_posts: dict[int, dict],
    postmeta: dict[int, dict[str, str]],
    terms: dict[int, "Term"],
    taxonomies: dict[int, "Taxonomy"],
    object_terms: dict[int, list[int]],
    posts_by_id: dict[int, "Post"],
    pages_by_id: dict[int, "Post"],
    theme_mods_raw: dict[str, str],
    active_theme: str,
    base_url: str,
) -> tuple[list[NavMenu], dict[str, str]]:
    # term_taxonomy_id -> nav_menu term_id
    nav_ttid_to_term: dict[int, int] = {
        tax.term_taxonomy_id: tax.term_id
        for tax in taxonomies.values()
        if tax.taxonomy == "nav_menu"
    }
    menus_by_term_id: dict[int, NavMenu] = {}
    for term_id in {tid for tid in nav_ttid_to_term.values()}:
        term = terms.get(term_id)
        if not term:
            continue
        menus_by_term_id[term_id] = NavMenu(
            term_id=term_id, name=term.name, slug=term.slug,
        )

    # Collect every nav_menu_item, grouped by its parent menu term.
    items_by_menu: dict[int, list[NavMenuItem]] = {}
    for pid, d in raw_posts.items():
        if d.get("post_type") != "nav_menu_item":
            continue
        if d.get("post_status") != "publish":
            continue
        meta = postmeta.get(pid, {})
        # A nav_menu_item belongs to exactly one nav_menu term — find it.
        menu_term_id: int | None = None
        for ttid in object_terms.get(pid, ()):
            if ttid in nav_ttid_to_term:
                menu_term_id = nav_ttid_to_term[ttid]
                break
        if menu_term_id is None or menu_term_id not in menus_by_term_id:
            continue
        label = _menu_item_label(
            d.get("post_title", "") or "", meta,
            posts_by_id, pages_by_id, terms,
        )
        if not label:
            continue  # nothing useful to render
        url = _resolve_menu_item_url(
            meta, posts_by_id, pages_by_id, terms, base_url,
        )
        try:
            parent_id = int(meta.get("_menu_item_menu_item_parent", "0") or 0)
        except (TypeError, ValueError):
            parent_id = 0
        try:
            order = int(d.get("menu_order", 0) or 0)
        except (TypeError, ValueError):
            order = 0
        items_by_menu.setdefault(menu_term_id, []).append(NavMenuItem(
            item_id=pid, label=label, url=url,
            parent_id=parent_id, order=order,
            target=(meta.get("_menu_item_target") or "").strip(),
        ))

    # Turn the flat list into a parent/child tree, ordered by menu_order.
    for term_id, menu in menus_by_term_id.items():
        flat = sorted(items_by_menu.get(term_id, ()), key=lambda it: it.order)
        by_id = {it.item_id: it for it in flat}
        top: list[NavMenuItem] = []
        for it in flat:
            parent = by_id.get(it.parent_id)
            if parent is not None and parent is not it:
                parent.children.append(it)
            else:
                top.append(it)
        menu.items = top

    menus = [m for m in menus_by_term_id.values() if m.items]

    # Resolve theme-registered slots ('primary', 'footer', ...) to menu
    # slugs so emitters can place each menu in the right location.
    menu_locations: dict[str, str] = {}
    slug_of_term: dict[int, str] = {m.term_id: m.slug for m in menus}
    mods_key = f"theme_mods_{active_theme}" if active_theme else ""
    if mods_key and mods_key in theme_mods_raw:
        for slot, term_id in _parse_nav_menu_locations(
            theme_mods_raw[mods_key],
        ).items():
            slug = slug_of_term.get(term_id)
            if slug:
                menu_locations[slot] = slug

    return menus, menu_locations


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
    show_on_front = ""
    page_on_front = 0
    page_for_posts = 0
    ft_galleries: dict[int, dict] = {}       # Id -> {"slug", "name"}
    ft_images: dict[int, list[dict]] = {}    # gid -> [{"sort", "imageId", "url"}]
    theme_mods_raw: dict[str, str] = {}      # 'theme_mods_<slug>' -> serialized PHP

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
                # WP stores option values HTML-encoded (``Burro &amp; Ansia``).
                # Decode here so Hugo/Jekyll can apply their own escaping
                # once when rendering — otherwise templates show the raw
                # ``&amp;`` entity to visitors.
                site_name = _html_unescape(str(value))
            elif name == "blogdescription":
                site_description = _html_unescape(str(value))
            elif name == "show_on_front":
                show_on_front = str(value)
            elif name == "page_on_front":
                try:
                    page_on_front = int(value)
                except (TypeError, ValueError):
                    page_on_front = 0
            elif name == "page_for_posts":
                try:
                    page_for_posts = int(value)
                except (TypeError, ValueError):
                    page_for_posts = 0
            elif isinstance(name, str) and name.startswith("theme_mods_"):
                theme_mods_raw[name] = str(value)
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

    posts_by_id = {p.post_id: p for p in posts}
    pages_by_id = {p.post_id: p for p in pages}
    menus, menu_locations = _build_menus(
        raw_posts=raw_posts,
        postmeta=postmeta,
        terms=terms,
        taxonomies=taxonomies,
        object_terms=object_terms,
        posts_by_id=posts_by_id,
        pages_by_id=pages_by_id,
        theme_mods_raw=theme_mods_raw,
        active_theme=active_theme,
        base_url=base_url,
    )

    return Site(
        posts=posts, pages=pages, attachments=attachments,
        galleries=galleries, galleries_by_slug=galleries_by_slug,
        base_url=base_url,
        active_theme=active_theme,
        site_name=site_name,
        site_description=site_description,
        show_on_front=show_on_front,
        page_on_front=page_on_front,
        page_for_posts=page_for_posts,
        menus=menus,
        menu_locations=menu_locations,
    )
