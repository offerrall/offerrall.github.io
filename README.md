# offerrall.github.io

The site of my Python libraries, and the rules every one of them follows. The site holds no
content of its own: it reads each library listed in `site.toml` at its latest release and checks
it against the rules below. When a library breaks one, the site stays as it was and the reason is
sent to the ntfy topic `offerepos`.

## The rules

A library listed in `site.toml` follows all of these.

### Name

- **One name everywhere**: lowercase letters and digits only (`pytypehint`), the same on PyPI,
  for the GitHub repository and for the package you import.
- Libraries named before this rule are marked `name_predates_convention = true` in `site.toml`,
  because a PyPI name cannot change. A marked library that follows the rule must lose the mark.

### Release

- **A release is a GitHub tag `vX.Y.Z`**, and the package at that tag has version `X.Y.Z`.
- **Every release is on PyPI.**
- **`CHANGELOG.md` starts with the entry for the release.**

### Package metadata (`pyproject.toml`)

- **A `name` and a `description`.** The description is the library's line on the site.
- **A license, declared and shipped**: `pyproject.toml` declares it and a `LICENSE` file holds it.
  When the license is MIT, the file holds the MIT text.
- **Python classifiers only for supported versions**: none below `requires-python`.
- **Every URL in `[project.urls]` works.**
- **Dependencies on another of these libraries are pinned exactly** (`pytypehint==1.2.1`): a new
  release of one never changes what an installed release of another does. Dependencies on other
  packages are free.

### Documentation

- **All the documentation lives in `README.md` and `docs/`**: no other README anywhere in the
  repository.
- **`README.md` is titled with the library's name**, without a version (the name of the repository
  is accepted too).
- **The README has a `## Documentation` section listing every page under `docs/`**, subfolders
  included, one per line, in reading order, optionally followed by a one-line description. That
  list is the library's menu on the site:

  ```markdown
  ## Documentation

  - [Getting started](docs/getting-started.md): install and a first example.
  - [Design notes](docs/design/architecture.md): why the layers are split as they are.
  ```

- **Every page under `docs/` is in that list**, every entry of the list exists, and no page is
  named `docs/index.md`.
- **Every relative link works**: to a file of the repository, and to a heading (`#anchor`) of a
  page. Links are written as they work on GitHub. The site publishes the images in
  `docs/images/`; any other file is linked on GitHub.

## What the site shows

Everything the site says about a library is read from its latest release on every build, never
written by hand: its version, description, license, Python versions and dependencies as its
`pyproject.toml` declares them, its GitHub stars, its docs, and the exact versions the libraries
pin of each other. If the site says a library has two dependencies, its released `pyproject.toml`
declares those two.

- **Home**: the ten libraries with the most GitHub stars, then every library by group.
- **A library's page**: what its `pyproject.toml` declares, then its README and its docs.
- **`/dependencies/`**: the exact pins between the libraries, and the packages from PyPI each one
  needs to run.
- **`/doc/` and `/llms.txt`**: the same content as markdown, for AI agents.

## How it updates

The site is rebuilt from scratch on a push to this repository and every hour, so a new release of
a library shows up within the hour. The libraries do not know the site exists.

To add a library, add it to `site.toml` with its group.

## Technical reference

```bash
GITHUB_TOKEN=$(gh auth token) uv run build.py   # the latest releases, into _site/
uv run build.py --local                         # the working trees next to this repo (../<repo>)
python -m http.server -d _site
```

The token avoids GitHub's limit on anonymous API calls, used for the stars; the deploy workflow
passes its own.

| Path | What it is |
|---|---|
| `site.toml` | The site's title, description, URL and links, and the libraries with their groups |
| `build.py` | Reads the libraries, checks the rules, turns their markdown into HTML and fills the templates |
| `theme/` | The design: `base.html`, `home.html`, `page.html` and `dependencies.html` for people, `doc.html` and `llms.txt` for agents ([Jinja](https://jinja.palletsprojects.com/) templates), and `static/`, copied as is to the root of the site |
| `.github/workflows/deploy.yml` | The hourly rebuild, the deploy to GitHub Pages and the ntfy message on failure |

How the rules read their data:

- **The release** is the highest tag `vX.Y.Z`. Its version is read where `pyproject.toml` declares
  it: `version`, a `[tool.hatch.version] path`, or a `[tool.setuptools.dynamic] version = {attr}`
  whose file holds `__version__ = "X.Y.Z"`.
- **The license** is an SPDX expression, a `{text}`, or an OSI classifier with `license = {file}`.
- **A URL works** when it answers 200, following redirects.
- **The imported package** is a folder named like the library, under `src/` or at the root, with
  an `__init__.py`.
- **Another README** is any file named `README*` outside the repository root, hidden folders
  excepted.

### For agents

| URL | What it is |
|---|---|
| `/doc/` | Every library, in the groups of `site.toml`: version, description, `pip install`, repository, Python, license, dependencies, the exact versions it pins of the others and those that pin it, and its pages as markdown |
| `/llms.txt` | The same index as markdown, following [llms.txt](https://llmstxt.org) |
| `/doc/<lib>/index.md` | The README of a library |
| `/doc/<lib>/<path>.md` | A doc, at the path of its HTML page: `/func-to-web/design/router/` is `/doc/func-to-web/design/router.md` |

A markdown page is its source with the links rewritten as absolute URLs: to another page, its
markdown copy; to an image, the one the site publishes; to any other file of the repository, that
file on GitHub at the release tag. HTML and markdown pages resolve links with the same function.
Every human page names its markdown copy in `<link rel="alternate" type="text/markdown">`.
