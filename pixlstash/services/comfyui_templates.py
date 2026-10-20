"""The workflow templates the owner's ComfyUI serves (#2000).

ComfyUI ships its templates as static files from the
``comfyui-workflow-templates`` package, and lists the templates installed
custom-node packs carry on a route of its own:

=====================================  ==========================================
``GET /templates/index.json``          the built-in catalogue, by category
``GET /templates/{name}.json``         one built-in graph (editor format)
``GET /templates/{name}-1.{ext}``      its thumbnail
``GET /api/workflow_templates``        ``{pack folder: [name, ...]}``
``GET /api/workflow_templates/{m}/…``  one pack's graph (``.json``) or picture
=====================================  ==========================================

None of it is a documented API, so every read tolerates a file that is missing
or shaped otherwise: what does not read is left out and logged, never raised.

**The install is the source, not a list built elsewhere.** It serves exactly
the templates its version opens, and the packs it has installed, so nothing
here filters by ``minComfyUIVersion``.

What each template would download is read off its graph: every
``properties.models`` entry (``{name, url, directory}``) on a node at the top
level or inside ``definitions.subgraphs``, which is where most templates keep
their loaders. The graphs are small and local, so the listing reads them all,
a few at a time, and keeps the answer per ComfyUI address for
:data:`LISTING_TTL_S`.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

import requests

from pixlstash.pixl_logging import get_logger
from pixlstash.services.comfyui_service import NO_PROXY
from pixlstash.services.workflow_hash import normalized_filename

logger = get_logger(__name__)

# The module ComfyUI files its own templates under, in `index.json` and here.
BUILTIN_MODULE = "default"

SOURCE_BUILTIN = "builtin"
SOURCE_PACK = "pack"
# Runs on Comfy's paid API nodes: downloads nothing, needs a Comfy account.
SOURCE_PARTNER = "partner"

# A listing is kept this long per ComfyUI address; *Re-read* skips it.
LISTING_TTL_S = 600.0
# Graphs read at once. ComfyUI serves them from disk; a few hundred take a
# fraction of a second on the same machine.
GRAPH_READERS = 8
TIMEOUT_S = 10
# Larger than any template ComfyUI ships (the largest is well under 1 MB).
MAX_GRAPH_BYTES = 8 * 1024 * 1024
MAX_THUMBNAIL_BYTES = 8 * 1024 * 1024

# What a thumbnail extension is served as. ComfyUI answers them all as
# `application/octet-stream`, which an `<img>` would still draw but a
# `nosniff` response would not.
THUMBNAIL_TYPES = {
    "webp": "image/webp",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
}


class TemplatesUnavailable(RuntimeError):
    """ComfyUI could not be asked for its templates; the message says why."""


@dataclass
class TemplateFile:
    """One file a template's loaders name, as ``properties.models`` lists it."""

    name: str
    directory: Optional[str]
    url: Optional[str]


@dataclass
class Template:
    """One template ComfyUI serves, and what its graph would download."""

    module: str
    name: str
    source: str
    title: str
    description: Optional[str] = None
    category: Optional[str] = None
    media_type: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    model_names: list[str] = field(default_factory=list)
    date: Optional[str] = None
    usage: Optional[int] = None
    size: Optional[int] = None
    tutorial_url: Optional[str] = None
    thumbnail_ext: Optional[str] = None
    # Whether the pack's nodes loaded in ComfyUI; None for a built-in.
    pack_loaded: Optional[bool] = None
    files: list[TemplateFile] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.module}/{self.name}"


@dataclass
class Listing:
    """Every template one ComfyUI serves, read at *read_at* (epoch seconds)."""

    templates: list[Template]
    read_at: float

    def find(self, module: str, name: str) -> Optional[Template]:
        return next(
            (t for t in self.templates if t.module == module and t.name == name),
            None,
        )


_listings: dict[str, Listing] = {}
_listings_lock = threading.Lock()


def forget_listings() -> None:
    """Drop every kept listing. For tests, which change what ComfyUI answers."""
    with _listings_lock:
        _listings.clear()


def _get(url: str, *, max_bytes: int) -> requests.Response:
    response = requests.get(
        url, timeout=TIMEOUT_S, allow_redirects=False, proxies=NO_PROXY
    )
    if response.status_code != 200:
        raise requests.HTTPError(f"ComfyUI answered HTTP {response.status_code}")
    if len(response.content) > max_bytes:
        raise requests.HTTPError(f"{len(response.content)} bytes, past {max_bytes}")
    return response


def _get_json(url: str):
    return _get(url, max_bytes=MAX_GRAPH_BYTES).json()


def graph_url(base_url: str, module: str, name: str) -> str:
    """Where ComfyUI serves one template's graph."""
    if module == BUILTIN_MODULE:
        return f"{base_url}/templates/{quote(name)}.json"
    return f"{base_url}/api/workflow_templates/{quote(module)}/{quote(name)}.json"


def template_files(graph) -> list[TemplateFile]:
    """The files a template's loaders name, each ``(directory, name)`` once.

    Read off ``properties.models`` at the top level and inside every
    ``definitions.subgraphs[].nodes``. An entry that is not shaped as one is
    passed over: a template is somebody else's file.
    """
    if not isinstance(graph, dict):
        return []
    node_lists = [graph.get("nodes")]
    subgraphs = (graph.get("definitions") or {}).get("subgraphs")
    if isinstance(subgraphs, list):
        node_lists += [s.get("nodes") for s in subgraphs if isinstance(s, dict)]
    found: dict[tuple, TemplateFile] = {}
    for nodes in node_lists:
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            properties = node.get("properties") if isinstance(node, dict) else None
            models = properties.get("models") if isinstance(properties, dict) else None
            if not isinstance(models, list):
                continue
            for entry in models:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                directory = entry.get("directory")
                directory = directory if isinstance(directory, str) else None
                url = entry.get("url")
                url = url if isinstance(url, str) else None
                found.setdefault(
                    (directory, name.lower()), TemplateFile(name, directory, url)
                )
    return list(found.values())


def _is_partner(entry: dict) -> bool:
    name = str(entry.get("name") or "")
    return name.startswith("api_") or entry.get("openSource") is False


def _str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _builtin_templates(index) -> list[Template]:
    """The built-in templates ``index.json`` lists, in its order."""
    if not isinstance(index, list):
        logger.warning(
            "ComfyUI's templates/index.json is %s, not a list; no built-in "
            "templates are listed.",
            type(index).__name__,
        )
        return []
    templates: list[Template] = []
    seen: set[str] = set()
    for category in index:
        if not isinstance(category, dict):
            continue
        if category.get("moduleName", BUILTIN_MODULE) != BUILTIN_MODULE:
            continue
        for entry in category.get("templates") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            # A template listed under two categories is one template.
            if not isinstance(name, str) or not name or name in seen:
                continue
            seen.add(name)
            subtype = entry.get("mediaSubtype")
            usage = entry.get("usage")
            size = entry.get("size")
            templates.append(
                Template(
                    module=BUILTIN_MODULE,
                    name=name,
                    source=SOURCE_PARTNER if _is_partner(entry) else SOURCE_BUILTIN,
                    title=str(entry.get("title") or name),
                    description=entry.get("description")
                    if isinstance(entry.get("description"), str)
                    else None,
                    category=category.get("title")
                    if isinstance(category.get("title"), str)
                    else None,
                    media_type=entry.get("mediaType")
                    if isinstance(entry.get("mediaType"), str)
                    else None,
                    tags=_str_list(entry.get("tags")),
                    model_names=_str_list(entry.get("models")),
                    date=entry.get("date")
                    if isinstance(entry.get("date"), str)
                    else None,
                    usage=usage if isinstance(usage, int) else None,
                    size=size if isinstance(size, int) and size > 0 else None,
                    tutorial_url=entry.get("tutorialUrl")
                    if isinstance(entry.get("tutorialUrl"), str)
                    else None,
                    thumbnail_ext=subtype
                    if isinstance(subtype, str) and subtype.lower() in THUMBNAIL_TYPES
                    else None,
                )
            )
    return templates


def _pack_templates(listed, loaded_modules: Optional[set[str]]) -> list[Template]:
    """The templates custom-node packs carry: ``{pack folder: [name, ...]}``.

    *loaded_modules* are the pack folders ComfyUI loaded nodes from, folded;
    ``None`` when that could not be read, and then nobody is said not to load.
    """
    if not isinstance(listed, dict):
        logger.warning(
            "ComfyUI's /api/workflow_templates is %s, not an object; no "
            "node-pack templates are listed.",
            type(listed).__name__,
        )
        return []
    templates: list[Template] = []
    for module, names in sorted(listed.items(), key=lambda item: str(item[0])):
        if not isinstance(module, str) or not module or not isinstance(names, list):
            continue
        loaded = None if loaded_modules is None else module.lower() in loaded_modules
        for name in names:
            if not isinstance(name, str) or not name:
                continue
            templates.append(
                Template(
                    module=module,
                    name=name,
                    source=SOURCE_PACK,
                    title=name.replace("_", " "),
                    pack_loaded=loaded,
                )
            )
    return templates


def loaded_pack_modules(object_info: Optional[dict]) -> Optional[set[str]]:
    """The custom-node folders ComfyUI loaded nodes from, folded.

    Each node's ``python_module`` is ``custom_nodes.<folder>`` for a pack.
    ``None`` for no map.
    """
    if not isinstance(object_info, dict):
        return None
    modules: set[str] = set()
    for spec in object_info.values():
        module = spec.get("python_module") if isinstance(spec, dict) else None
        if isinstance(module, str) and module.startswith("custom_nodes."):
            modules.add(module.split(".", 2)[1].lower())
    return modules


def _read_files(base_url: str, template: Template) -> None:
    """Fill *template*'s files from its graph; leave them empty if it won't read."""
    try:
        template.files = template_files(
            _get_json(graph_url(base_url, template.module, template.name))
        )
    except (requests.RequestException, ValueError) as exc:
        logger.info(
            "Template %s from ComfyUI at %s will not read, so its files are "
            "not listed: %s",
            template.key,
            base_url,
            exc,
        )


def read_listing(
    base_url: str, object_info: Optional[dict] = None, *, refresh: bool = False
) -> Listing:
    """Every template the ComfyUI at *base_url* serves, kept for a while.

    *refresh* reads ComfyUI again whatever is kept. A partner template's graph
    is not read: it runs on Comfy's API nodes and downloads nothing.

    Raises:
        TemplatesUnavailable: Neither list could be read.
    """
    now = time.monotonic()
    with _listings_lock:
        kept = _listings.get(base_url)
    if kept is not None and not refresh and now - kept.read_at < LISTING_TTL_S:
        return kept
    problems = []
    index = listed = None
    try:
        index = _get_json(f"{base_url}/templates/index.json")
    except (requests.RequestException, ValueError) as exc:
        problems.append(f"templates/index.json: {exc}")
    try:
        listed = _get_json(f"{base_url}/api/workflow_templates")
    except (requests.RequestException, ValueError) as exc:
        problems.append(f"api/workflow_templates: {exc}")
    if index is None and listed is None:
        raise TemplatesUnavailable("; ".join(problems))
    for problem in problems:
        logger.info("ComfyUI at %s lists no %s", base_url, problem)
    templates = _builtin_templates(index) if index is not None else []
    if listed is not None:
        templates += _pack_templates(listed, loaded_pack_modules(object_info))
    with ThreadPoolExecutor(GRAPH_READERS) as readers:
        list(
            readers.map(
                lambda t: _read_files(base_url, t),
                [t for t in templates if t.source != SOURCE_PARTNER],
            )
        )
    listing = Listing(templates, time.monotonic())
    with _listings_lock:
        # Pruned on write, as the object_info cache is: an address the owner
        # changed away from is not kept for the life of the process.
        stale = [
            u for u, kept in _listings.items() if now - kept.read_at >= LISTING_TTL_S
        ]
        for url in stale:
            del _listings[url]
        _listings[base_url] = listing
    logger.info("Read %d templates from ComfyUI at %s.", len(templates), base_url)
    return listing


def fetch_graph(base_url: str, template: Template) -> dict:
    """One template's graph, read now.

    Raises:
        TemplatesUnavailable: It could not be read, or is not a JSON object.
    """
    try:
        graph = _get_json(graph_url(base_url, template.module, template.name))
    except (requests.RequestException, ValueError) as exc:
        raise TemplatesUnavailable(str(exc)) from exc
    if not isinstance(graph, dict):
        raise TemplatesUnavailable(f"it is {type(graph).__name__}, not a workflow")
    return graph


def fetch_thumbnail(base_url: str, template: Template) -> tuple[bytes, str]:
    """One template's picture and its media type.

    A built-in's is ``{name}-1.{mediaSubtype}``; a pack's sits beside its
    graph under a name ComfyUI's own browser guesses at, so the usual picture
    extensions are tried in turn.

    Raises:
        LookupError: ComfyUI serves no picture for it.
    """
    if template.module == BUILTIN_MODULE:
        if template.thumbnail_ext is None:
            raise LookupError(f"{template.key} has no thumbnail")
        ext = template.thumbnail_ext.lower()
        candidates = [(f"{base_url}/templates/{quote(template.name)}-1.{ext}", ext)]
    else:
        stem = (
            f"{base_url}/api/workflow_templates/"
            f"{quote(template.module)}/{quote(template.name)}"
        )
        candidates = [(f"{stem}.{ext}", ext) for ext in ("jpg", "png", "webp")]
    for url, ext in candidates:
        try:
            response = _get(url, max_bytes=MAX_THUMBNAIL_BYTES)
        except requests.RequestException as exc:
            logger.debug("No thumbnail at %s: %s", url, exc)
            continue
        return response.content, THUMBNAIL_TYPES[ext]
    raise LookupError(f"ComfyUI serves no thumbnail for {template.key}")


def present_file_names(hub) -> set[str]:
    """Every model file name the shelf has a present copy of, folded.

    The same key a recipe's asset is matched by (:func:`normalized_filename`):
    templates carry no hashes, so a file is on the shelf when one of its
    copies has the name the template asks for.
    """
    return {
        normalized_filename(row["relpath"])
        for row in hub.fetchall(
            "SELECT relpath FROM model_file WHERE state = 'present'"
        )
        if row["relpath"]
    }
