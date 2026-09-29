# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py", "mdit-py-plugins", "jinja2", "pygments"]
# ///
"""Build the site into _site/.

The content comes from site.toml and from each library's repository at its highest release
tag: pyproject.toml, README.md and docs/. The design is theme/: its templates and static/.

Run: uv run build.py            # the latest releases, cloned into .cache/
     uv run build.py --local    # the working trees next to this repo (../<repo>), to preview
"""

import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from pygments import highlight as pygmentize
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

ROOT = Path(__file__).parent
THEME = ROOT / "theme"
OUT = ROOT / "_site"
CACHE = ROOT / ".cache"

DOC_ITEM = re.compile(r"^- \[(?P<title>[^\]]+)\]\((?P<path>docs/(?:[\w.-]+/)*[\w.-]+\.md)\)"
                      r"(?:\s*[:—–-]?\s*(?P<about>.*))?")
HTML_URL = re.compile(r'\b(src|href)="([^"]+)"')
# Where markdown names a URL: a link or image, a reference definition, an HTML attribute.
# Code spans match too, so that the targets inside them are left as written.
MD_URL = re.compile(r"(?P<code>(?P<ticks>`+).+?(?<!`)(?P=ticks)(?!`))"
                    r"|(?P<lead>\]\(\s*<?|^ {0,3}\[[^\]]+\]:\s*<?|\b(?:src|href)=\")(?P<url>[^\s()<>\"]+)")
EXTERNAL = re.compile(r"^([a-z][a-z0-9+.-]*:|//)")
REQUIREMENT = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([^;]*)")


@dataclass(frozen=True)
class Page:
    source: str  # path inside the repository, e.g. "docs/usage.md"
    title: str
    url: str
    about: str  # what follows the link in the README list, "" for the README itself

    @property
    def markdown(self) -> str:
        """The URL of its markdown copy: /doc/<lib>/index.md for the README, else the page's path + .md."""
        return f"/doc{self.url}index.md" if self.source == "README.md" else f"/doc{self.url.rstrip('/')}.md"


@dataclass(frozen=True)
class Lib:
    name: str
    group: str
    description: str
    version: str
    repo: str  # https://github.com/owner/name
    ref: str  # the tag read, or "main" for a working tree
    stars: int
    requires: tuple[tuple[str, str], ...]  # (normalized name, version specifier) of each dependency
    project: dict  # the [project] table of its pyproject.toml, as read
    root: Path
    pages: tuple[Page, ...]  # the README first, then the docs in README order


def fail(errors: list[str]) -> None:
    if errors:
        print("\n".join(f"error: {e}" for e in errors), file=sys.stderr)
        sys.exit(1)


# Reading a library

def checkout(repo: str, local: bool) -> tuple[Path, str]:
    """The folder to read a library from, and the git ref it holds."""
    name = repo.split("/")[1]
    if local:
        root = (ROOT.parent / name).resolve()
        if not root.is_dir():
            fail([f"{repo}: no working tree at {root}"])
        return root, "main"
    url = f"https://github.com/{repo}.git"
    refs = subprocess.run(["git", "ls-remote", "--tags", "--refs", url],
                          capture_output=True, text=True, check=True).stdout
    versions = [tuple(map(int, v)) for v in re.findall(r"refs/tags/v(\d+)\.(\d+)\.(\d+)$", refs, re.M)]
    if not versions:
        fail([f"{repo}: no release tag v<major>.<minor>.<patch>"])
    tag = "v" + ".".join(map(str, max(versions)))
    root = CACHE / name
    if root.exists():
        shutil.rmtree(root)
    subprocess.run(["git", "-c", "advice.detachedHead=false", "clone", "--quiet", "--depth", "1",
                    "--branch", tag, url, str(root)], check=True)
    return root, tag


def declared_version(root: Path, pyproject: dict) -> str | None:
    """The version where pyproject.toml declares it: `version`, a hatch `path`, or a
    setuptools `attr`, whose file holds `__version__ = "X.Y.Z"`."""
    if "version" in pyproject["project"]:
        return pyproject["project"]["version"]
    tool = pyproject.get("tool", {})
    files = []
    if path := tool.get("hatch", {}).get("version", {}).get("path"):
        files = [root / path]
    elif attr := tool.get("setuptools", {}).get("dynamic", {}).get("version", {}).get("attr"):
        module = attr.rsplit(".", 1)[0].replace(".", "/")
        files = [base / f for base in (root / "src", root) for f in (f"{module}.py", f"{module}/__init__.py")]
    for file in files:
        if file.exists() and (found := re.search(r'^__version__ = "([^"]+)"', file.read_text(), re.M)):
            return found.group(1)
    return None


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirements(project: dict) -> tuple[tuple[str, str], ...]:
    found = (REQUIREMENT.match(spec) for spec in project.get("dependencies", []))
    return tuple((normalized(m[1]), m[2].strip()) for m in found if m)


def github_stars(repo: str) -> int:
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}",
                                     headers={"Accept": "application/vnd.github+json"})
    if token := os.environ.get("GITHUB_TOKEN"):
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request) as response:
        return json.load(response)["stargazers_count"]


def http_status(url: str) -> int:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "offerrall.github.io"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def declared_license(project: dict) -> str | None:
    """The license pyproject.toml declares: an SPDX expression, a text, or an OSI classifier."""
    license = project.get("license")
    if isinstance(license, str):
        return license
    if isinstance(license, dict) and license.get("text"):
        return license["text"]
    for classifier in project.get("classifiers", []):
        if classifier.startswith("License :: OSI Approved :: "):
            return classifier.removeprefix("License :: OSI Approved :: ").removesuffix(" License")
    return None


def release_problems(repo: str, root: Path, project: dict, version: str, local: bool) -> list[str]:
    """What every released library must have, besides its docs."""
    name, problems = project["name"], []

    changelog = root / "CHANGELOG.md"
    newest = changelog.exists() and re.search(r"^#+\s*\[?v?(\d+\.\d+\.\d+)", changelog.read_text(), re.M)
    if not newest or newest.group(1) != version:
        problems.append(f"{name}: CHANGELOG.md does not start with an entry for {version}")

    if not local and http_status(f"https://pypi.org/pypi/{name}/{version}/json") != 200:
        problems.append(f"{name}: PyPI has no {version}; the release did not publish")

    title = (root / "README.md").read_text().splitlines()[0]
    if title not in (f"# {name}", f"# {repo.split('/')[1]}"):
        problems.append(f"{name}: the README title is {title!r}; use the package or repository name")

    for label, url in project.get("urls", {}).items():
        if (status := http_status(url)) != 200:
            problems.append(f"{name}: [project.urls] {label} = {url} answers {status}")

    license_files = [f for f in root.iterdir()
                     if f.is_file() and f.name.upper().startswith(("LICENSE", "LICENCE", "COPYING"))]
    declared = declared_license(project)
    if not license_files:
        problems.append(f"{name}: no LICENSE file")
    elif not declared:
        problems.append(f"{name}: pyproject.toml declares no license")
    elif declared == "MIT" and "Permission is hereby granted, free of charge" not in license_files[0].read_text():
        problems.append(f"{name}: pyproject.toml declares MIT and {license_files[0].name} is not the MIT text")

    for stray in sorted(root.rglob("README*")):
        where = stray.relative_to(root)
        if where != Path("README.md") and not any(part.startswith(".") for part in where.parts):
            problems.append(f"{name}: {where} is documentation outside README.md and docs/")

    floor = re.search(r">=\s*3\.(\d+)", project.get("requires-python", ""))
    for classifier in project.get("classifiers", []):
        supported = re.fullmatch(r"Programming Language :: Python :: 3\.(\d+)", classifier)
        if supported and floor and int(supported.group(1)) < int(floor.group(1)):
            problems.append(f"{name}: classifier Python 3.{supported.group(1)} is below "
                            f"requires-python {project['requires-python']}")
    return problems


def name_problems(repo: str, root: Path, project: dict, predates: bool) -> list[str]:
    """One name everywhere: lowercase letters and digits only, the same on PyPI, for the
    repository and for the package you import. site.toml marks the libraries named before."""
    name, repo_name = project["name"], repo.split("/")[1]
    follows = bool(re.fullmatch(r"[a-z0-9]+", name)) and repo_name == name and any(
        (base / name / "__init__.py").exists() for base in (root / "src", root))
    if predates and follows:
        return [f"{name}: follows the naming convention; remove name_predates_convention from site.toml"]
    if not predates and not follows:
        return [f"{name}: the name must be lowercase letters and digits only, and the same for the "
                f"repository ({repo_name}) and for the imported package"]
    return []


def load_lib(repo: str, group: str, local: bool, predates: bool = False) -> Lib:
    root, ref = checkout(repo, local)
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    project = pyproject["project"]
    name = project["name"]
    version = declared_version(root, pyproject)
    if not version:
        fail([f"{repo}: pyproject.toml declares no version this build can read"])
    if not local and ref != f"v{version}":
        fail([f"{repo}: tag {ref} holds version {version}"])

    readme = (root / "README.md").read_text()
    section = re.search(r"^## Documentation\n(.*?)(?=^## |\Z)", readme, re.M | re.S)
    if not section:
        fail([f"{name}: README.md has no '## Documentation' section"])
    items = re.sub(r"\n[ \t]+(?=\S)", " ", section.group(1))  # an item wrapped over several lines
    listed = [m for line in items.splitlines() if (m := DOC_ITEM.match(line))]

    errors = []
    on_disk = {p.relative_to(root).as_posix() for p in (root / "docs").rglob("*.md")}
    for m in listed:
        if m["path"] not in on_disk:
            errors.append(f"{name}: README lists {m['path']}, which does not exist")
    for missing in sorted(on_disk - {m["path"] for m in listed}):
        errors.append(f"{name}: {missing} is not listed in README '## Documentation'")
    if "docs/index.md" in on_disk:
        errors.append(f"{name}: docs/index.md would have the markdown URL of the README, rename it")
    errors += release_problems(repo, root, project, version, local)
    errors += name_problems(repo, root, project, predates)
    fail(errors)

    pages = [Page("README.md", name, f"/{name}/", "")]
    # Menu labels are plain text: `run()` in the README list reads as run() in the menu.
    pages += [Page(m["path"], m["title"].replace("`", ""),
                   f"/{name}/{m['path'].removeprefix('docs/').removesuffix('.md')}/",
                   (m["about"] or "").strip().removesuffix("."))
              for m in listed]
    return Lib(name, group, project["description"], version, f"https://github.com/{repo}",
               ref, github_stars(repo), requirements(project), project, root, tuple(pages))


# Markdown to HTML

def highlight(code: str, lang: str, _attrs: str) -> str:
    """The colored HTML of a fenced block in a known language; "" leaves it plain."""
    try:
        lexer = get_lexer_by_name(lang)
    except ClassNotFound:
        return ""
    return pygmentize(code, lexer, HtmlFormatter(nowrap=True))


md = (
    MarkdownIt("commonmark", {"html": True, "highlight": highlight})
    .enable(["table", "strikethrough"])
    .use(anchors_plugin, max_level=6, permalink=True, permalinkSymbol="#", permalinkBefore=True)
)


def resolve(target: str, source: str, lib: Lib, ids: dict[str, set[str]], errors: list[str],
            origin: str | None = None) -> str:
    """Map a link written for GitHub to its place on the site: the page it names, or, given the
    site's `origin`, the absolute URL of that page's markdown copy."""
    if EXTERNAL.match(target):
        return target
    path, _, anchor = target.partition("#")
    rel = posixpath.normpath(posixpath.join(posixpath.dirname(source), path)) if path else source
    if anchor and rel in ids and anchor not in ids[rel]:
        errors.append(f"{lib.name}/{source}: no heading for {target}")
    frag = f"#{anchor}" if anchor else ""
    if not path and origin is None:
        return frag
    for page in lib.pages:
        if page.source == rel:
            return (page.url if origin is None else origin + page.markdown) + frag
    if rel.startswith("docs/images/"):
        return f"{origin or ''}/{lib.name}/images/{rel.removeprefix('docs/images/')}"
    if not (lib.root / rel).exists():
        errors.append(f"{lib.name}/{source}: broken link {target}")
    kind = "tree" if (lib.root / rel).is_dir() else "blob"
    return f"{lib.repo}/{kind}/{lib.ref}/{rel}{frag}"


def render_docs(lib: Lib, origin: str, errors: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """The HTML of each page of a library and its markdown copy, by source path, with their
    links resolved."""
    texts = {p.source: (lib.root / p.source).read_text() for p in lib.pages}
    parsed = {source: md.parse(text) for source, text in texts.items()}
    ids = {source: {t.attrGet("id") for t in tokens if t.type == "heading_open"}
           for source, tokens in parsed.items()}

    def copy(source: str) -> str:
        code = {i for t in parsed[source] if t.type in ("fence", "code_block") for i in range(*t.map)}
        lines = texts[source].splitlines(keepends=True)
        return "".join(line if i in code else MD_URL.sub(
            lambda m: m[0] if m["code"] else m["lead"] + resolve(m["url"], source, lib, ids, errors, origin),
            line) for i, line in enumerate(lines))

    markdown = {source: copy(source) for source in parsed}

    def fix(token, source):
        for attr in ("href", "src"):
            if token.attrGet(attr):
                token.attrSet(attr, resolve(token.attrGet(attr), source, lib, ids, errors))
        if token.type in ("html_block", "html_inline"):
            token.content = HTML_URL.sub(
                lambda m: f'{m[1]}="{resolve(m[2], source, lib, ids, errors)}"', token.content)

    for source, tokens in parsed.items():
        for token in tokens:
            fix(token, source)
            for child in token.children or ():
                fix(child, source)
    return {source: md.renderer.render(tokens, md.options, {}) for source, tokens in parsed.items()}, markdown


# Dependencies between the libraries

@dataclass(frozen=True)
class Link:
    user: Lib
    used: Lib
    spec: str  # e.g. "==1.0.0", or "" when any version will do

    @property
    def behind(self) -> bool:
        """Pinned to a version older than the one the site shows."""
        return self.spec.startswith("==") and self.spec[2:].strip() != self.used.version


def dependency_links(libs: list[Lib]) -> list[Link]:
    """What each library declares in [project] dependencies, among the libraries listed."""
    by_name = {normalized(lib.name): lib for lib in libs}
    return [Link(lib, by_name[name], spec) for lib in libs for name, spec in lib.requires
            if name in by_name and by_name[name] is not lib]


def facts(lib: Lib, libs: list[Lib]) -> dict:
    """What the Overview shows before the README: its pyproject.toml, as written."""
    pages = {normalized(other.name): other.pages[0].url for other in libs}

    def requirement(text: str) -> dict:
        found = REQUIREMENT.match(text)
        return {"text": text, "url": pages.get(normalized(found[1])) if found else None}

    project = lib.project
    return {
        "python": project.get("requires-python"),
        "license": declared_license(project),
        "dependencies": [requirement(text) for text in project.get("dependencies", [])],
        "extras": [(extra, [requirement(text) for text in texts])
                   for extra, texts in project.get("optional-dependencies", {}).items()],
        "commands": list(project.get("scripts", {})),
    }


def external_dependencies(libs: list[Lib]) -> list[dict]:
    """The packages from elsewhere that the libraries need, each with who needs it and how,
    the most shared first."""
    ours = {normalized(lib.name) for lib in libs}
    packages: dict[str, dict] = {}
    for lib in libs:
        for text in lib.project.get("dependencies", []):
            found = REQUIREMENT.match(text)
            if not found or normalized(found[1]) in ours:
                continue
            package = packages.setdefault(normalized(found[1]), {"name": found[1], "users": []})
            package["users"].append({"lib": lib, "spec": found[2].strip()})
    return sorted(packages.values(), key=lambda p: (-len(p["users"]), p["name"].lower()))


def unpinned(links: list[Link]) -> list[str]:
    """A library that needs another of these must pin it exactly, so a new release of one
    can never change what an installed release of the other does."""
    return [f"{link.user.name}: needs {link.used.name} {link.spec or '(any version)'}, "
            f"pin it exactly, e.g. {link.used.name}=={link.used.version}"
            for link in links if not re.fullmatch(r"==\s*\d+(\.\d+)*", link.spec)]


NODE_W, NODE_H, COLUMN_W, ROW_H, PAD = 190, 46, 360, 72, 24


def graph_layout(libs: list[Lib], links: list[Link]) -> dict:
    """Columns by depth: a library sits one column right of the deepest one it uses."""
    linked = [lib for lib in libs if any(lib in (link.user, link.used) for link in links)]
    depth: dict[str, int] = {}

    def depth_of(lib: Lib) -> int:
        if lib.name not in depth:
            depth[lib.name] = 0  # a cycle stops here instead of recursing forever
            depth[lib.name] = 1 + max((depth_of(l.used) for l in links if l.user is lib), default=-1)
        return depth[lib.name]

    for lib in linked:
        depth_of(lib)
    columns = [[lib for lib in linked if depth[lib.name] == c] for c in range(max(depth.values(), default=-1) + 1)]

    row: dict[str, float] = {}
    for c, column in enumerate(columns):
        def place(lib: Lib) -> tuple:
            used = [row[l.used.name] for l in links if l.user is lib and l.used.name in row]
            return (sum(used) / len(used) if used else 0, lib.name)
        column.sort(key=place)
        for i, lib in enumerate(column):
            row[lib.name] = i

    rows = max((len(column) for column in columns), default=0)
    boxes = {}
    for c, column in enumerate(columns):
        top = PAD + (rows - len(column)) * ROW_H / 2
        for i, lib in enumerate(column):
            boxes[lib.name] = {"lib": lib, "x": PAD + c * COLUMN_W, "y": top + i * ROW_H}
    edges = []
    for link in links:
        a, b = boxes[link.user.name], boxes[link.used.name]
        # Arrows into one box arrive spread along its side, in the order of their sources.
        arriving = sorted((l for l in links if l.used is link.used), key=lambda l: boxes[l.user.name]["y"])
        share = (arriving.index(link) + 1) / (len(arriving) + 1)
        ax, ay = a["x"], a["y"] + NODE_H / 2
        bx, by = b["x"] + NODE_W + 6, b["y"] + NODE_H * share
        edges.append({"link": link, "label": (ax - 10, ay - 6),
                      "path": f"M{ax},{ay} C{ax - 90},{ay} {bx + 90},{by} {bx},{by}"})
    width = 2 * PAD + max(len(columns) - 1, 0) * COLUMN_W + NODE_W
    height = 2 * PAD + max(rows - 1, 0) * ROW_H + NODE_H if linked else PAD

    # Libraries that need none of the others and that none needs: a row of their own below.
    standalone = [lib for lib in libs if lib not in linked]
    label_y = None
    if standalone:
        label_y = height + 16
        top = label_y + 18
        for i, lib in enumerate(standalone):
            boxes[lib.name] = {"lib": lib, "x": PAD + i * (NODE_W + 30), "y": top}
        width = max(width, 2 * PAD + len(standalone) * (NODE_W + 30) - 30)
        height = top + NODE_H + PAD
    return {"boxes": list(boxes.values()), "edges": edges, "node_w": NODE_W, "node_h": NODE_H,
            "width": width, "height": height, "standalone_label": (PAD, label_y) if standalone else None}


# Writing the site

def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def main() -> None:
    config = tomllib.loads((ROOT / "site.toml").read_text())
    local = "--local" in sys.argv[1:]
    libs = [load_lib(entry["repo"], entry["group"], local, entry.get("name_predates_convention", False))
            for entry in config["lib"]]
    # The home page: the ten with the most stars first, then each group, by stars too.
    by_stars = sorted(libs, key=lambda lib: (-lib.stars, lib.name))
    sections = [{"name": "Top", "libs": by_stars[:10]}]
    for group in dict.fromkeys(lib.group for lib in libs):
        sections.append({"name": group, "libs": [lib for lib in by_stars if lib.group == group]})
    links = dependency_links(libs)
    fail(unpinned(links))

    theme = Environment(loader=FileSystemLoader(THEME), autoescape=select_autoescape(["html"]),
                        undefined=StrictUndefined)
    theme.filters["inline"] = md.renderInline
    site = config["site"]
    origin = site["url"].rstrip("/")

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(THEME / "static", OUT)
    (OUT / ".nojekyll").touch()
    write(OUT / "index.html", theme.get_template("home.html").render(
        site=site, sections=sections, count=len(libs)))
    graph = graph_layout(libs, links)
    linked = {name for link in links for name in (link.user.name, link.used.name)}
    write(OUT / "dependencies" / "index.html", theme.get_template("dependencies.html").render(
        site=site, graph=graph, links=links, count=len(libs), external=external_dependencies(libs),
        standalone=[lib for lib in libs if lib.name not in linked]))

    # The pages for agents: an index, llms.txt, and every page as markdown. Libraries in the order
    # of site.toml, grouped as on the home page.
    catalog = [{"name": group, "libs": [{"lib": lib, "facts": facts(lib, libs),
                                         "uses": [link for link in links if link.user is lib],
                                         "used_by": [link for link in links if link.used is lib]}
                                        for lib in libs if lib.group == group]}
               for group in dict.fromkeys(lib.group for lib in libs)]
    write(OUT / "doc" / "index.html", theme.get_template("doc.html").render(site=site, catalog=catalog))
    write(OUT / "llms.txt", theme.get_template("llms.txt").render(site=site, origin=origin, catalog=catalog))

    errors: list[str] = []
    for lib in libs:
        html, markdown = render_docs(lib, origin, errors)
        for i, page in enumerate(lib.pages):
            write(OUT / page.markdown.lstrip("/"), markdown[page.source])
            write(OUT / page.url.strip("/") / "index.html", theme.get_template("page.html").render(
                site=site, lib=lib, page=page, content=html[page.source],
                facts=facts(lib, libs),
                uses=[link for link in links if link.user is lib],
                used_by=[link for link in links if link.used is lib],
                prev=lib.pages[i - 1] if i > 0 else None,
                next=lib.pages[i + 1] if i + 1 < len(lib.pages) else None))
        images = lib.root / "docs" / "images"
        if images.is_dir():
            shutil.copytree(images, OUT / lib.name / "images")
    fail(list(dict.fromkeys(errors)))  # the HTML and the markdown of a page report the same link
    print(f"built {sum(len(lib.pages) for lib in libs) + 1} pages into {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
