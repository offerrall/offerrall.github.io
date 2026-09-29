# offerrall.github.io

The site of my libraries, in Python, C and C++, and the rules every one of them follows. The site holds no
content of its own: it reads each library listed in `site.toml` at its latest release and checks
it against the rules below. When a library breaks one, the site stays as it was and the reason is
sent to the ntfy topic `offerepos`.

## The rules

A library listed in `site.toml` follows all of these. A library with a `pyproject.toml` is a
Python library; one with a `CMakeLists.txt` is a C or C++ library. Most rules hold for all of
them; the metadata has its own rules for each.

### Name

- **One name everywhere**: lowercase letters and digits only (`pytypehint`), the same for the
  GitHub repository and for what a user writes: in Python the name on PyPI and the package you
  import, in C and C++ the name of `project()`, `find_package(<name>)` and the target
  `<name>::<name>`.
- Libraries named before this rule are marked `name_predates_convention = true` in `site.toml`,
  because a PyPI name cannot change. A marked library that follows the rule must lose the mark.

### Release

- **A release is a GitHub tag `vX.Y.Z`**, and the package at that tag has version `X.Y.Z`.
- **A Python release is on PyPI.**
- **`CHANGELOG.md` starts with the entry for the release.**
- **A license is shipped**: a `LICENSE` file holds it.

### Python metadata (`pyproject.toml`)

- **A `name` and a `description`.** The description is the library's line on the site.
- **A license, declared and shipped**: `pyproject.toml` declares it and a `LICENSE` file holds it.
  When the license is MIT, the file holds the MIT text.
- **Python classifiers only for supported versions**: none below `requires-python`.
- **Every URL in `[project.urls]` works.**
- **Dependencies on another of these libraries are pinned exactly** (`pytypehint==1.2.1`): a new
  release of one never changes what an installed release of another does. Dependencies on other
  packages are free.
- **`[project.urls]` has `Documentation = "https://offerrall.github.io/<name>/"`.**

### C and C++ metadata (`CMakeLists.txt`)

- **`project()` declares the library**: its name, a `VERSION X.Y.Z` (or a `VERSION` file holding
  it, which `project()` reads), a `DESCRIPTION`, which is the library's line on the site,
  `HOMEPAGE_URL "https://offerrall.github.io/<name>/"`, and `LANGUAGES C` or `LANGUAGES CXX`.

  ```cmake
  project(imagekit VERSION 1.0.0
      DESCRIPTION "GPU image processing with WebGPU"
      HOMEPAGE_URL "https://offerrall.github.io/imagekit/"
      LANGUAGES CXX)
  ```

- **One target, installed for `find_package`**: `add_library(<name> ...)`, exported with
  `install(EXPORT ... NAMESPACE <name>::)`, so a user writes `find_package(<name>)` and links
  `<name>::<name>`.
- **The language standard is declared**: `target_compile_features(<name> PUBLIC cxx_std_NN)`
  (`c_std_NN` in C), or `CMAKE_CXX_STANDARD` / `CMAKE_C_STANDARD`.
- **The license is one the site recognizes by its text**: MIT, Apache-2.0, BSD-3-Clause,
  BSD-2-Clause, GPL-3.0, LGPL-3.0, MPL-2.0 or Zlib.
- **The README declares the dependencies** in a `## Dependencies` section after
  `## Documentation`, since CMake has no one place that declares them. One line per
  dependency: its name, its version in pip's notation (`==`, `>=`, `<`, `,` between clauses),
  and marks in parentheses: `bundled` when CMake downloads and builds it, `optional: <option>`
  when only that CMake option needs it. Without marks it comes from the system. A short
  description may follow a colon. `None.` when there are none. Another of these libraries is
  pinned exactly, as in Python.

  ```markdown
  ## Dependencies

  - zstd `==1.5.6` (bundled): compressed project files
  - lcms2 `>=2.16`
  - harfbuzz `>=2.6` (optional: IMAGEKIT_BUILD_TEXT)
  ```

### Documentation

- **`README.md` is a short entrance**: the library's name as title, without a version (the name of
  the repository is accepted too), a presentation of at most 40 lines with at most one block of
  code, the line `The full documentation is at https://offerrall.github.io/<name>/.`, and a
  `## Documentation` section, its only section (a C or C++ library adds `## Dependencies` after it).
- **The `## Documentation` list is the site's menu, and it links the site**: every page under
  `docs/`, subfolders included, one per line, in reading order, each linked by its address on the
  site, so a reader on GitHub or PyPI lands there. `docs/<page>.md` is
  `https://offerrall.github.io/<name>/<page>/`, and `docs/overview.md` is the library's own
  address, listed first. An entry may be followed by a one-line description. The list links nothing
  else (the site adds the changelog), and has no subheadings.

  ```markdown
  ## Documentation

  - [Overview](https://offerrall.github.io/pytypehint/): what it is and how it works.
  - [Getting started](https://offerrall.github.io/pytypehint/getting-started/): a first example.
  ```

- **`docs/overview.md` is the introduction**: on the site, the Overview page is the README's
  presentation followed by it.
- **Every page under `docs/` is in the list**, every entry of the list exists, and no page is
  named `docs/index.md`. All the documentation lives in `README.md` and `docs/`: no other README
  anywhere in the repository.
- **A page starts with its one title** (`# Title`), and its entry in the list uses that same title.
- **Notes for maintainers close the page they explain**, folded, never as pages of their own:

  ```markdown
  <details>
  <summary>How it works inside</summary>

  Why the upload endpoint does not apply the size bounds of a field...

  </details>
  ```

- **Every block of code names its language** (` ```python `, ` ```bash `, ` ```text `...).
- **No badges** (`img.shields.io`): the site shows version, language and license itself.
- **Every relative link works**: to a file of the repository, and to a heading (`#anchor`) of a
  page. Links are written as they work on GitHub, and no page links to `README.md`. The site
  publishes the images in `docs/images/`; any other file is linked on GitHub.
- **Another of these libraries is linked by its page on the site**, not by its GitHub repository.
- **`CHANGELOG.md` headings are `## X.Y.Z - YYYY-MM-DD`**, newest first, with real dates.

## What the site shows

Everything the site says about a library is read from its latest release on every build, never
written by hand: its version, description, license, language (Python versions, or the C or C++
standard) and dependencies, as its `pyproject.toml`, `CMakeLists.txt` or README declares them,
its GitHub stars, its docs, and the exact versions the libraries pin of each other. If the site
says a library has two dependencies, its release declares those two. Names appear in the form PyPI displays: lowercase, separators as a single
hyphen (`pygrbl_streamer` is shown, installed and linked as `pygrbl-streamer`).

- **Home**: the ten libraries with the most GitHub stars, then every library by group.
- **A library's page**: how to get it (`pip install <name>`, or CMake's `FetchContent` at the
  release tag and the target `<name>::<name>`), what its `pyproject.toml` or `CMakeLists.txt` declares, then its Overview (the README's
  presentation and `docs/overview.md`), its docs, and its changelog.
- **`/dependencies/`**: the exact pins between the libraries, the packages from elsewhere each
  one needs, and everything pip installs with each Python library.
- **`/doc/` and `/llms.txt`**: the same content as markdown, for AI agents, with the rules above
  and this README, so an agent knows what a new library must be.

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
| `site.toml` | The site's title, description, URL, repository and links, and the libraries with their groups |
| `build.py` | Reads the libraries, checks the rules, turns their markdown into HTML and fills the templates |
| `theme/` | The design: `base.html`, `home.html`, `page.html` and `dependencies.html` for people, `doc.html` and `llms.txt` for agents ([Jinja](https://jinja.palletsprojects.com/) templates), and `static/`, copied as is to the root of the site |
| `.github/workflows/deploy.yml` | The hourly rebuild, the deploy to GitHub Pages and the ntfy message on failure |

How the rules read their data:

- **The release** is the highest tag `vX.Y.Z`. Its version is read where `pyproject.toml` declares
  it: `version`, a `[tool.hatch.version] path`, or a `[tool.setuptools.dynamic] version = {attr}`
  whose file holds `__version__ = "X.Y.Z"`. In `CMakeLists.txt`, the `VERSION` of `project()`
  when it is written as `X.Y.Z`, else the `VERSION` file.
- **`CMakeLists.txt`** is read as its commands, with comments removed, never run: `project()`,
  `add_library()`, `install(EXPORT)`, `target_compile_features()` and `set(CMAKE_CXX_STANDARD)`. Only the top-level `CMakeLists.txt` counts, not `include()`d files.
- **The license** is an SPDX expression, a `{text}`, or an OSI classifier with `license = {file}`.
  In C and C++, the text of `LICENSE` (or the shortest-named `LICENSE*`/`COPYING*` file).
- **A URL works** when it answers 200, following redirects. The library's page on the site is
  checked by its value instead, since it does not exist before the first build.
- **The imported package** is a folder named like the library, under `src/` or at the root, with
  an `__init__.py`.
- **Another README** is any file named `README*` outside the repository root, hidden folders
  excepted.

### For agents

| URL | What it is |
|---|---|
| `/doc/` | The rules every library follows, then every library, in the groups of `site.toml`: version, description, language, how to install it, repository, license, dependencies, the exact versions it pins of the others and those that pin it, and its pages as markdown |
| `/llms.txt` | The same index as markdown, following [llms.txt](https://llmstxt.org), linking the rules |
| `/doc/site.md` | This README: the rules, what the site shows and how it reads them |
| `/doc/<lib>/index.md` | The README of a library |
| `/doc/<lib>/<path>.md` | A doc, at the path of its HTML page: `/func-to-web/design/router/` is `/doc/func-to-web/design/router.md` |

A markdown page is its source with the links rewritten as absolute URLs: to another page, its
markdown copy; to an image, the one the site publishes; to any other file of the repository, that
file on GitHub at the release tag. HTML and markdown pages resolve links with the same function.
Every human page names its markdown copy in `<link rel="alternate" type="text/markdown">`.
