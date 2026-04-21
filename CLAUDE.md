# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Docker-first workflow

This project is developed and run exclusively via Docker — do **not**
`pip install` on the host or create a local venv. All commands below
go through the Dockerfile's two-stage build:

```bash
# Build the runtime image (slim — no tests, no pytest)
docker build -t wp2static .

# Build the test image (adds tests/ and the [test] extra)
docker build --target test -t wp2static-test .

# Run the full test suite
docker run --rm wp2static-test

# Run a single test file / test
docker run --rm wp2static-test tests/test_convert.py -v
docker run --rm wp2static-test tests/test_theme.py::test_transpile_marks_unmapped_calls
```

Running the tool:

```bash
docker run --rm \
    -v /path/to/site:/data:ro \
    -v "$PWD/out":/out \
    wp2static \
        --sql /data/dump.sql \
        --uploads /data/wp-content/uploads \
        --themes-dir /data/wp-content/themes \
        --out /out \
        --target hugo -v
```

The starter gallery templates under `wp2static/templates/` are shipped
as package data (`[tool.setuptools.package-data]`) — if you add new
template files, they need to match the `templates/**/*.html` glob or
they won't be included in the wheel.

## Architecture — the migration pipeline

`cli.main` runs two independent passes:

1. **Content emission** (`wpdata.load` → `emit.emit`)
   - `sqldump.iter_rows` streams the mysqldump line by line, only parsing
     `INSERT INTO \`<table>\` VALUES ...` lines for the tables in a
     whitelist. It is a hand-rolled parser (not a full SQL parser) and
     handles MySQL's C-style escapes plus doubled single-quotes.
   - `wpdata.load` consumes those rows into dataclasses: `Site`
     (posts/pages/attachments/galleries + options like `base_url`,
     `active_theme`, `site_name`), `Post`, `Attachment`, `Gallery`,
     `Term`, `Taxonomy`. Only `post_status == 'publish'` rows for
     `post` / `page` types make it into the output; attachments are
     kept separately so the content pipeline can resolve image refs.
   - `emit.emit` walks posts + pages, runs the content pipeline, writes
     files with YAML (Jekyll) or TOML (Hugo) front matter, tracks
     referenced `wp-content/uploads/...` paths, copies those from
     `uploads_src`, and installs starter templates from
     `wp2static/templates/<target>/` (skipping any files the user has
     customised).

2. **Theme scaffold** (`theme.migrate_active_theme`, opt-in via `--themes-dir`)
   - Reads `wp_options.stylesheet` from the loaded `Site` to pick the
     active theme directory.
   - Copies static assets, parses `style.css`'s header block, emits
     `theme.toml` (Hugo) or `theme.yml` (Jekyll) metadata.
   - Transpiles each `.php` template by walking `<?php ... ?>` blocks
     and applying a **regex rule table** (`_RULES`) plus
     `_rewrite_template_parts` for `get_header / get_footer /
     get_sidebar / get_template_part`. This is pattern-based, not a
     PHP parser — anything that doesn't match becomes a visible
     `<!-- wp2static: unmapped PHP: ... -->` comment and is logged in
     the generated `MIGRATION.md`.
   - Layout mapping is intentionally different per target:
     `_jekyll_layout_for` → `_layouts/` + `_includes/` inside `out_dir`;
     `_hugo_layout_for` → `themes/<slug>/layouts/_default/` +
     `layouts/partials/`. `functions.php` and plugin bundles shipped
     inside the theme (`freemius/`, `plugins/`, `inc/`) are skipped on
     purpose.

### Content-pipeline invariants (`convert.clean_content`)

Order matters. The pipeline is:

```
resolve_galleries → strip_shortcodes → wpautop → rewrite_urls → [markdown]
```

- **Galleries first**: they emit SSG directives (e.g.
  `{{< gallery images="..." >}}`) surrounded by blank lines so they
  survive the next passes.
- **`wpautop` skips directives**: lines that start with `{{<`, `{{%`,
  or `{%` match `_DIRECTIVE_RE` and are not wrapped in `<p>`. Breaking
  this invariant is the quickest way to produce invalid Hugo / Jekyll
  output.
- **Markdown is optional** and runs last so the HTML transforms see a
  consistent shape.

### Elementor handling

Posts authored with Elementor store a JSON tree in
`postmeta._elementor_data`, which is wrapped in PHP's `wp_slash` before
insertion — after SQL parsing we still have a layer of `\"` / `\'` /
`\\` / `\0` escapes. `wpdata.wp_unslash` reverses those four (and only
those four) before `elementor.render` calls `json.loads`. Don't use a
generic string-unescape here; it will corrupt content that contains
backslash-n or similar.

`elementor.render` walks `section → column → widget` and dispatches via
`_WIDGETS`. Unknown widgets become `<!-- wp2static: unsupported
elementor widget '<type>' -->` rather than raising, so new widget types
are visible on inspection and can be added by extending `_WIDGETS`.
`emit.emit` only substitutes the rendered HTML when
`elementor.has_builder_content(post)` is true (i.e. both
`elementor_mode == "builder"` **and** `elementor_data` is non-empty).

## Things to be careful about

- **`sqldump._INSERT_RE` matches at start of line only**. mysqldump
  emits one statement per line; if a dump has been reformatted with
  leading whitespace or split across lines, rows will be silently
  skipped.
- **`wp_unslash` is intentionally narrow**. It reverses `wp_slash`,
  not generic C escapes — expanding it will break round-tripping for
  content that legitimately contains `\n` / `\t`.
- The tests' `tiny_dump` fixture (`tests/conftest.py`) is the
  load-integration canary: changes to positional column order in
  `POST_COLS` / `POSTMETA_COLS` etc. must be reflected there too, or
  the loader tests will silently swap fields.

## Repository

Source: <https://github.com/sirmmo/wp2static>. Licence: MIT (see
`LICENSE`).
