"""A read-only MCP server: ``pixlstash-mcp`` / ``python -m pixlstash.mcp_server``.

Lets an agent search a PixlStash library and read its pictures, tags and
recipes over the Model Context Protocol (stdio transport, newline-delimited
JSON-RPC 2.0).

**It holds no authority of its own.** It is an HTTP client of a running
server, and every tool is a fixed ``GET`` against an existing API route,
authenticated with the API token in ``PIXLSTASH_TOKEN``. Scope is therefore
enforced where it always is, by the auth middleware and the authz gate: a
token restricted to a set sees that set and nothing else, and a refusal comes
back to the agent as a tool error. There are no write tools, and nothing here
can issue a request other than ``GET``. Mint a ``READ`` token for it; the
token is read from the environment rather than argv, which other accounts on
the machine can list.

The protocol is implemented with the standard library rather than the ``mcp``
SDK: the stdio surface a tools-only server needs is four methods.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Callable

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

API_PREFIX = "/api/v1"
SERVER_NAME = "pixlstash"
# The newest protocol revision this server speaks. A client asking for a
# revision we know is answered with it; anything else gets ours.
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", PROTOCOL_VERSION}
DEFAULT_LIMIT = 20
MAX_LIMIT = 200

# (path, params) -> (status, content type, body). The one seam between the
# protocol and the network, so tests can route requests through a TestClient.
Fetch = Callable[[str, dict], tuple[int, str, bytes]]

_PICTURE_ID = {"type": "integer", "description": "The picture's id."}
_LIMIT = {
    "type": "integer",
    "description": f"Maximum pictures to return (default {DEFAULT_LIMIT}, "
    f"at most {MAX_LIMIT}).",
}
_OFFSET = {"type": "integer", "description": "Pictures to skip, for paging."}
_TAGS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Only pictures carrying every one of these tags.",
}

TOOLS = [
    {
        "name": "search_pictures",
        "description": "Semantic text search over the library, best match "
        "first. Returns picture metadata (id, tags, description, score...).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for."},
                "tags": _TAGS,
                "limit": _LIMIT,
                "offset": _OFFSET,
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_pictures",
        "description": "List pictures, newest first, optionally only those "
        "carrying the given tags. Returns picture metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {"tags": _TAGS, "limit": _LIMIT, "offset": _OFFSET},
        },
    },
    {
        "name": "get_picture",
        "description": "Full metadata for one picture: tags, description, "
        "scores, dimensions and embedded file metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {"picture_id": _PICTURE_ID},
            "required": ["picture_id"],
        },
    },
    {
        "name": "view_picture",
        "description": "The picture itself, as a thumbnail image.",
        "inputSchema": {
            "type": "object",
            "properties": {"picture_id": _PICTURE_ID},
            "required": ["picture_id"],
        },
    },
    {
        "name": "list_tags",
        "description": "Every tag in the library with how many pictures carry it.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recipe",
        "description": "How a picture was made, when it carries a ComfyUI "
        "recipe: prompt, seed, models, LoRAs and node classes.",
        "inputSchema": {
            "type": "object",
            "properties": {"picture_id": _PICTURE_ID},
            "required": ["picture_id"],
        },
    },
]
for _tool in TOOLS:
    _tool["annotations"] = {"readOnlyHint": True, "openWorldHint": False}


class ToolError(Exception):
    """A tool call that failed in a way the agent should be told about."""


def http_fetch(base_url: str, token: str) -> Fetch:
    """Return a :data:`Fetch` that GETs *base_url* with *token* as Bearer."""
    base = base_url.rstrip("/")

    def fetch(path: str, params: dict) -> tuple[int, str, bytes]:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{base}{API_PREFIX}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"}, method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return (
                    response.status,
                    response.headers.get("Content-Type", ""),
                    response.read(),
                )
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()
        except urllib.error.URLError as exc:
            logger.warning("[mcp] Could not reach %s%s: %s", base, path, exc)
            raise ToolError(f"Could not reach PixlStash at {base}: {exc.reason}")

    return fetch


def _picture_id(arguments: dict) -> int:
    """The picture id as an int, so no argument can reshape the request path."""
    value = arguments.get("picture_id")
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ToolError("picture_id must be an integer")
    try:
        return int(value)
    except ValueError:
        raise ToolError("picture_id must be an integer")


def _paging(arguments: dict) -> dict:
    try:
        limit = int(arguments.get("limit") or DEFAULT_LIMIT)
        offset = int(arguments.get("offset") or 0)
    except (TypeError, ValueError):
        raise ToolError("limit and offset must be integers")
    params = {"limit": max(1, min(limit, MAX_LIMIT)), "offset": max(0, offset)}
    tags = arguments.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ToolError("tags must be a list of strings")
    if tags:
        params["tag"] = tags
    return params


def _get(fetch: Fetch, path: str, params: dict | None = None) -> tuple[str, bytes]:
    status, content_type, body = fetch(path, params or {})
    if status != 200:
        try:
            detail = json.loads(body).get("detail", "")
        except (ValueError, AttributeError):
            detail = body[:200].decode("utf-8", "replace")
        raise ToolError(f"PixlStash answered {status}: {detail}")
    return content_type, body


def _json_content(body: bytes) -> list[dict]:
    return [{"type": "text", "text": json.dumps(json.loads(body), indent=1)}]


def call_tool(fetch: Fetch, name: str, arguments: dict) -> list[dict]:
    """Run one tool and return its MCP content blocks; raise ToolError on failure."""
    if name == "search_pictures":
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolError("query must be a non-empty string")
        params = {"query": query, **_paging(arguments)}
        return _json_content(_get(fetch, "/pictures/search", params)[1])
    if name == "list_pictures":
        params = {"sort": "DATE", "descending": "true", **_paging(arguments)}
        return _json_content(_get(fetch, "/pictures", params)[1])
    if name == "get_picture":
        path = f"/pictures/{_picture_id(arguments)}/metadata"
        return _json_content(_get(fetch, path)[1])
    if name == "view_picture":
        path = f"/pictures/thumbnails/{_picture_id(arguments)}.webp"
        content_type, body = _get(fetch, path)
        return [
            {
                "type": "image",
                "data": base64.b64encode(body).decode("ascii"),
                "mimeType": content_type.split(";")[0] or "image/webp",
            }
        ]
    if name == "list_tags":
        return _json_content(_get(fetch, "/tags")[1])
    if name == "get_recipe":
        path = f"/comfyui/pictures/{_picture_id(arguments)}/recipe"
        return _json_content(_get(fetch, path)[1])
    raise ToolError(f"Unknown tool: {name}")


def handle_message(fetch: Fetch, message: dict) -> dict | None:
    """Answer one JSON-RPC message; ``None`` for a notification."""
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None:
        return None
    params = message.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        result = {
            "protocolVersion": requested
            if requested in SUPPORTED_PROTOCOL_VERSIONS
            else PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": _version()},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            content = call_tool(
                fetch, params.get("name"), params.get("arguments") or {}
            )
            result = {"content": content, "isError": False}
        except ToolError as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except Exception as exc:
            # One bad answer (a non-JSON body, a timeout) must not end the
            # session: the agent is told, and the loop keeps serving.
            logger.exception("[mcp] Tool %r failed", params.get("name"))
            result = {
                "content": [{"type": "text", "text": f"Tool failed: {exc}"}],
                "isError": True,
            }
    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve(fetch: Fetch, stdin=None, stdout=None) -> None:
    """Read JSON-RPC lines from *stdin* until EOF, writing answers to *stdout*."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError as exc:
            logger.warning("[mcp] Unparseable message %r: %s", line[:200], exc)
            reply = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        else:
            reply = (
                handle_message(fetch, message) if isinstance(message, dict) else None
            )
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


def _version() -> str:
    try:
        return package_version("pixlstash")
    except PackageNotFoundError:
        logger.warning("[mcp] pixlstash is not installed; reporting version unknown")
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pixlstash-mcp",
        description="Read-only MCP server (stdio) for a running PixlStash "
        "server. Authenticates with the API token in PIXLSTASH_TOKEN; mint a "
        "READ token, optionally restricted to a set, character or project, "
        "and the agent sees exactly what that token sees.",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("PIXLSTASH_URL", "http://127.0.0.1:9537"),
        help="Base URL of the PixlStash server (default: $PIXLSTASH_URL or "
        "http://127.0.0.1:9537).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.environ.get("PIXLSTASH_TOKEN", "").strip()
    if not token:
        print("PIXLSTASH_TOKEN is not set; mint an API token first.", file=sys.stderr)
        return 1
    serve(http_fetch(args.url, token))
    return 0


if __name__ == "__main__":
    sys.exit(main())
