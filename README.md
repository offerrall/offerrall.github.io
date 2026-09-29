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
| `site.toml` | The content: the site's title, description, URL and links, and the libraries with their groups |
| `theme/` | The design: `base.html`, `home.html`, `page.html` and `dependencies.html` for people, `doc.html` and `llms.txt` for agents ([Jinja](https://jinja.palletsprojects.com/) templates), and `static/` (styles, script, icon), copied as is to the root of the site |
| `build.py` | Reads the libraries, checks them, turns their Markdown into HTML and fills the templates |

## For agents

The site has a second face, for AI agents: the same content as plain markdown, without styles,
scripts or tabs.

| URL | What it is |
|---|---|
| `/doc/` | Every library, in the groups of `site.toml`: version, description, `pip install`, repository, the exact versions it pins of the others and those that pin it, and its pages as markdown, each with the line that follows it in the README list |
| `/llms.txt` | The same index as markdown, following [llms.txt](https://llmstxt.org) |
| `/doc/<lib>/index.md` | The README of a library |
| `/doc/<lib>/<path>.md` | A doc, at the path of its HTML page: `/func-to-web/design/router/` is `/doc/func-to-web/design/router.md` |

A markdown page is its source with the links rewritten as absolute URLs: to another page, its
markdown copy; to an image, the one the site publishes; to any other file of the repository, that
file on GitHub at the release tag. The HTML pages and the markdown pages resolve links with the
same function, so both always point to the same place. Every human page names its markdown copy in
`<link rel="alternate" type="text/markdown">`; the home page and the dependencies view name
`/llms.txt`.

## How it updates

`.github/workflows/deploy.yml` rebuilds the site from scratch and deploys it to GitHub Pages on a
push to `main` and every hour. The libraries do not know the site exists: a new release shows up
within the hour. If a build fails, the site stays as it was and the reason is sent to the
ntfy topic `offerepos`.

## What a library needs

- **`pyproject.toml`** with `name`, `description` (its line on the home page) and the version:
  `version`, or a file named by `[tool.hatch.version] path` or `[tool.setuptools.dynamic]
  version = {attr = ...}` holding `__version__ = "X.Y.Z"`.
- **Release tags `v<major>.<minor>.<patch>`**, each holding that version, and published: PyPI
  has that version.
- **`CHANGELOG.md`** whose first entry is the released version.
- **A `LICENSE` file**, and the license declared in `pyproject.toml` (an SPDX expression, a text
  or an OSI classifier); when it declares MIT, the file holds the MIT text.
- **Python classifiers within `requires-python`**: none for a version it does not support.
- **Every URL in `[project.urls]` answering.**
- **Dependencies on other libraries of the site pinned exactly**, `name==X.Y.Z`: a new
  release of one can never change what an installed release of another does.
- **`README.md`** titled with the package or repository name, without a version, and with a
  `## Documentation` section that lists every `.md` under `docs/`,
  subfolders included, one per line, in reading order. That list is the library's menu on the site:

  ```markdown
  ## Documentation

  - [Getting started](docs/getting-started.md): install and a first example.
  ```

- **`docs/*.md`**, one page per topic, none named `docs/index.md` (its markdown copy would be
  the README's). Images go in `docs/images/`. Links are relative, as they work on GitHub
  (`usage.md#options`).

The build fails when a library breaks any of these, and the reason goes to the ntfy topic
`offerepos`. What the site shows about a library, its version, dependencies, license and pins,
is read from its released `pyproject.toml` on every build, never written by hand.

To add a library, add it to `site.toml`.
