# offerrall.github.io

The site of my Python libraries. It holds no documentation of its own: `build.py` reads each
library in `site.toml` at its highest release tag and renders its README and `docs/`.

```bash
uv run build.py            # the latest releases, into _site/
uv run build.py --local    # the working trees next to this repo (../<repo>), to preview docs
python -m http.server -d _site
```

| Path | What it is |
|---|---|
| `site.toml` | The content: the site's title, description and links, and the libraries with their groups |
| `theme/` | The design: `base.html`, `home.html` and `page.html` ([Jinja](https://jinja.palletsprojects.com/) templates), and `static/` (styles, script, icon), copied as is to the root of the site |
| `build.py` | Reads the libraries, checks them, turns their Markdown into HTML and fills the templates |

## How it updates

`.github/workflows/deploy.yml` rebuilds the site from scratch and deploys it to GitHub Pages on a
push to `main` and every hour. The libraries do not know the site exists: a new release shows up
within the hour. If a build fails, the site stays as it was and the reason is sent to the
ntfy topic `offerepos`.

## What a library needs

- **`pyproject.toml`** with `name`, `description` (its line on the home page) and the version:
  `version`, or a file named by `[tool.hatch.version] path` or `[tool.setuptools.dynamic]
  version = {attr = ...}` holding `__version__ = "X.Y.Z"`.
- **Release tags `v<major>.<minor>.<patch>`**, each holding that version.
- **Dependencies on other libraries of the site pinned exactly**, `name==X.Y.Z`: a new
  release of one can never change what an installed release of another does.
- **`README.md`** with a `## Documentation` section that lists every `.md` under `docs/`,
  subfolders included, one per line, in reading order. That list is the library's menu on the site:

  ```markdown
  ## Documentation

  - [Getting started](docs/getting-started.md): install and a first example.
  ```

- **`docs/*.md`**, one page per topic. Images go in `docs/images/`. Links are relative, as they
  work on GitHub (`usage.md#options`).

The build fails when a library breaks any of these: a missing section, a doc not listed or listed
but missing, a tag that holds another version, a dependency on another library of the site that is
not pinned exactly, or a link to a file or heading that does not exist.

To add a library, add it to `site.toml`.
