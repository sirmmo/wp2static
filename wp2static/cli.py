"""Command-line entrypoint for ``wp2static``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import theme as theme_mod
from .emit import EmitOptions, emit
from .wpdata import load


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wp2static",
        description="Migrate a WordPress MySQL dump to a Jekyll or Hugo site tree.",
    )
    p.add_argument("--sql", required=True, type=Path,
                   help="path to the mysqldump .sql file")
    p.add_argument("--out", required=True, type=Path,
                   help="output directory (will be created)")
    p.add_argument("--target", choices=("jekyll", "hugo"), default="jekyll",
                   help="static site generator layout to emit")
    p.add_argument("--uploads", type=Path, default=None,
                   help="path to the wp-content/uploads directory; "
                        "referenced files are copied into the output tree")
    p.add_argument("--markdown", action="store_true",
                   help="convert post bodies from HTML to Markdown")
    p.add_argument("--no-templates", dest="install_templates",
                   action="store_false",
                   help="skip installing the starter gallery template")
    p.add_argument("--themes-dir", type=Path, default=None,
                   help="path to wp-content/themes; the active theme "
                        "(from wp_options.stylesheet) is scaffolded into "
                        "the output tree")
    p.add_argument("--no-theme", dest="migrate_theme", action="store_false",
                   help="skip theme scaffolding even if --themes-dir is set")
    p.add_argument("--table-prefix", default="wp_",
                   help="WordPress table prefix (default: wp_)")
    p.add_argument("--base-url", default="",
                   help="override the site URL (otherwise read from wp_options.siteurl)")
    p.add_argument("-v", "--verbose", action="count", default=0,
                   help="increase log verbosity (-v, -vv)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    level = logging.WARNING - 10 * min(args.verbose, 2)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")

    if not args.sql.is_file():
        print(f"error: SQL dump not found: {args.sql}", file=sys.stderr)
        return 2
    if args.uploads is not None and not args.uploads.is_dir():
        print(f"error: uploads directory not found: {args.uploads}", file=sys.stderr)
        return 2
    if args.themes_dir is not None and not args.themes_dir.is_dir():
        print(f"error: themes directory not found: {args.themes_dir}", file=sys.stderr)
        return 2

    site = load(args.sql, table_prefix=args.table_prefix)
    opts = EmitOptions(
        out_dir=args.out,
        uploads_src=args.uploads,
        target=args.target,
        markdown=args.markdown,
        base_url=args.base_url,
        install_templates=args.install_templates,
    )
    stats = emit(site, opts)
    print(
        f"wrote {stats['posts']} posts, {stats['pages']} pages "
        f"({stats['elementor_posts']} rendered from Elementor), "
        f"copied {stats['uploads_copied']}/{stats['uploads_referenced']} uploads, "
        f"installed {stats['templates_copied']} template file(s) "
        f"→ {args.out}"
    )

    if args.themes_dir is not None and args.migrate_theme:
        theme_stats = theme_mod.migrate_active_theme(
            site, args.themes_dir, args.out, args.target,
        )
        if theme_stats.get("skipped"):
            print(
                f"theme: skipped (active={theme_stats.get('slug') or 'unknown'})"
            )
        else:
            print(
                f"theme {theme_stats['slug']!r}: "
                f"{theme_stats['templates_written']} template(s) "
                f"({theme_stats['templates_with_unmapped']} with unmapped PHP), "
                f"{theme_stats['assets_copied']} asset(s) copied"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
