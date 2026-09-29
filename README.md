# offerrall.github.io

The site of my Python libraries. It holds no documentation of its own: `build.py` reads each
library's repository at its highest release tag and renders it.

```bash
uv run build.py            # the latest releases, into _site/
uv run build.py --local    # the working trees next to this repo (../<repo>), to preview docs
python -m http.server -d _site
```

## How it updates

`.github/workflows/deploy.yml` rebuilds the site from scratch and deploys it to GitHub Pages, on
a push to `main` and every hour. The libraries do not know the site exists: a new release shows
up within the hour. If a build fails, the site stays as it was.

## What a library needs

The build fails if a library breaks any of these.

1. **`pyproject.toml`**: `name` and `description` (the line on the home page), and the version,
   in `version` or in `src/<name>/__init__.py` as `__version__`.
2. **A release is a tag `v<major>.<minor>.<patch>`** holding that version. The highest one is
   what the site shows.
3. **`README.md`**: what it is, a short example, install, and a `## Documentation` section
   listing every file in `docs/`, one per line, in reading order:

   ```markdown
   ## Documentation

   - [Getting started](docs/getting-started.md): install, a first part, run, export.
   ```

   That list is the menu of the site. Other sections are free.
4. **`docs/*.md`**: one page per topic, each starting with a `# Title`. Images go in
   `docs/images/`. Links between pages are relative (`window.md#layout`), and must point
   to files and headings that exist.

To add a library, add it to `libs.toml`. `pycodecad` is the reference.
