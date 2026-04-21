"""HTML content transforms: shortcodes, wpautop, URL rewriting, HTML→MD."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# --- shortcode attribute parsing --------------------------------------------

_ATTR_RE = re.compile(
    r"""(?P<key>[a-zA-Z_][a-zA-Z0-9_-]*)\s*=\s*"(?P<val>[^"]*)" """.strip(),
)


def _parse_attrs(attr_src: str) -> dict[str, str]:
    """Parse ``key="value"`` pairs from a shortcode attribute string."""
    return {m.group("key"): m.group("val") for m in _ATTR_RE.finditer(attr_src)}


# --- gallery shortcodes -----------------------------------------------------

_GALLERY_RE = re.compile(
    r"\[gallery(?P<attrs>[^\]]*)\]", re.IGNORECASE,
)
_FINALTILES_RE = re.compile(
    r"\[FinalTilesGallery(?P<attrs>[^\]]*)\]", re.IGNORECASE,
)


def _emit_gallery_directive(target: str, images: list[str], title: str = "") -> str:
    """Return a target-specific directive referring to ``images`` (URL paths).

    The generated directive is on its own line, surrounded by blank lines, so
    ``wpautop`` leaves it unwrapped (see :func:`wpautop` for the detection rule).
    """
    images = [i for i in images if i]
    if not images:
        return ""
    joined = ",".join(images)
    if target == "hugo":
        if title:
            body = f'{{{{< gallery images="{joined}" title="{_esc(title)}" >}}}}'
        else:
            body = f'{{{{< gallery images="{joined}" >}}}}'
    else:  # jekyll
        if title:
            body = (
                f'{{% include gallery.html images="{joined}" '
                f'title="{_esc(title)}" %}}'
            )
        else:
            body = f'{{% include gallery.html images="{joined}" %}}'
    return f"\n\n{body}\n\n"


def _esc(s: str) -> str:
    return s.replace('"', "&quot;")


def _attachment_url(attachment, uploads_prefix: str) -> str:
    """Return the uploads-relative URL for an attachment, preferring `file`."""
    if attachment.file:
        return f"{uploads_prefix.rstrip('/')}/{attachment.file}"
    return attachment.url


def _strip_thumbnail_suffix(url: str) -> str:
    """Turn `foo-150x150.jpg` into `foo.jpg` (WP thumbnail naming)."""
    return re.sub(r"-\d+x\d+(\.[a-zA-Z0-9]+)$", r"\1", url)


def resolve_galleries(
    html: str,
    attachments: dict[int, object],
    finaltiles_by_id: dict[int, object],
    finaltiles_by_slug: dict[str, object],
    base_url: str,
    uploads_prefix: str,
    target: str,
) -> str:
    """Replace gallery shortcodes with target-specific directives."""
    if not html:
        return html

    host = urlparse(base_url).netloc if base_url else ""

    def _image_url_for_id(att_id: int) -> str | None:
        att = attachments.get(att_id)
        if not att:
            return None
        return _attachment_url(att, uploads_prefix)

    def _local_from_url(url: str) -> str:
        """If ``url`` points at this site's uploads, rewrite to local path."""
        if not host:
            return url
        pat = re.compile(
            rf"(?:https?:)?//{re.escape(host)}/wp-content/uploads/",
            re.IGNORECASE,
        )
        if pat.search(url):
            return pat.sub(uploads_prefix.rstrip("/") + "/", url)
        return url

    def _handle_gallery(m: re.Match) -> str:
        attrs = _parse_attrs(m.group("attrs") or "")
        ids_raw = attrs.get("ids") or attrs.get("include") or ""
        ids = [int(x) for x in re.findall(r"\d+", ids_raw)]
        urls = [u for u in (_image_url_for_id(i) for i in ids) if u]
        return _emit_gallery_directive(target, urls)

    def _handle_finaltiles(m: re.Match) -> str:
        attrs = _parse_attrs(m.group("attrs") or "")
        gid_raw = attrs.get("id") or ""
        slug = attrs.get("slug") or ""
        gallery = None
        if gid_raw.isdigit():
            gallery = finaltiles_by_id.get(int(gid_raw))
        if gallery is None and slug:
            gallery = finaltiles_by_slug.get(slug)
        if gallery is None:
            log.warning("FinalTilesGallery %r/%r not found in dump", gid_raw, slug)
            return ""
        # Prefer attachment-resolved URLs (full-size), fall back to the plugin's
        # stored imagePath (which is typically the thumbnail URL).
        urls: list[str] = []
        for att_id in gallery.image_ids:
            url = _image_url_for_id(att_id)
            if url:
                urls.append(url)
        if not urls:
            urls = [_strip_thumbnail_suffix(_local_from_url(u))
                    for u in gallery.image_urls]
        return _emit_gallery_directive(target, urls, title=gallery.name)

    html = _GALLERY_RE.sub(_handle_gallery, html)
    html = _FINALTILES_RE.sub(_handle_finaltiles, html)
    return html


# --- shortcodes --------------------------------------------------------------

_CAPTION_RE = re.compile(
    r"\[caption[^\]]*\](?P<body>.*?)\[/caption\]",
    re.DOTALL | re.IGNORECASE,
)
_GENERIC_SHORTCODE_RE = re.compile(
    r"\[(?P<name>[a-zA-Z][a-zA-Z0-9_-]*)(?P<attrs>[^\]]*)\]"
    r"(?:(?P<body>.*?)\[/(?P=name)\])?",
    re.DOTALL,
)


def _handle_caption(match: re.Match) -> str:
    """Turn ``[caption]<img>Some text[/caption]`` into a ``<figure>``."""
    body = match.group("body").strip()
    # body is typically: <img ... /> caption text
    m = re.match(r"(?s)(?P<img><a[^>]*>\s*<img[^>]*>\s*</a>|<img[^>]*/?>)\s*(?P<cap>.*)", body)
    if not m:
        return body
    img = m.group("img")
    cap = m.group("cap").strip()
    if not cap:
        return img
    return f'<figure class="wp-caption">{img}<figcaption>{cap}</figcaption></figure>'


def strip_shortcodes(html: str, keep: tuple[str, ...] = ()) -> str:
    """Drop all shortcodes except those named in ``keep``.

    ``[caption]`` always becomes a ``<figure>`` regardless of ``keep``.
    ``keep`` contents are passed through unchanged so a downstream renderer
    can handle them (useful if Jekyll/Hugo has a matching shortcode).
    """
    html = _CAPTION_RE.sub(_handle_caption, html)

    def _replace(m: re.Match) -> str:
        name = m.group("name").lower()
        if name in keep:
            return m.group(0)
        # wordpress-internal or gallery-style shortcodes: drop them
        return m.group("body") or ""
    return _GENERIC_SHORTCODE_RE.sub(_replace, html)


# --- wpautop (minimal) -------------------------------------------------------

_BLOCK_TAGS = (
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
    "blockquote", "pre", "table", "thead", "tbody", "tr", "td", "th",
    "figure", "figcaption", "hr", "section", "article", "aside", "header",
    "footer", "nav", "form", "fieldset",
)
_BLOCK_RE = re.compile(
    r"<(?:/)?(?:" + "|".join(_BLOCK_TAGS) + r")\b", re.IGNORECASE,
)
# Lines that begin with an SSG directive should not be wrapped in <p>.
_DIRECTIVE_RE = re.compile(r"^\s*(?:\{\{[%<]|\{%)")


def wpautop(html: str) -> str:
    """Approximate WordPress's ``wpautop``: wrap bare-line paragraphs in ``<p>``.

    WordPress stores post content without ``<p>`` tags — it inserts them at
    render time based on blank-line heuristics. We do the same, in a
    conservative way: we only split on blank lines, skip blocks that already
    contain a block-level tag, and convert lone newlines to ``<br>``.
    """
    if not html.strip():
        return html
    # normalize line endings
    html = html.replace("\r\n", "\n").replace("\r", "\n")
    # If post content already has paragraph-level structure, trust it.
    if re.search(r"<p[\s>]", html, re.IGNORECASE):
        return html
    parts = re.split(r"\n\s*\n", html)
    out = []
    for part in parts:
        s = part.strip()
        if not s:
            continue
        if _BLOCK_RE.search(s) or _DIRECTIVE_RE.match(s):
            out.append(s)
        else:
            s = s.replace("\n", "<br>\n")
            out.append(f"<p>{s}</p>")
    return "\n\n".join(out)


# --- URL rewriting -----------------------------------------------------------

def rewrite_urls(html: str, base_url: str, uploads_prefix: str = "/uploads") -> str:
    """Rewrite ``{base_url}/wp-content/uploads/...`` links to ``{uploads_prefix}/...``.

    Also rewrites protocol-relative ``//host/wp-content/...`` and absolute
    ``https?://host/wp-content/...`` to the same destination when the host
    matches ``base_url``. Links to the site root become ``/``.
    """
    if not html or not base_url:
        return html
    host = urlparse(base_url).netloc
    escaped_host = re.escape(host)

    # /wp-content/uploads → uploads_prefix
    patterns = [
        re.compile(rf"https?://{escaped_host}/wp-content/uploads/", re.IGNORECASE),
        re.compile(rf"//{escaped_host}/wp-content/uploads/", re.IGNORECASE),
    ]
    for pat in patterns:
        html = pat.sub(uploads_prefix.rstrip("/") + "/", html)

    # Plain site-root links on the same host
    patterns_root = [
        re.compile(rf"https?://{escaped_host}/", re.IGNORECASE),
        re.compile(rf"//{escaped_host}/", re.IGNORECASE),
    ]
    for pat in patterns_root:
        html = pat.sub("/", html)
    return html


def extract_upload_paths(html: str, base_url: str) -> list[str]:
    """Return the list of ``wp-content/uploads/...`` paths referenced in ``html``."""
    if not html or not base_url:
        return []
    host = urlparse(base_url).netloc
    pat = re.compile(
        rf"(?:https?:)?//{re.escape(host)}/wp-content/uploads/([^\s\"'<>)]+)",
        re.IGNORECASE,
    )
    return pat.findall(html)


# --- HTML → Markdown ---------------------------------------------------------

def to_markdown(html: str) -> str:
    """Convert cleaned HTML to Markdown via ``markdownify``.

    Imports inside the function so the module is usable without the extra dep
    when someone only needs HTML output.
    """
    from markdownify import markdownify as _md
    return _md(html, heading_style="ATX", bullets="-")


def clean_content(
    html: str,
    base_url: str,
    uploads_prefix: str = "/uploads",
    markdown: bool = False,
    keep_shortcodes: tuple[str, ...] = (),
    attachments: dict[int, object] | None = None,
    finaltiles_by_id: dict[int, object] | None = None,
    finaltiles_by_slug: dict[str, object] | None = None,
    target: str = "jekyll",
) -> str:
    """Full post-content pipeline.

    Order: resolve galleries → strip other shortcodes → wpautop → url rewrite
    → optional HTML→Markdown. Galleries are resolved first so the emitted
    SSG directives survive the subsequent shortcode-stripping pass.
    """
    html = resolve_galleries(
        html,
        attachments=attachments or {},
        finaltiles_by_id=finaltiles_by_id or {},
        finaltiles_by_slug=finaltiles_by_slug or {},
        base_url=base_url,
        uploads_prefix=uploads_prefix,
        target=target,
    )
    html = strip_shortcodes(html, keep=keep_shortcodes)
    html = wpautop(html)
    html = rewrite_urls(html, base_url, uploads_prefix)
    if markdown:
        html = to_markdown(html)
    return html
