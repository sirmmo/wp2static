# wp2static

Migrate a WordPress MySQL dump to a Jekyll or Hugo site tree so the
resulting static site can be built by any CI runner (or locally).

## What it does

- Streams the mysqldump `.sql` file without loading MySQL.
- Reads `wp_posts`, `wp_postmeta`, `wp_terms`, `wp_term_taxonomy`,
  `wp_term_relationships`, `wp_options` and (if present) the
  FinalTiles Gallery plugin tables.
- Emits one file per published `post` / `page` with YAML (Jekyll) or
  TOML (Hugo) front matter including title, date, slug, categories,
  tags, featured image, excerpt.
- Cleans up the body: `[caption]` → `<figure>`, resolves `[gallery]`
  and `[FinalTilesGallery]` shortcodes to a target-specific gallery
  directive (`{{< gallery >}}` for Hugo, `{% include gallery.html %}`
  for Jekyll) with the image URLs already resolved, drops other
  shortcodes, runs a minimal `wpautop`, rewrites
  `https://site/wp-content/uploads/...` links to `/uploads/...`.
- Installs a starter gallery template into the output tree so the
  generated content renders out of the box (`--no-templates` to skip).
- Optionally copies referenced uploads from a local
  `wp-content/uploads` directory into the output tree.
- Optionally converts body HTML to Markdown via `markdownify`.
- Renders Elementor page-builder content (`_elementor_data`) to plain
  HTML for posts/pages authored with the builder.
- Optionally scaffolds the active WordPress theme (`--themes-dir`):
  copies static assets, parses `style.css`, and transpiles the PHP
  templates to Liquid (Jekyll) or Go `html/template` (Hugo). Unmapped
  PHP is left inline as a visible comment and summarised in
  `MIGRATION.md`. It's a scaffold, not a drop-in port.

Ignored on purpose: comments, revisions, drafts, authors, custom
post types, `functions.php`, widget areas, Customizer mods.

## Getting the source

```bash
git clone https://github.com/sirmmo/wp2static.git
cd wp2static
```

## Build the image

```bash
docker build -t wp2static .
```

## Usage

```bash
docker run --rm \
    -v /path/to/dump:/data:ro \
    -v "$PWD/site":/out \
    wp2static \
        --sql /data/dump.sql \
        --uploads /data/wp-content/uploads \
        --out /out \
        --target jekyll            # or: hugo
        # --markdown               # convert HTML → Markdown
        # --no-templates           # skip starter gallery template
        # --themes-dir /data/wp-content/themes   # scaffold active theme
        # --no-theme               # disable theme scaffolding
        # --base-url https://example.com   # override wp_options.siteurl
        # --table-prefix wp_       # if the dump uses a non-default prefix
```

A fuller invocation that also pulls in uploads and scaffolds the active
theme:

```bash
docker run --rm \
    -v /path/to/site:/data:ro \
    -v "$PWD/site":/out \
    wp2static \
        --sql /data/dump.sql \
        --uploads /data/wp-content/uploads \
        --themes-dir /data/wp-content/themes \
        --out /out \
        --target hugo -v
```

## Output layout

Content:

| target | posts                         | pages               | uploads             | gallery template                    |
| ------ | ----------------------------- | ------------------- | ------------------- | ----------------------------------- |
| jekyll | `_posts/YYYY-MM-DD-slug.html` | `slug.html`         | `assets/uploads/`   | `_includes/gallery.html`            |
| hugo   | `content/posts/slug.html`     | `content/slug.html` | `static/uploads/`   | `layouts/shortcodes/gallery.html`   |

Theme scaffold (when `--themes-dir` is set):

| target | theme root       | partials / includes                       | assets                  |
| ------ | ---------------- | ----------------------------------------- | ----------------------- |
| jekyll | `out_dir` (root) | `_layouts/` + `_includes/`                | `assets/theme/`         |
| hugo   | `themes/<slug>/` | `layouts/_default/` + `layouts/partials/` | `themes/<slug>/static/` |

The starter gallery template is intentionally minimal — it emits a
`<figure class="wp2j-gallery">` with anchor-wrapped `<img>` tags, so
you can drop in any lightbox library by styling `.wp2j-gallery-item`
or replacing the template file.

Each scaffolded theme gets a `MIGRATION.md` listing the PHP calls that
fell through — search the templates for `wp2static: unmapped` to find
them in context.

## Running the tests

Tests live under `tests/` and run inside Docker via a dedicated build
stage, so you don't need a local Python toolchain:

```bash
docker build --target test -t wp2static-test .
docker run --rm wp2static-test              # runs: pytest -q
docker run --rm wp2static-test tests/test_convert.py -v   # single file
```

The `test` stage installs the `[test]` extra (adds `pytest`) and copies
`tests/` into the image. The `runtime` stage, which is what `docker
build -t wp2static .` produces, stays slim and has neither.

## Contributing

Bug reports and pull requests are welcome at
<https://github.com/sirmmo/wp2static/issues>. When filing a bug, please
include:

- the output of `docker run --rm wp2static --help` (to confirm the
  version / build you are on),
- the relevant `INSERT INTO` statement from the dump (redacted if
  needed), and
- the command line you used.

PRs should include tests under `tests/` exercising the behaviour you
are changing, and keep the Docker-first workflow intact.

## Licence

MIT — see [LICENSE](LICENSE).
