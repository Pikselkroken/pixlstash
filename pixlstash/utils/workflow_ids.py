"""What a workflow id looks like, in one place and stdlib only.

``auto:<digest>`` names an automatic workflow (the topologies sharing a core
and a set of base-model families; the digest is opaque, not a core address,
``hub.workflow_cards.auto_workflow_id``); ``manual:<uuid4 hex>`` names a manual one, a hub row holding its
own document (``workflow_document``). Stdlib only, so the MCP server, which is
a light separate process, can check an id without importing the hub.

The pattern is also a JSON-schema one, where ``$`` is the end; Python checks
it with ``fullmatch``, since there ``$`` also matches before a trailing newline.
"""

import json
import re

AUTO_PREFIX = "auto:"
MANUAL_PREFIX = "manual:"

WORKFLOW_ID_PATTERN = r"^(?:auto:[0-9a-f]{64}|manual:[0-9a-f]{32})$"

# Where a manual workflow's id rides inside its own editor-format document:
# ``extra`` is outside the nodes, ComfyUI keeps it across edit and save, and
# SaveImage embeds the whole editor workflow as a picture's ``workflow`` chunk,
# so a run started in ComfyUI carries the id into the library. An API-format
# document has no such place (every top-level key is a node) and goes unstamped;
# a run PixlStash submits carries the id as a PNG chunk of this name instead
# (``submitted_run_tag``).
WORKFLOW_TAG_KEY = "pixlstash_workflow_id"


def stamp_workflow_id(document: dict, workflow_id: str) -> dict:
    """*document* carrying *workflow_id* as its tag, replacing any other.

    A copy; *document* is not changed. An API-format document, or one whose
    ``extra`` is not an object, comes back as it is.
    """
    if not isinstance(document.get("nodes"), list):
        return document
    extra = document.get("extra", {})
    if not isinstance(extra, dict):
        return document
    return {**document, "extra": {**extra, WORKFLOW_TAG_KEY: workflow_id}}


def tagged_workflow_id(document) -> str | None:
    """The manual workflow id an editor-format *document* is tagged with.

    ``None`` for no tag, and for anything that is not a manual id: the tag
    came in with a picture, from wherever that picture was made.
    """
    extra = document.get("extra") if isinstance(document, dict) else None
    tag = extra.get(WORKFLOW_TAG_KEY) if isinstance(extra, dict) else None
    return _manual_id(tag)


def submitted_run_tag(metadata) -> str | None:
    """The manual workflow id a PixlStash run embedded as a chunk of its own.

    The run submits it under ``extra_pnginfo`` keyed :data:`WORKFLOW_TAG_KEY`,
    and SaveImage writes every such key as a text chunk holding its value as
    JSON. *metadata* is ``ImageUtils.extract_embedded_metadata``'s, where PNG
    chunks sit under ``png``. ``None`` for no chunk, and for anything that is
    not a manual id.
    """
    png = metadata.get("png") if isinstance(metadata, dict) else None
    raw = png.get(WORKFLOW_TAG_KEY) if isinstance(png, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        return _manual_id(json.loads(raw))
    except ValueError:
        return None


def _manual_id(tag) -> str | None:
    if not isinstance(tag, str) or not tag.startswith(MANUAL_PREFIX):
        return None
    return tag if re.fullmatch(WORKFLOW_ID_PATTERN, tag) else None


def untagged(document: dict) -> dict:
    """*document* without its tag, and without an ``extra`` only the tag filled."""
    extra = document.get("extra")
    if not isinstance(extra, dict) or WORKFLOW_TAG_KEY not in extra:
        return document
    rest = {key: value for key, value in extra.items() if key != WORKFLOW_TAG_KEY}
    body = {key: value for key, value in document.items() if key != "extra"}
    if rest:
        body["extra"] = rest
    return body
