"""Rebuild ``pixlstash/data/workflow_templates.json`` from ComfyUI's templates.

The model shelf's New workflow set picker (#1999, #2001) lists ComfyUI's
workflow templates with the files each one loads, so a template whose files are
all on the shelf is one press from a set. The app never fetches the list at
runtime: it reads the committed file this script writes.

**Source.** The ``comfyui-workflow-templates-json`` wheel on PyPI, the package
ComfyUI itself installs to serve ``/templates``. It holds ``index.json`` (the
catalogue) and one UI-format graph per template. A release is one download,
so ``--version`` rebuilds the same file and the version is recorded in the
output; a weekly diff is one release to the next. The sha256 it is checked
against comes from the same PyPI answer as its URL, so it catches a broken
download, not a compromised release: the review of the bot PR is that gate.

**What is kept.** Per template: its name, title, description, kind (the
catalogue category's type, ``edit`` for an image template tagged as an edit),
tags, the model names the catalogue lists, whether it is open source, the
minimum ComfyUI version, size and usage, ``requires_custom_nodes`` (a node
whose ``cnr_id`` names a pack other than ``comfy-core``; a flag the picker
shows, never a filter) and ``files``: every ``{name, url, directory}`` from
``properties.models`` on its nodes, top level and inside
``definitions.subgraphs``. A template that downloads nothing (the paid API-node
ones) is left out.

**The graphs are kept too**, one minified file per template in
``pixlstash/data/workflow_templates/``, so a workflow can be made from one with
no ComfyUI reachable (#2000). One file each, so the weekly PR's file list says
which graphs changed. A name that is not a plain file name is skipped.

**The weekly refresh** (``.github/workflows/template-suggestions.yml``) runs
this with ``--summary``, which writes the PR body: the templates added,
removed, whose files changed and whose graph changed.

Usage::

    python scripts/build_template_suggestions.py            # latest release
    python scripts/build_template_suggestions.py --version 0.1.117
    python scripts/build_template_suggestions.py --wheel path/to/the.whl
    python scripts/build_template_suggestions.py --summary body.md
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

PACKAGE = "comfyui-workflow-templates-json"
TEMPLATES_DIR = "comfyui_workflow_templates_json/templates/"
OUTPUT = Path(__file__).resolve().parent.parent / (
    "pixlstash/data/workflow_templates.json"
)
# The graphs, beside the list: ``workflow_templates/<name>.json``.
GRAPHS = OUTPUT.with_suffix("")
# A template name as a file name: the package is not ours, so a name that could
# leave the folder (a slash, a leading dot) is not written.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")


def _fetch_wheel(version: str | None) -> tuple[bytes, str]:
    """The wheel's bytes and version, checked against PyPI's sha256."""
    url = (
        f"https://pypi.org/pypi/{PACKAGE}/{version}/json"
        if version
        else (f"https://pypi.org/pypi/{PACKAGE}/json")
    )
    with urllib.request.urlopen(url, timeout=60) as response:
        meta = json.load(response)
    wheel = next(f for f in meta["urls"] if f["filename"].endswith(".whl"))
    with urllib.request.urlopen(wheel["url"], timeout=300) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != wheel["digests"]["sha256"]:
        raise SystemExit(f"{wheel['filename']} does not match PyPI's sha256.")
    return data, meta["info"]["version"]


def _nodes(graph: dict):
    yield from graph.get("nodes") or []
    for subgraph in (graph.get("definitions") or {}).get("subgraphs") or []:
        yield from subgraph.get("nodes") or []


def _files(graph: dict) -> list[dict]:
    """Every distinct file the graph's loaders name, in node order."""
    seen: dict[str, dict] = {}
    for node in _nodes(graph):
        for model in (node.get("properties") or {}).get("models") or []:
            name = model.get("name")
            if name and name not in seen:
                seen[name] = {
                    "name": name,
                    "url": model.get("url") or "",
                    "directory": model.get("directory") or "",
                }
    return list(seen.values())


def _requires_custom_nodes(graph: dict) -> bool:
    packs = {(node.get("properties") or {}).get("cnr_id") for node in _nodes(graph)}
    return bool(packs - {None, "comfy-core"})


def _kind(category_type: str, tags: list[str]) -> str:
    if category_type == "image" and any("edit" in tag.lower() for tag in tags):
        return "edit"
    return category_type or "image"


def build(wheel: bytes, version: str) -> tuple[dict, dict[str, str]]:
    """The list, and each kept template's graph minified, by name."""
    templates: dict[str, dict] = {}
    graphs: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        index = json.loads(archive.read(TEMPLATES_DIR + "index.json"))
        names = set(archive.namelist())
        for category in index:
            for entry in category.get("templates") or []:
                name = entry["name"]
                path = f"{TEMPLATES_DIR}{name}.json"
                # A template listed in two categories keeps its first.
                if name in templates or path not in names:
                    continue
                if not _SAFE_NAME.match(name):
                    print(f"Skipped template {name!r}: not a plain file name.")
                    continue
                graph = json.loads(archive.read(path))
                files = _files(graph)
                if not files:
                    continue
                graphs[name] = json.dumps(
                    graph, separators=(",", ":"), ensure_ascii=False
                )
                tags = list(entry.get("tags") or [])
                templates[name] = {
                    "name": name,
                    "title": entry.get("title") or name,
                    "description": entry.get("description") or "",
                    "kind": _kind(category.get("type") or "", tags),
                    "tags": tags,
                    "models": list(entry.get("models") or []),
                    "open_source": bool(entry.get("openSource")),
                    "min_comfyui_version": entry.get("minComfyUIVersion"),
                    "requires_custom_nodes": _requires_custom_nodes(graph),
                    "size": entry.get("size") or 0,
                    "usage": entry.get("usage") or 0,
                    "files": files,
                }
    document = {
        "source": PACKAGE,
        "version": version,
        "templates": sorted(templates.values(), key=lambda t: t["name"]),
    }
    return document, graphs


def _read_graphs(folder: Path) -> dict[str, str]:
    if not folder.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in folder.glob("*.json")}


def _title(template: dict) -> str:
    """A title as plain text on one line of the PR body.

    The package's text is not ours: an ``@`` would notify somebody, and
    brackets, a ``#`` or a newline would add links, references or structure.
    """
    title = " ".join(template["title"].split())[:120]
    title = re.sub(r"([\\`*_\[\]()<>#!|~])", r"\\\1", title)
    return title.replace("@", "@\u200b")


def summary(old: dict, new: dict, old_graphs: dict, new_graphs: dict) -> str:
    """The weekly PR's body: what changed, by template, rather than a JSON diff."""
    before = {t["name"]: t for t in old.get("templates") or []}
    after = {t["name"]: t for t in new["templates"]}

    def files(template):
        return sorted((f["name"], f["url"], f["directory"]) for f in template["files"])

    sections = [
        ("Added", sorted(after.keys() - before.keys())),
        ("Removed", sorted(before.keys() - after.keys())),
        (
            "Files changed",
            sorted(
                n
                for n in after.keys() & before.keys()
                if files(after[n]) != files(before[n])
            ),
        ),
        (
            "Graph changed",
            sorted(
                n
                for n in after.keys() & before.keys()
                if n in old_graphs and old_graphs[n] != new_graphs.get(n)
            ),
        ),
    ]
    lines = [
        f"ComfyUI's workflow templates, `{PACKAGE}` "
        f"{old.get('version') or 'none'} to {new['version']}: "
        f"{len(after)} templates that download files.",
        "",
    ]
    for heading, names in sections:
        if names:
            lines.append(f"**{heading}** ({len(names)})")
            lines.extend(f"- `{n}` {_title(after.get(n) or before[n])}" for n in names)
            lines.append("")
    if not any(names for _, names in sections):
        lines.append("No template was added, removed or changed.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", help=f"{PACKAGE} release (default: latest)")
    parser.add_argument("--wheel", type=Path, help="read this wheel, offline")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--summary", type=Path, help="also write what changed, as Markdown, here"
    )
    args = parser.parse_args(argv)
    if args.wheel:
        data = args.wheel.read_bytes()
        version = args.version or args.wheel.name.split("-")[1]
    else:
        data, version = _fetch_wheel(args.version)
    document, graphs = build(data, version)
    folder = args.output.with_suffix("")
    try:
        old = json.loads(args.output.read_text(encoding="utf-8"))
    except FileNotFoundError:
        old = {}
    old_graphs = _read_graphs(folder)
    args.output.write_text(
        json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    folder.mkdir(exist_ok=True)
    for name in old_graphs.keys() - graphs.keys():
        (folder / f"{name}.json").unlink()
    for name, text in graphs.items():
        if old_graphs.get(name) != text:
            (folder / f"{name}.json").write_text(text, encoding="utf-8")
    if args.summary:
        args.summary.write_text(
            summary(old, document, old_graphs, graphs), encoding="utf-8"
        )
    print(
        f"Wrote {len(document['templates'])} templates from {PACKAGE} "
        f"{version} to {args.output}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
