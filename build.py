# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py", "mdit-py-plugins"]
# ///
"""Build the site into _site/ from each library's README.md, docs/ and pyproject.toml.

Each library is read at its highest release tag (v1.2.0).

Run: uv run build.py            # the latest releases, cloned into .cache/
     uv run build.py --local    # the working trees next to this repo (../<repo>), to preview
"""

import html
import posixpath
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin

ROOT = Path(__file__).parent
OUT = ROOT / "_site"
CACHE = ROOT / ".cache"
TAGLINE = "Small, versioned Python libraries. Each one does one thing and installs with a single command."

md = (
    MarkdownIt("commonmark", {"html": True})
    .enable(["table", "strikethrough"])
    .use(anchors_plugin, max_level=6)
)

DOC_ITEM = re.compile(r"^- \[(?P<title>[^\]]+)\]\((?P<path>docs/[\w-]+\.md)\)")
HTML_URL = re.compile(r'\b(src|href)="([^"]+)"')
EXTERNAL = re.compile(r"^([a-z][a-z0-9+.-]*:|//)")


@dataclass(frozen=True)
class Page:
    source: str  # path inside the repository, e.g. "docs/window.md"
    title: str
    url: str


@dataclass(frozen=True)
class Lib:
    name: str
    group: str
    description: str
    version: str
    repo: str  # https://github.com/owner/name
    ref: str  # the tag read, or "main" for a working tree
    root: Path
    pages: tuple[Page, ...]  # the README first, then the docs in README order


def fail(errors: list[str]) -> None:
    if errors:
        print("\n".join(f"error: {e}" for e in errors), file=sys.stderr)
        sys.exit(1)


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


def load_lib(repo: str, group: str, local: bool) -> Lib:
    root, ref = checkout(repo, local)
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    name = project["name"]
    version = project.get("version") or re.search(
        r'__version__ = "([^"]+)"', (root / "src" / name / "__init__.py").read_text()
    ).group(1)

    readme = (root / "README.md").read_text()
    section = re.search(r"^## Documentation\n(.*?)(?=^## |\Z)", readme, re.M | re.S)
    if not section:
        fail([f"{name}: README.md has no '## Documentation' section"])
    listed = [m for line in section.group(1).splitlines() if (m := DOC_ITEM.match(line))]

    errors = []
    on_disk = {f"docs/{p.name}" for p in (root / "docs").glob("*.md")}
    for m in listed:
        if m["path"] not in on_disk:
            errors.append(f"{name}: README lists {m['path']}, which does not exist")
    for missing in sorted(on_disk - {m["path"] for m in listed}):
        errors.append(f"{name}: {missing} is not listed in README '## Documentation'")
    fail(errors)

    pages = [Page("README.md", name, f"/{name}/")]
    pages += [Page(m["path"], m["title"], f"/{name}/{Path(m['path']).stem}/") for m in listed]
    if not local and ref != f"v{version}":
        fail([f"{repo}: tag {ref} holds version {version}"])
    return Lib(name, group, project["description"], version,
               f"https://github.com/{repo}", ref, root, tuple(pages))


def resolve(target: str, source: str, lib: Lib, ids: dict[str, set[str]], errors: list[str]) -> str:
    """Map a link written for GitHub to its place on the site."""
    if EXTERNAL.match(target):
        return target
    path, _, anchor = target.partition("#")
    rel = posixpath.normpath(posixpath.join(posixpath.dirname(source), path)) if path else source
    if anchor and rel in ids and anchor not in ids[rel]:
        errors.append(f"{lib.name}/{source}: no heading for {target}")
    frag = f"#{anchor}" if anchor else ""
    if not path:
        return frag
    for page in lib.pages:
        if page.source == rel:
            return page.url + frag
    if rel.startswith("docs/images/"):
        return f"/{lib.name}/images/{rel.removeprefix('docs/images/')}"
    if not (lib.root / rel).exists():
        errors.append(f"{lib.name}/{source}: broken link {target}")
    kind = "tree" if (lib.root / rel).is_dir() else "blob"
    return f"{lib.repo}/{kind}/{lib.ref}/{rel}{frag}"


def render(tokens, source: str, lib: Lib, ids: dict[str, set[str]], errors: list[str]) -> str:
    def fix(token):
        for attr in ("href", "src"):
            if token.attrGet(attr):
                token.attrSet(attr, resolve(token.attrGet(attr), source, lib, ids, errors))
        if token.type in ("html_block", "html_inline"):
            token.content = HTML_URL.sub(
                lambda m: f'{m[1]}="{resolve(m[2], source, lib, ids, errors)}"', token.content)

    for token in tokens:
        fix(token)
        for child in token.children or ():
            fix(child)
    return md.renderer.render(tokens, md.options, {})


def layout(title: str, body: str, description: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500&family=Inter:wght@400;500;600&family=JetBrains+Mono&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/style.css">
</head>
<body>
{body}
</body>
</html>
"""


def home(libs: list[Lib]) -> str:
    groups: dict[str, list[Lib]] = {}
    for lib in libs:
        groups.setdefault(lib.group, []).append(lib)
    sections = "".join(
        f"""
  <section>
    <h2>{html.escape(group)}</h2>
    <ul class="libs">{"".join(
        f'<li><a href="/{lib.name}/">{lib.name}</a><p>{html.escape(lib.description)}</p></li>'
        for lib in members)}</ul>
  </section>"""
        for group, members in groups.items())
    return layout("offerrall", f"""<main class="home">
  <header>
    <h1>offerrall</h1>
    <p>{TAGLINE}</p>
    <nav><a href="https://github.com/offerrall">GitHub</a><a href="https://pypi.org/user/offerrall/">PyPI</a></nav>
  </header>
{sections}

  <footer>pip install &lt;name&gt;</footer>
</main>""", TAGLINE)


def doc_page(lib: Lib, index: int, content: str) -> str:
    page = lib.pages[index]
    nav = "".join(
        f'<li><a href="{p.url}"{" aria-current=\"page\"" if i == index else ""}>'
        f'{"Overview" if i == 0 else html.escape(p.title)}</a></li>'
        for i, p in enumerate(lib.pages))
    prev = lib.pages[index - 1] if index > 0 else None
    next_ = lib.pages[index + 1] if index + 1 < len(lib.pages) else None
    pager = "".join((
        f'<a class="prev" href="{prev.url}"><span>Previous</span>{html.escape("Overview" if prev.source == "README.md" else prev.title)}</a>' if prev else "<span></span>",
        f'<a class="next" href="{next_.url}"><span>Next</span>{html.escape(next_.title)}</a>' if next_ else "",
    ))
    title = lib.name if index == 0 else f"{page.title} · {lib.name}"
    return layout(title, f"""<div class="docs">
  <aside>
    <a class="home-link" href="/">offerrall</a>
    <div class="lib-name"><a href="{lib.pages[0].url}">{lib.name}</a><span>{lib.version}</span></div>
    <ul>{nav}</ul>
    <p class="links"><a href="{lib.repo}">GitHub</a><a href="https://pypi.org/project/{lib.name}/">PyPI</a></p>
  </aside>
  <main>
    <article>{content}</article>
    <nav class="pager">{pager}</nav>
  </main>
</div>""", lib.description)


def main() -> None:
    config = tomllib.loads((ROOT / "libs.toml").read_text())
    local = "--local" in sys.argv[1:]
    libs = [load_lib(entry["repo"], entry["group"], local) for entry in config["lib"]]

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copy(ROOT / "style.css", OUT / "style.css")
    (OUT / ".nojekyll").touch()
    (OUT / "index.html").write_text(home(libs))

    errors: list[str] = []
    for lib in libs:
        parsed = {p.source: md.parse((lib.root / p.source).read_text()) for p in lib.pages}
        ids = {src: {t.attrGet("id") for t in tokens if t.type == "heading_open"}
               for src, tokens in parsed.items()}
        for i, page in enumerate(lib.pages):
            out = OUT / page.url.strip("/") / "index.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(doc_page(lib, i, render(parsed[page.source], page.source, lib, ids, errors)))
        images = lib.root / "docs" / "images"
        if images.is_dir():
            shutil.copytree(images, OUT / lib.name / "images")
    fail(errors)
    print(f"built {sum(len(lib.pages) for lib in libs) + 1} pages into {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
