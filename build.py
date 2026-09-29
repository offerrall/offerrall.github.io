# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py", "mdit-py-plugins", "jinja2", "pygments"]
# ///
"""Build the site into _site/.

The content comes from site.toml and from each library's repository at its highest release
tag: pyproject.toml for Python or CMakeLists.txt for C and C++, README.md and docs/. The design is theme/: its templates and static/.

Run: uv run build.py            # the latest releases, cloned into .cache/
     uv run build.py --local    # the working trees next to this repo (../<repo>), to preview
"""

import datetime
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
    project: dict  # the [project] table of its pyproject.toml, or what its CMakeLists.txt declares
    root: Path
    pages: tuple[Page, ...]  # the README first, then the docs in README order
    language: str  # "Python", "C" or "C++"

    @property
    def manifest(self) -> str:
        """The file its metadata is read from."""
        return "pyproject.toml" if self.language == "Python" else "CMakeLists.txt"

    @property
    def install(self) -> str:
        """How a user gets it: from PyPI, or fetched by CMake at the release the site shows."""
        if self.language == "Python":
            return f"pip install {self.name}"
        return (f"FetchContent_Declare({self.name} GIT_REPOSITORY {self.repo} GIT_TAG v{self.version})\n"
                f"FetchContent_MakeAvailable({self.name})")

    @property
    def install_label(self) -> str:
        """The install line, short enough for a button."""
        return self.install if self.language == "Python" else f"FetchContent {self.name} v{self.version}"


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


def license_file(root: Path) -> Path | None:
    """LICENSE, or the shortest-named of its variants (LICENSE.md, COPYING...)."""
    found = [f for f in root.iterdir() if f.is_file() and f.name.upper().startswith(("LICENSE", "LICENCE", "COPYING"))]
    return min(found, key=lambda f: (len(f.name), f.name), default=None)


MIT_TEXT = "Permission is hereby granted, free of charge"
# The licenses a LICENSE file is recognized as, by phrases of their text, for C and C++.
LICENSE_TEXTS = {
    "MIT": (MIT_TEXT,),
    "Apache-2.0": ("Apache License", "Version 2.0"),
    "BSD-3-Clause": ("Redistribution and use in source and binary forms", "Neither the name"),
    "BSD-2-Clause": ("Redistribution and use in source and binary forms",),
    "GPL-3.0": ("GNU GENERAL PUBLIC LICENSE", "Version 3"),
    "LGPL-3.0": ("GNU LESSER GENERAL PUBLIC LICENSE", "Version 3"),
    "MPL-2.0": ("Mozilla Public License Version 2.0",),
    "Zlib": ("This software is provided 'as-is', without any express or implied",),
}


def recognized_license(text: str) -> str | None:
    return next((spdx for spdx, phrases in LICENSE_TEXTS.items() if all(p in text for p in phrases)), None)


def release_problems(repo: str, root: Path, name: str, version: str) -> list[str]:
    """What every released library must have, in any language, besides its docs."""
    problems = []

    changelog = root / "CHANGELOG.md"
    newest = changelog.exists() and re.search(r"^#+\s*\[?v?(\d+\.\d+\.\d+)", changelog.read_text(), re.M)
    if not newest or newest.group(1) != version:
        problems.append(f"{name}: CHANGELOG.md does not start with an entry for {version}")

    title = (root / "README.md").read_text().splitlines()[0]
    if title not in (f"# {name}", f"# {repo.split('/')[1]}"):
        problems.append(f"{name}: the README title is {title!r}; use the package or repository name")

    if not license_file(root):
        problems.append(f"{name}: no LICENSE file")

    for stray in sorted(root.rglob("README*")):
        where = stray.relative_to(root)
        if where != Path("README.md") and not any(part.startswith(".") for part in where.parts):
            problems.append(f"{name}: {where} is documentation outside README.md and docs/")
    return problems


def site_address(url: str, name: str) -> bool:
    return url.rstrip("/") == f"{SITE_URL}/{name}"


def python_problems(root: Path, project: dict, version: str, local: bool) -> list[str]:
    """What a Python library must have: its release on PyPI, and a pyproject.toml that holds."""
    name, problems = project["name"], []

    if not local and http_status(f"https://pypi.org/pypi/{name}/{version}/json") != 200:
        problems.append(f"{name}: PyPI has no {version}; the release did not publish")

    # The library's page on the site is checked by its value: it does not exist before the first build.
    for label, url in project.get("urls", {}).items():
        if not site_address(url, normalized(name)) and (status := http_status(url)) != 200:
            problems.append(f"{name}: [project.urls] {label} = {url} answers {status}")

    declared, file = declared_license(project), license_file(root)
    if not declared:
        problems.append(f"{name}: pyproject.toml declares no license")
    elif file and declared == "MIT" and MIT_TEXT not in file.read_text():
        problems.append(f"{name}: pyproject.toml declares MIT and {file.name} is not the MIT text")

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


CMAKE_ARGUMENT = re.compile(r'"((?:[^"\\]|\\.)*)"|([^\s"]+)')


def cmake_commands(text: str) -> list[tuple[str, list[str]]]:
    """Each command of a CMakeLists.txt: its name in lowercase and its arguments, with the
    quotes removed."""
    text = re.sub(r"#\[(=*)\[.*?\]\1\]", "", text, flags=re.S)  # bracket comments
    text = re.sub(r'("(?:[^"\\]|\\.)*")|#[^\n]*', lambda m: m[1] or "", text)  # line comments
    commands, at = [], 0
    while found := re.compile(r"\b([A-Za-z_]\w*)\s*\(").search(text, at):
        level, end = 1, found.end()
        while level and end < len(text):
            if text[end] == '"':
                end = re.compile(r'"(?:[^"\\]|\\.)*"').match(text, end).end()
                continue
            level += {"(": 1, ")": -1}.get(text[end], 0)
            end += 1
        arguments = [m[1] if m[1] is not None else m[2]
                     for m in CMAKE_ARGUMENT.finditer(text[found.end():end - 1]) if m[0] not in "()"]
        commands.append((found[1].lower(), arguments))
        at = end
    return commands


CMAKE_KEYWORDS = ("VERSION", "DESCRIPTION", "HOMEPAGE_URL", "LANGUAGES")


def cmake_project(root: Path) -> dict:
    """What a CMakeLists.txt declares, as the site shows it: the keywords of project(), the
    version (there or in a VERSION file), the language standard, and its exported targets.
    Not its dependencies: CMake has no one place that declares them, so the docs say them."""
    commands = cmake_commands((root / "CMakeLists.txt").read_text())
    arguments = next((a for n, a in commands if n == "project"), None)
    if not arguments:
        fail([f"{root.name}: CMakeLists.txt has no project()"])
    project = {"name": arguments[0], "languages": []}
    key = None
    for word in arguments[1:]:
        if word in CMAKE_KEYWORDS:
            key = word
        elif key == "LANGUAGES":
            project["languages"].append(word)
        elif key:
            project[key.lower()] = word
    if not re.fullmatch(r"\d+\.\d+\.\d+", project.get("version", "")) and (root / "VERSION").exists():
        project["version"] = (root / "VERSION").read_text().strip()
    standards = re.findall(r"\b(c|cxx)_std_(\d+)\b",
                           " ".join(" ".join(a) for n, a in commands if n == "target_compile_features"))
    standards += [("cxx" if a[0] == "CMAKE_CXX_STANDARD" else "c", a[1])
                  for n, a in commands if n == "set" and len(a) > 1 and a[0] in ("CMAKE_CXX_STANDARD", "CMAKE_C_STANDARD")]
    project["standards"] = {lang: number for lang, number in standards}
    project["exports"] = [a[a.index("NAMESPACE") + 1] for n, a in commands
                          if n == "install" and "EXPORT" in a and "NAMESPACE" in a[:-1]]
    project["libraries"] = [a[0] for n, a in commands if n == "add_library" and a]
    return project


def cmake_language(project: dict) -> str | None:
    languages = project["languages"]
    return "C++" if "CXX" in languages else "C" if "C" in languages else None


def cmake_problems(repo: str, root: Path, project: dict, local: bool) -> list[str]:
    """What a C or C++ library must declare in its CMakeLists.txt: project() with its name,
    version, description, page on the site and language; its target, exported as <name>::<name>;
    its language standard; and a LICENSE the site recognizes."""
    name, problems = project["name"], []
    if not re.fullmatch(r"[a-z0-9]+", name) or repo.split("/")[1] != name:
        problems.append(f"{name}: the name must be lowercase letters and digits only, and the same for the "
                        f"repository ({repo.split('/')[1]}) and for project() in CMakeLists.txt")
    if not re.fullmatch(r"\d+\.\d+\.\d+", project.get("version", "")):
        problems.append(f"{name}: project() declares no VERSION X.Y.Z, and there is no VERSION file holding it")
    if not project.get("description"):
        problems.append(f"{name}: project() declares no DESCRIPTION")
    if not cmake_language(project):
        problems.append(f"{name}: project() declares no LANGUAGES C or CXX")
    elif ("cxx" if cmake_language(project) == "C++" else "c") not in project["standards"]:
        std = "cxx_std_NN" if cmake_language(project) == "C++" else "c_std_NN"
        problems.append(f"{name}: no language standard; declare it with target_compile_features({name} PUBLIC {std})")
    if name not in project["libraries"]:
        problems.append(f"{name}: no add_library({name} ...)")
    if f"{name}::" not in project["exports"]:
        problems.append(f"{name}: no install(EXPORT ... NAMESPACE {name}::), so find_package({name}) "
                        f"cannot give the target {name}::{name}")
    if (file := license_file(root)) and not recognized_license(file.read_text()):
        problems.append(f"{name}: {file.name} is none of the licenses the site recognizes "
                        f"({', '.join(LICENSE_TEXTS)})")
    return problems


DEPENDENCIES = re.compile(r"^## Dependencies\n(.*?)(?=^## |\Z)", re.M | re.S)
DEPENDENCY = re.compile(r"- (?P<name>[A-Za-z0-9][A-Za-z0-9._+-]*)(?: `(?P<spec>[^`]+)`)?(?: \((?P<marks>[^)]+)\))?"
                        r"(?:: (?P<about>\S.*))?")
VERSION_CLAUSE = re.compile(r"(==|!=|>=|<=|~=|>|<)\s*\d+(\.\d+)*")


def readme_dependencies(name: str, readme: str) -> tuple[list[dict], list[str]]:
    """What a C or C++ library needs, as its README's '## Dependencies' declares it: one line
    per dependency, `- <name> `<version>` (<marks>)`, the version in pip's notation and the marks
    `bundled` (CMake downloads and builds it) and `optional: <CMake option>`, then an optional
    `: <what it is used for>`; 'None.' when there are none."""
    section = DEPENDENCIES.search(readme)
    if not section:
        return [], [f"{name}: README.md has no '## Dependencies' section after '## Documentation'; a C or C++ "
                    f"library declares its dependencies there, one per line, or 'None.'"]
    lines = [line.strip() for line in section[1].splitlines() if line.strip()]
    if lines == ["None."]:
        return [], []
    found, problems = [], []
    for line in lines:
        match = DEPENDENCY.fullmatch(line)
        spec = (match and match["spec"] or "").strip()
        marks = [mark.strip() for mark in (match and match["marks"] or "").split(",") if mark.strip()]
        optional = [m.removeprefix("optional:").strip() for m in marks if m.startswith("optional:")]
        if (not match or (spec and not all(VERSION_CLAUSE.fullmatch(c.strip()) for c in spec.split(",")))
                or any(m != "bundled" and not m.startswith("optional:") for m in marks)
                or len(optional) > 1 or "" in optional or len(marks) != len(set(marks))):
            problems.append(f"{name}: README '## Dependencies' line {line!r} is not "
                            f"'- <name> `<version>` (bundled, optional: <CMake option>): <what for>'")
            continue
        if any(d["name"] == normalized(match["name"]) for d in found):
            problems.append(f"{name}: README '## Dependencies' lists {match['name']} twice")
        found.append({"name": normalized(match["name"]), "spec": spec, "bundled": "bundled" in marks,
                      "optional": optional[0] if optional else None, "about": match["about"] or ""})
    return found, problems


SITE_URL = "https://offerrall.github.io"
OVERVIEW = "docs/overview.md"
FENCE = re.compile(r"^\s*(```+|~~~+)(.*)$")
CHANGELOG_HEADING = re.compile(r"(\d+)\.(\d+)\.(\d+) - (\d{4}-\d{2}-\d{2})")


def listed_pages(name: str, items: str) -> list[dict]:
    """The README list links each page on the site, so GitHub sends readers there; its source
    is the matching file under docs/, and the library's own address is docs/overview.md."""
    item = re.compile(rf"^- \[(?P<title>[^\]]+)\]\({re.escape(SITE_URL)}/{re.escape(name)}/(?P<page>(?:[\w.-]+/)*)\)"
                      r"(?:\s*[:—–-]?\s*(?P<about>.*))?")
    found = (item.match(line) for line in items.splitlines())
    return [{"title": m["title"], "about": m["about"],
             "path": f"docs/{m['page'].rstrip('/')}.md" if m["page"] else OVERVIEW}
            for m in found if m]


def site_line(name: str) -> str:
    return f"The full documentation is at {SITE_URL}/{name}/."


def fences_without_language(text: str) -> int:
    count, inside = 0, False
    for line in text.splitlines():
        if found := FENCE.match(line):
            if not inside and not found[2].strip():
                count += 1
            inside = not inside
    return count


def docs_problems(name: str, root: Path, site_link: tuple[str, str], readme: str, listed: list,
                  sections: tuple[str, ...] = ()) -> list[str]:
    """The shape of the documentation: a short README that opens the site, docs/overview.md
    first, each page's menu label its own title, every block of code tagged with a language.
    `site_link` is where the metadata names the site, and the address it names; `sections` are
    the ones allowed after '## Documentation', in that order."""
    problems = []
    entrance, _, documentation = readme.partition("\n## Documentation\n")
    if not listed or listed[0]["path"] != OVERVIEW:
        problems.append(f"{name}: {OVERVIEW} must exist and come first in '## Documentation'")
    headings = [h.strip() for h in re.findall(r"^## (.*)$", entrance + documentation, re.M)]
    if headings != list(sections):
        allowed = " and ".join(f"'## {s}'" for s in ("Documentation", *sections))
        problems.append(f"{name}: README.md may have no section besides {allowed}"
                        + (", in that order" if sections else ""))
    if len(entrance.splitlines()) > 40:
        problems.append(f"{name}: README.md has {len(entrance.splitlines())} lines before '## Documentation', at most 40")
    if sum(1 for line in entrance.splitlines() if FENCE.match(line)) > 2:
        problems.append(f"{name}: README.md has more than one block of code before '## Documentation'")
    if site_line(name) not in entrance.splitlines():
        problems.append(f"{name}: README.md must have the line: {site_line(name)}")
    for target in re.findall(r"\]\(([^)\s]+)", documentation.split("\n## ")[0]):
        if not target.startswith(f"{SITE_URL}/{name}/"):
            problems.append(f"{name}: '## Documentation' links {target}; link each page of the library on the "
                            f"site, {SITE_URL}/{name}/<page>/ (the changelog is added by the site)")

    pages = {"README.md": readme} | {p.relative_to(root).as_posix(): p.read_text() for p in (root / "docs").rglob("*.md")}
    for path, text in pages.items():
        if "img.shields.io" in text:
            problems.append(f"{name}: {path} has a badge; the site shows version, language and license itself")
        if (count := fences_without_language(text)):
            problems.append(f"{name}: {path} has {count} block(s) of code without a language")
        if path != "README.md" and re.search(r"\]\([^)\s]*README\.md", text):
            problems.append(f"{name}: {path} links to README.md; link to docs/overview.md or another page")
    for m in listed:
        text = pages.get(m["path"], "")
        titles = [t for t in md.parse(text) if t.type == "heading_open" and t.tag == "h1"]
        first = re.match(r"# (.+)", text)
        if len(titles) != 1 or not first:
            problems.append(f"{name}: {m['path']} must start with its one '# Title'")
        elif first[1].strip() != m["title"]:
            problems.append(f"{name}: '{m['title']}' in the README list is titled '{first[1].strip()}' in {m['path']}")

    if not site_address(site_link[1], name):
        problems.append(f"{name}: {site_link[0]} must be {SITE_URL}/{name}/")

    changelog = (root / "CHANGELOG.md").read_text() if (root / "CHANGELOG.md").exists() else ""
    previous = None
    for heading in re.findall(r"^## (.*)$", changelog, re.M):
        found = CHANGELOG_HEADING.fullmatch(heading.strip())
        try:
            day = found and datetime.date.fromisoformat(found[4])
        except ValueError:
            day = None
        if not day:
            problems.append(f"{name}: CHANGELOG.md heading '## {heading}' is not '## X.Y.Z - YYYY-MM-DD'")
            continue
        current = (tuple(map(int, found.groups()[:3])), day)
        if previous and not (current[0] < previous[0] and current[1] <= previous[1]):
            problems.append(f"{name}: CHANGELOG.md '## {heading}' is out of order")
        previous = current
    return problems


def sibling_problems(libs: list["Lib"]) -> list[str]:
    """A library names another of the site by its page on the site, not by its GitHub repository."""
    repos = "|".join(re.escape(lib.repo.rsplit("/", 1)[1]) for lib in libs)
    root_link = re.compile(rf"https?://github\.com/offerrall/({repos})/?(?=[)\s\"'>#]|$)", re.I | re.M)
    problems = []
    for lib in libs:
        for path in ["README.md", *(p.relative_to(lib.root).as_posix() for p in (lib.root / "docs").rglob("*.md"))]:
            for found in root_link.finditer((lib.root / path).read_text()):
                other = next(l for l in libs if l.repo.lower().endswith("/" + found[1].lower()))
                if other is not lib:
                    problems.append(f"{lib.name}: {path} links {found[0]}; use {SITE_URL}/{other.name}/")
    return problems


def load_lib(repo: str, group: str, local: bool, predates: bool = False) -> Lib:
    root, ref = checkout(repo, local)
    errors = []
    if (root / "pyproject.toml").exists():
        language = "Python"
        pyproject = tomllib.loads((root / "pyproject.toml").read_text())
        project = pyproject["project"]
        # Shown everywhere in PyPI's canonical form: lowercase, separators as one hyphen
        # (pygrbl_streamer is pygrbl-streamer), the spelling PyPI itself displays.
        name = normalized(project["name"])
        version = declared_version(root, pyproject)
        if not version:
            fail([f"{repo}: pyproject.toml declares no version this build can read"])
        errors += python_problems(root, project, version, local)
        errors += name_problems(repo, root, project, predates)
        if DEPENDENCIES.search((root / "README.md").read_text()):
            errors.append(f"{name}: a Python library declares its dependencies in pyproject.toml; "
                          f"remove '## Dependencies' from README.md")
        documentation = ("[project.urls] Documentation", project.get("urls", {}).get("Documentation", ""))
    elif (root / "CMakeLists.txt").exists():
        project = cmake_project(root)
        name, version = project["name"], project.get("version", "")
        language = cmake_language(project) or "C++"
        errors += cmake_problems(repo, root, project, local)
        if predates:
            errors.append(f"{name}: name_predates_convention is only for PyPI names; remove it from site.toml")
        documentation = ("project() HOMEPAGE_URL", project.get("homepage_url", ""))
        project["dependencies"], found = readme_dependencies(name, (root / "README.md").read_text())
        errors += found
        if not version:
            fail(errors)
    else:
        fail([f"{repo}: no pyproject.toml (Python) or CMakeLists.txt (C, C++)"])
    if not local and ref != f"v{version}":
        fail([f"{repo}: tag {ref} holds version {version}"])

    readme = (root / "README.md").read_text()
    section = re.search(r"^## Documentation\n(.*?)(?=^## |\Z)", readme, re.M | re.S)
    if not section:
        fail([f"{name}: README.md has no '## Documentation' section"])
    items = re.sub(r"\n[ \t]+(?=\S)", " ", section.group(1))  # an item wrapped over several lines
    listed = listed_pages(name, items)

    on_disk = {p.relative_to(root).as_posix() for p in (root / "docs").rglob("*.md")}
    for m in listed:
        if m["path"] not in on_disk:
            errors.append(f"{name}: the README lists a page whose source, {m['path']}, does not exist")
    for missing in sorted(on_disk - {m["path"] for m in listed}):
        errors.append(f"{name}: {missing} is not listed in README '## Documentation'")
    if "docs/index.md" in on_disk:
        errors.append(f"{name}: docs/index.md would have the markdown URL of the README, rename it")
    errors += release_problems(repo, root, name, version)
    errors += docs_problems(name, root, documentation, readme, listed,
                            () if language == "Python" else ("Dependencies",))
    fail(errors)

    pages = [Page("README.md", name, f"/{name}/", "")]
    # Menu labels are plain text: `run()` in the README list reads as run() in the menu.
    pages += [Page(m["path"], m["title"].replace("`", ""),
                   f"/{name}/{m['path'].removeprefix('docs/').removesuffix('.md')}/",
                   (m["about"] or "").strip().removesuffix("."))
              for m in listed if m["path"] != OVERVIEW]
    if (root / "CHANGELOG.md").exists():
        pages.append(Page("CHANGELOG.md", "Changelog", f"/{name}/changelog/", "the changes of every release"))
    requires = requirements(project) if language == "Python" else tuple(
        (d["name"], d["spec"]) for d in project["dependencies"])
    return Lib(name, group, project["description"], version, f"https://github.com/{repo}",
               ref, github_stars(repo), requires, project, root, tuple(pages), language)


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
    rel = "README.md" if rel == OVERVIEW else rel  # the Overview page is the README and docs/overview.md
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
    # The Overview: the README's entrance, without its list and the line pointing here, then
    # docs/overview.md without its title. Each part keeps its own folder for relative links.
    entrance = texts["README.md"].partition("\n## Documentation\n")[0]
    texts["README.md"] = "".join(line for line in entrance.splitlines(keepends=True)
                                 if line.strip() != site_line(lib.name))
    if (lib.root / OVERVIEW).exists():
        texts[OVERVIEW] = re.sub(r"\A# .*\n+", "", (lib.root / OVERVIEW).read_text())
    parsed = {source: md.parse(text) for source, text in texts.items()}
    ids = {source: {t.attrGet("id") for t in tokens if t.type == "heading_open"}
           for source, tokens in parsed.items()}
    if OVERVIEW in ids:
        ids["README.md"] = ids[OVERVIEW] = ids["README.md"] | ids[OVERVIEW]

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
    html = {source: md.renderer.render(tokens, md.options, {}) for source, tokens in parsed.items()}
    if OVERVIEW in html:
        html["README.md"] += html.pop(OVERVIEW)
        markdown["README.md"] = markdown["README.md"].rstrip("\n") + "\n\n" + markdown.pop(OVERVIEW)
    return html, markdown


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
    """What the Overview shows before the README: its pyproject.toml or CMakeLists.txt, as written."""
    project = lib.project
    pages = {normalized(other.name): other.pages[0].url for other in libs}
    if lib.language != "Python":
        standard = project["standards"].get("cxx" if lib.language == "C++" else "c")
        file = license_file(lib.root)
        return {
            "install": [], "install_direct": 0, "resolved_for": RESOLVED_FOR, "python": None,
            "standard": f"{lib.language}{standard}" if standard else lib.language,
            "target": f"{lib.name}::{lib.name}",
            "license": recognized_license(file.read_text()) if file else None,
            "dependencies": [{"text": f"{d['name']} {d['spec']}".strip(), "url": pages.get(d["name"]),
                              "note": ", ".join(filter(None, ["bundled" if d["bundled"] else "",
                                                              d["optional"] and f"optional: {d['optional']}",
                                                              d["about"]]))}
                             for d in project["dependencies"]],
            "extras": [], "commands": [],
        }

    def requirement(text: str) -> dict:
        found = REQUIREMENT.match(text)
        return {"text": text, "url": pages.get(normalized(found[1])) if found else None, "note": ""}

    install = resolved_install(lib)
    return {
        "install": install,
        "install_direct": sum(package["direct"] for package in install),
        "resolved_for": RESOLVED_FOR,
        "python": project.get("requires-python"),
        "standard": None, "target": None,
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
    packages: dict[tuple[bool, str], dict] = {}
    for lib in libs:
        if lib.language == "Python":
            declared = [(found[1], found[2].strip(), "") for found in map(REQUIREMENT.match, lib.project.get("dependencies", []))
                        if found]
        else:
            declared = [(d["name"], d["spec"], "bundled" if d["bundled"] else "") for d in lib.project["dependencies"]]
        for name, spec, note in declared:
            if normalized(name) in ours:
                continue
            # A C library and a Python package may share a name; they are not the same package.
            key = (lib.language == "Python", normalized(name))
            package = packages.setdefault(key, {"name": name, "python": key[0], "users": []})
            package["users"].append({"lib": lib, "spec": spec, "note": note})
    return sorted(packages.values(), key=lambda p: (-len(p["users"]), p["name"].lower()))


RESOLVED_FOR = ("3.13", "x86_64-unknown-linux-gnu")  # what pip would install there, today


def resolved_install(lib: Lib) -> list[dict]:
    """Every package installing the library brings, as uv resolves its pyproject.toml: each
    with its version, whether the library declares it, and which packages pull it in."""
    result = subprocess.run(
        ["uv", "pip", "compile", str(lib.root / "pyproject.toml"), "--python-version", RESOLVED_FOR[0],
         "--python-platform", RESOLVED_FOR[1], "--no-header", "--quiet"],
        capture_output=True, text=True)
    if result.returncode:
        fail([f"{lib.name}: its dependencies cannot be installed together\n{result.stderr.strip()}"])
    packages: list[dict] = []
    for line in result.stdout.splitlines():
        if found := re.match(r"([A-Za-z0-9._-]+)==(\S+)", line):
            packages.append({"name": normalized(found[1]), "version": found[2], "direct": False, "via": []})
        elif line.strip().startswith("#") and packages:
            note = line.strip().lstrip("#").strip().removeprefix("via").strip()
            if note.endswith("pyproject.toml)"):
                packages[-1]["direct"] = True
            elif note:
                packages[-1]["via"].append(normalized(note.split()[0]))
    return packages


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
    fail(sibling_problems(libs))
    lib_facts = {lib.name: facts(lib, libs) for lib in libs}

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
        installs=sorted(((lib, lib_facts[lib.name]) for lib in libs if lib.language == "Python"), key=lambda i: (-len(i[1]["install"]), i[0].name)),
        distinct=len({package["name"] for f in lib_facts.values() for package in f["install"]}),
        resolved_for=RESOLVED_FOR,
        standalone=[lib for lib in libs if lib.name not in linked]))

    # The pages for agents: an index, llms.txt, and every page as markdown. Libraries in the order
    # of site.toml, grouped as on the home page.
    catalog = [{"name": group, "libs": [{"lib": lib, "facts": lib_facts[lib.name],
                                         "uses": [link for link in links if link.user is lib],
                                         "used_by": [link for link in links if link.used is lib]}
                                        for lib in libs if lib.group == group]}
               for group in dict.fromkeys(lib.group for lib in libs)]
    # With them, this repository's README: the rules every library follows, whole at /doc/site.md
    # and its rules on /doc/, so an agent there knows what a new library must be.
    readme = (ROOT / "README.md").read_text()
    rules = readme.partition("\n## The rules\n")[2].partition("\n## ")[0]
    if not rules.strip():
        fail(["README.md: no ## The rules section for /doc/"])
    write(OUT / "doc" / "site.md", readme)
    write(OUT / "doc" / "index.html", theme.get_template("doc.html").render(
        site=site, catalog=catalog, rules=md.render(rules)))
    write(OUT / "llms.txt", theme.get_template("llms.txt").render(site=site, origin=origin, catalog=catalog))

    errors: list[str] = []
    for lib in libs:
        html, markdown = render_docs(lib, origin, errors)
        for i, page in enumerate(lib.pages):
            write(OUT / page.markdown.lstrip("/"), markdown[page.source])
            write(OUT / page.url.strip("/") / "index.html", theme.get_template("page.html").render(
                site=site, lib=lib, page=page, content=html[page.source],
                facts=lib_facts[lib.name],
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
