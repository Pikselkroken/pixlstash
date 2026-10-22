"""Rebuild ``pixlstash/data/workflow_templates.json`` from ComfyUI's templates.

The model shelf's New workflow set picker (#1999, #2001) lists ComfyUI's
workflow templates with the files each one loads, so a template whose files are
all on the shelf is one press from a set. The app never fetches the list at
runtime: it reads the committed file this script writes.

**Source.** The ``comfyui-workflow-templates-json`` wheel on PyPI, the package
ComfyUI itself installs to serve ``/templates``. It holds ``index.json`` (the
catalogue) and one UI-format graph per template. A wheel is one pinned,
hash-checked download, so a rebuild is reproducible and the version is recorded
in the output; a weekly diff is one release to the next.

**What is kept.** Per template: its name, title, kind (the catalogue
category's type, ``edit`` for an image template tagged as an edit), tags, the
model names the catalogue lists, the minimum ComfyUI version, size and usage,
and ``files``: every ``{name, url, directory}`` from ``properties.models`` on
its nodes, top level and inside ``definitions.subgraphs``. A template that
downloads nothing (the paid API-node ones) is left out. The graphs themselves
are not kept: nothing reads them yet (#2000 will).

Usage::

    python scripts/build_template_suggestions.py            # latest release
    python scripts/build_template_suggestions.py --version 0.1.117
    python scripts/build_template_suggestions.py --wheel path/to/the.whl
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

PACKAGE = "comfyui-workflow-templates-json"
TEMPLATES_DIR = "comfyui_workflow_templates_json/templates/"
OUTPUT = Path(__file__).resolve().parent.parent / (
    "pixlstash/data/workflow_templates.json"
)


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


def _kind(category_type: str, tags: list[str]) -> str:
    if category_type == "image" and any("edit" in tag.lower() for tag in tags):
        return "edit"
    return category_type or "image"


def build(wheel: bytes, version: str) -> dict:
    templates: dict[str, dict] = {}
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
                files = _files(json.loads(archive.read(path)))
                if not files:
                    continue
                tags = list(entry.get("tags") or [])
                templates[name] = {
                    "name": name,
                    "title": entry.get("title") or name,
                    "kind": _kind(category.get("type") or "", tags),
                    "tags": tags,
                    "models": list(entry.get("models") or []),
                    "min_comfyui_version": entry.get("minComfyUIVersion"),
                    "size": entry.get("size") or 0,
                    "usage": entry.get("usage") or 0,
                    "files": files,
                }
    return {
        "source": PACKAGE,
        "version": version,
        "templates": sorted(templates.values(), key=lambda t: t["name"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", help=f"{PACKAGE} release (default: latest)")
    parser.add_argument("--wheel", type=Path, help="read this wheel, offline")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    if args.wheel:
        data = args.wheel.read_bytes()
        version = args.version or args.wheel.name.split("-")[1]
    else:
        data, version = _fetch_wheel(args.version)
    document = build(data, version)
    args.output.write_text(
        json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"Wrote {len(document['templates'])} templates from {PACKAGE} "
        f"{version} to {args.output}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
