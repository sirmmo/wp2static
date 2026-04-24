"""Load a WordPress WXR (``Tools → Export``) XML file into a :class:`Site`.

WXR is the sibling format to the ``mysqldump`` path handled by
:mod:`.wpdata`. It carries most of the same content (posts, pages,
attachments, categories, tags, postmeta, nav menus) but none of the
``wp_options`` row — so settings like ``siteurl``, ``stylesheet`` and
``theme_mods_*`` that only exist in the database are inferred from the
channel header or left blank.

The returned :class:`Site` is identical in shape to what
:func:`wpdata.load` produces, so :mod:`.emit` and :mod:`.theme` can
consume either interchangeably.
"""

from __future__ import annotations

import html
import logging
from pathlib import Path
from xml.etree import ElementTree as ET

from .wpdata import (
    Attachment, Post, Site, Taxonomy, Term,
    _build_menus, _parse_dt,
)

log = logging.getLogger(__name__)

# WXR 1.2 namespaces. Older dumps (1.0/1.1) use the same URIs for the
# elements we care about, so one set covers them all.
NS = {
    "wp": "http://wordpress.org/export/1.2/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "excerpt": "http://wordpress.org/export/1.2/excerpt/",
    "dc": "http://purl.org/dc/elements/1.1/",
}
_W = "{" + NS["wp"] + "}"
_C = "{" + NS["content"] + "}"
_E = "{" + NS["excerpt"] + "}"


def _child_text(elem: ET.Element, path: str) -> str:
    child = elem.find(path)
    if child is None:
        return ""
    return child.text or ""


def _child_int(elem: ET.Element, path: str) -> int | None:
    raw = _child_text(elem, path).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _register_category(
    elem: ET.Element,
    terms: dict[int, Term],
    taxonomies: dict[int, Taxonomy],
    term_by_key: dict[tuple[str, str], int],
) -> None:
    term_id = _child_int(elem, _W + "term_id")
    if term_id is None:
        return
    slug = _child_text(elem, _W + "category_nicename").strip()
    name = _child_text(elem, _W + "cat_name").strip()
    terms[term_id] = Term(term_id, name, slug)
    taxonomies[term_id] = Taxonomy(term_id, term_id, "category")
    if slug:
        term_by_key[("category", slug)] = term_id


def _register_tag(
    elem: ET.Element,
    terms: dict[int, Term],
    taxonomies: dict[int, Taxonomy],
    term_by_key: dict[tuple[str, str], int],
) -> None:
    term_id = _child_int(elem, _W + "term_id")
    if term_id is None:
        return
    slug = _child_text(elem, _W + "tag_slug").strip()
    name = _child_text(elem, _W + "tag_name").strip()
    terms[term_id] = Term(term_id, name, slug)
    taxonomies[term_id] = Taxonomy(term_id, term_id, "post_tag")
    if slug:
        term_by_key[("post_tag", slug)] = term_id


def _register_term(
    elem: ET.Element,
    terms: dict[int, Term],
    taxonomies: dict[int, Taxonomy],
    term_by_key: dict[tuple[str, str], int],
) -> None:
    term_id = _child_int(elem, _W + "term_id")
    if term_id is None:
        return
    taxonomy = _child_text(elem, _W + "term_taxonomy").strip()
    slug = _child_text(elem, _W + "term_slug").strip()
    name = _child_text(elem, _W + "term_name").strip()
    terms[term_id] = Term(term_id, name, slug)
    taxonomies[term_id] = Taxonomy(term_id, term_id, taxonomy)
    if taxonomy and slug:
        term_by_key[(taxonomy, slug)] = term_id


def _process_item(
    elem: ET.Element,
    raw_posts: dict[int, dict],
    postmeta: dict[int, dict[str, str]],
    object_terms: dict[int, list[int]],
    attachment_urls: dict[int, str],
    term_by_key: dict[tuple[str, str], int],
) -> None:
    pid = _child_int(elem, _W + "post_id")
    if pid is None:
        return

    try:
        menu_order = int((_child_text(elem, _W + "menu_order") or "0").strip())
    except ValueError:
        menu_order = 0

    raw_posts[pid] = {
        "ID": pid,
        "post_type": _child_text(elem, _W + "post_type").strip(),
        "post_status": _child_text(elem, _W + "status").strip(),
        "post_title": _child_text(elem, "title").strip(),
        "post_name": _child_text(elem, _W + "post_name").strip(),
        "post_content": _child_text(elem, _C + "encoded"),
        "post_excerpt": _child_text(elem, _E + "encoded"),
        "post_date": _child_text(elem, _W + "post_date").strip(),
        "post_modified": _child_text(elem, _W + "post_modified").strip(),
        "guid": _child_text(elem, "guid").strip(),
        "menu_order": menu_order,
    }

    attach = elem.find(_W + "attachment_url")
    if attach is not None and attach.text:
        attachment_urls[pid] = attach.text.strip()

    for meta_el in elem.findall(_W + "postmeta"):
        key = _child_text(meta_el, _W + "meta_key").strip()
        if not key:
            continue
        # Meta values (e.g. _elementor_data, serialized PHP) must keep
        # their original whitespace and escape sequences intact.
        postmeta.setdefault(pid, {})[key] = _child_text(
            meta_el, _W + "meta_value",
        )

    for cat_el in elem.findall("category"):
        domain = (cat_el.get("domain") or "").strip()
        nicename = (cat_el.get("nicename") or "").strip()
        if not domain or not nicename:
            continue
        term_id = term_by_key.get((domain, nicename))
        if term_id is None:
            continue
        object_terms.setdefault(pid, []).append(term_id)


def load(path: Path) -> Site:
    """Read a WXR file and return a populated :class:`Site`."""
    raw_posts: dict[int, dict] = {}
    postmeta: dict[int, dict[str, str]] = {}
    terms: dict[int, Term] = {}
    taxonomies: dict[int, Taxonomy] = {}
    object_terms: dict[int, list[int]] = {}
    term_by_key: dict[tuple[str, str], int] = {}
    attachment_urls: dict[int, str] = {}

    base_url = ""
    site_name = ""
    site_description = ""

    # Nesting stack so we can disambiguate channel-level ``<title>`` /
    # ``<description>`` from the identically-named children of ``<item>``.
    stack: list[str] = []

    try:
        for event, elem in ET.iterparse(
            str(path), events=("start", "end"),
        ):
            if event == "start":
                stack.append(elem.tag)
                continue
            stack.pop()
            parent = stack[-1] if stack else None
            tag = elem.tag

            if tag == _W + "base_site_url":
                base_url = (elem.text or "").strip().rstrip("/")
            elif tag == _W + "base_blog_url" and not base_url:
                base_url = (elem.text or "").strip().rstrip("/")
            elif tag == "title" and parent == "channel":
                site_name = html.unescape((elem.text or "").strip())
            elif tag == "description" and parent == "channel":
                site_description = html.unescape((elem.text or "").strip())
            elif tag == _W + "category":
                _register_category(elem, terms, taxonomies, term_by_key)
                elem.clear()
            elif tag == _W + "tag":
                _register_tag(elem, terms, taxonomies, term_by_key)
                elem.clear()
            elif tag == _W + "term":
                _register_term(elem, terms, taxonomies, term_by_key)
                elem.clear()
            elif tag == "item":
                _process_item(
                    elem, raw_posts, postmeta, object_terms,
                    attachment_urls, term_by_key,
                )
                elem.clear()
    except ET.ParseError as err:
        # Real-world exports sometimes truncate mid-meta-value when the
        # site threw an error mid-download (the tail of the file is a
        # WP critical-error HTML page). Keep whatever items were fully
        # read before the breakage rather than refusing the whole run.
        log.warning(
            "WXR parse stopped at %s; using %d items parsed before the error",
            err, len(raw_posts),
        )

    log.info(
        "loaded %d items, %d terms, %d taxonomies from WXR",
        len(raw_posts), len(terms), len(taxonomies),
    )

    # Attachments: prefer <wp:attachment_url> over the <guid>, because
    # WXR keeps the canonical file URL on the former while ``guid`` is
    # occasionally a non-permalink (``?attachment_id=…``) that points
    # nowhere useful after migration.
    attachments: dict[int, Attachment] = {}
    for pid, d in raw_posts.items():
        if d["post_type"] != "attachment":
            continue
        meta = postmeta.get(pid, {})
        url = attachment_urls.get(pid) or d.get("guid") or ""
        attachments[pid] = Attachment(
            post_id=pid,
            title=d.get("post_title", ""),
            url=url,
            file=meta.get("_wp_attached_file"),
        )

    def _resolve_terms(post_id: int, taxonomy_name: str) -> list[Term]:
        out: list[Term] = []
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
        featured: Attachment | None = None
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

    posts_by_id = {p.post_id: p for p in posts}
    pages_by_id = {p.post_id: p for p in pages}
    # WXR has no wp_options, so theme mods and the active-theme slug are
    # unavailable. Menus still resolve their parent/child tree — just
    # without a mapping from theme slot to menu slug.
    menus, menu_locations = _build_menus(
        raw_posts=raw_posts,
        postmeta=postmeta,
        terms=terms,
        taxonomies=taxonomies,
        object_terms=object_terms,
        posts_by_id=posts_by_id,
        pages_by_id=pages_by_id,
        theme_mods_raw={},
        active_theme="",
        base_url=base_url,
    )

    return Site(
        posts=posts, pages=pages, attachments=attachments,
        galleries={}, galleries_by_slug={},
        base_url=base_url,
        active_theme="",
        site_name=site_name,
        site_description=site_description,
        show_on_front="",
        page_on_front=0,
        page_for_posts=0,
        menus=menus,
        menu_locations=menu_locations,
    )
