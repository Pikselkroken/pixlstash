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
import http.client
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Callable

from platformdirs import user_config_dir

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

API_PREFIX = "/api/v1"
SERVER_NAME = "pixlstash"
DEFAULT_PORT = 9537
# `app.py`'s SERVER_CONFIG_PATH, spelled again rather than imported: importing
# `pixlstash.app` to read one integer would build the whole FastAPI app.
SERVER_CONFIG_PATH = os.path.join(user_config_dir(SERVER_NAME), "server-config.json")
# The newest protocol revision this server speaks. A client asking for a
# revision we know is answered with it; anything else gets ours.
PROTOCOL_VERSION = "2025-06-18"
# 2025-03-26 is deliberately absent: it mandates JSON-RPC batching, which
# ``serve`` refuses.
SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", PROTOCOL_VERSION}
DEFAULT_LIMIT = 20
MAX_LIMIT = 200
TIMEOUT_SECONDS = 60

# Returned from `initialize`. Without it a client knows only six tool names and
# reaches for `ls` and `find` instead, which cannot see any of this: tags,
# scores, characters, sets, projects and recipes live in PixlStash's database,
# and the files on disk carry none of them.
INSTRUCTIONS = """\
PixlStash is this machine's picture library: the owner's images plus everything \
recorded about them. Use these tools to answer any question about the owner's \
pictures, tags, picture sets, characters, projects or how an image was \
generated.

Prefer them over the shell and the filesystem. Tags, scores, set and character \
membership, project grouping and ComfyUI recipes exist only in PixlStash's \
database - listing image files with ls or find cannot see any of it.

Do not read vault.db or hub.db with sqlite3, and do not query them through any \
other tool. It looks like a shortcut and it gives wrong answers:

- Some values are derived per request, not stored. A picture row carries a raw \
project_id that the API re-derives from project membership before returning \
it, so the stored column can name a project the picture is not really in. Set \
locking and the visible tag set are computed the same way.
- The schema is internal and moves with migrations; these tools are the \
contract, the tables are not.
- The file is live and single-writer while PixlStash runs. An outside reader \
is not part of that design and can see a half-written state.
- Reading the file bypasses the API token's scope, so it can expose parts of \
the library the owner did not share.

If a tool here cannot answer something, say so rather than going around it.

Where to start:
- "how many sets / characters / projects" -> list_sets, list_characters, \
list_projects.
- "pictures of X" or any question about content -> search_pictures, which \
matches meaning rather than filename.
- "pictures in that set / of that character" -> list_pictures with set_id, \
character_id or project_id from the list tools.
- One picture's tags and scores -> get_picture. To actually look at it -> \
view_picture.
- "how was this made" -> get_recipe.

Everything is read-only; there is no tool here that changes the library. A \
refusal means the API token is scoped to part of the library, not that the \
data is missing."""

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
_SET_ID = {
    "type": "integer",
    "description": "Only pictures in this picture set (see list_sets).",
}
_CHARACTER_ID = {
    "type": "integer",
    "description": "Only pictures of this character (see list_characters).",
}
_PROJECT_ID = {
    "type": "string",
    "description": "Only pictures in this project id (see list_projects), or "
    "'UNASSIGNED' for those in none.",
}
# The membership filters every picture listing shares.
_FILTERS = {
    "tags": _TAGS,
    "set_id": _SET_ID,
    "character_id": _CHARACTER_ID,
    "project_id": _PROJECT_ID,
    "limit": _LIMIT,
    "offset": _OFFSET,
}

TOOLS = [
    {
        "name": "search_pictures",
        "description": "Semantic text search over the library, best match "
        "first: finds pictures by what they depict, not by filename. Returns "
        "picture metadata (id, description, score, dimensions, file_path...); "
        "call get_picture for a picture's tags.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for."},
                **_FILTERS,
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_pictures",
        "description": "List pictures, newest first, optionally narrowed to a "
        "set, character, project or tags. Returns picture metadata. Use this "
        "rather than listing image files: the library holds pictures the "
        "filesystem does not show as a collection.",
        "inputSchema": {"type": "object", "properties": dict(_FILTERS)},
    },
    {
        "name": "count_pictures",
        "description": "How many pictures match, without listing them. Answers "
        "'how many pictures are there' in one call, and takes the same set, "
        "character, project and tag filters as list_pictures.",
        "inputSchema": {
            "type": "object",
            "properties": {
                key: value
                for key, value in _FILTERS.items()
                if key not in ("limit", "offset")
            },
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
        "description": "Every tag in the library with how many pictures carry "
        "it. Tags live in PixlStash's database, not in the image files.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_sets",
        "description": "Every picture set: the owner's named collections, with "
        "id, name and how many pictures each holds. Answers 'how many sets are "
        "there' and gives the set_id the picture tools filter by.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_characters",
        "description": "Every character: the recurring subjects the owner "
        "tracks, with id and name. Gives the character_id the picture tools "
        "filter by.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_projects",
        "description": "Every project: the owner's top-level groupings, with "
        "id and name. Gives the project_id the picture tools filter by.",
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
for _entry in TOOLS:
    # get_recipe pre-flights the recipe against the owner's ComfyUI, so it
    # reaches past the library even though it changes nothing.
    _entry["annotations"] = {
        "readOnlyHint": True,
        "openWorldHint": _entry["name"] == "get_recipe",
    }
del _entry


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: urllib would copy the Bearer header to any host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_opener(
    context: ssl.SSLContext | None = None,
) -> urllib.request.OpenerDirector:
    """An opener that reaches the named URL and nothing else.

    The token goes to the URL the owner named and nowhere else, which takes two
    handlers rather than one. Refusing redirects is the obvious half; the empty
    ``ProxyHandler`` is the other, because urllib's default one reads
    ``http_proxy`` from the environment and does not bypass loopback (it
    consults ``no_proxy`` only), so a proxy set for the shell would receive the
    Authorization header.
    """
    handlers: list = [_NoRedirect, urllib.request.ProxyHandler({})]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


class ToolError(Exception):
    """A tool call that failed in a way the agent should be told about."""


def unreachable_message(base: str, reason) -> str:
    """Why nothing answered, in terms the owner can act on.

    "Connection refused" on its own sends people hunting for a crashed server.
    The likelier cause on the desktop is that the port is simply not being
    served: the app's own window runs on a private port it picks per launch,
    and the *configured* port is bound only when remote access is switched on.
    """
    if not isinstance(reason, ConnectionRefusedError):
        return f"Could not reach PixlStash at {base}: {reason}"
    return (
        f"Nothing is listening on {base}. Either PixlStash is not running, or "
        "it is the desktop app with remote access switched off - the app "
        "serves its own window on a private port that changes every launch, "
        "and only binds the configured port when remote access is enabled in "
        "its settings. Enable it, start the server, or point this server "
        "somewhere else with --url or PIXLSTASH_URL."
    )


def http_fetch(
    base_url: str, token: str, context: ssl.SSLContext | None = None
) -> Fetch:
    """Return a :data:`Fetch` that GETs *base_url* with *token* as Bearer."""
    base = base_url.rstrip("/")
    opener = _build_opener(context)

    def fetch(path: str, params: dict) -> tuple[int, str, bytes]:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{base}{API_PREFIX}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"}, method="GET"
        )
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                return (
                    response.status,
                    response.headers.get("Content-Type", ""),
                    response.read(),
                )
        except urllib.error.HTTPError as exc:
            logger.warning("[mcp] %s answered %s", path, exc.code)
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()
        except urllib.error.URLError as exc:
            logger.warning("[mcp] Could not reach %s%s: %s", base, path, exc)
            raise ToolError(unreachable_message(base, exc.reason)) from exc
        except http.client.RemoteDisconnected as exc:
            # The server accepted the connection and then hung up without
            # speaking HTTP. On this port that means one thing far more often
            # than any other: it is serving TLS and we knocked in plain text.
            logger.warning("[mcp] %s closed the connection on %s", base, path)
            raise ToolError(
                f"{base} accepted the connection and closed it without "
                "answering. That is what a TLS listener does when it is sent "
                "plain HTTP, so PixlStash is probably serving https on this "
                "port. Check `require_ssl` in its server-config.json, and "
                "point this server at that file with --server-config."
            ) from exc
        except ssl.SSLError as exc:
            logger.warning("[mcp] TLS failed against %s: %s", base, exc)
            raise ToolError(
                f"Could not establish a secure connection to {base}: {exc}. "
                "PixlStash generates its own certificate; --server-config "
                "points here at the file naming it."
            ) from exc
        except TimeoutError as exc:
            # Only the *send* is wrapped in URLError; a read timeout arrives
            # bare. The first search loads the text encoder, which is the call
            # most likely to sit here.
            logger.warning("[mcp] %s timed out after %ss", path, TIMEOUT_SECONDS)
            raise ToolError(
                f"PixlStash did not answer {path} within {TIMEOUT_SECONDS}s."
            ) from exc

    return fetch


def _picture_id(arguments: dict) -> int:
    """The picture id as an int, so no argument can reshape the request path."""
    value = arguments.get("picture_id")
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ToolError("picture_id must be an integer")
    try:
        return int(value)
    except ValueError as exc:
        raise ToolError("picture_id must be an integer") from exc


def _paging(arguments: dict) -> dict:
    try:
        limit = int(arguments.get("limit") or DEFAULT_LIMIT)
        offset = int(arguments.get("offset") or 0)
    except (TypeError, ValueError) as exc:
        raise ToolError("limit and offset must be integers") from exc
    params = {"limit": max(1, min(limit, MAX_LIMIT)), "offset": max(0, offset)}
    tags = arguments.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ToolError("tags must be a list of strings")
    if tags:
        params["tag"] = tags
    # Membership filters. `GET /pictures` already takes all three, and a
    # scoped token still only sees what its scope allows: these narrow the
    # listing, they never widen it.
    for key in ("set_id", "character_id", "project_id"):
        value = arguments.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ToolError(f"{key} must be an id")
        params[key] = str(value)
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
    if name == "count_pictures":
        # The paging keys are dropped from the schema but _paging still builds
        # the filter params; the route ignores limit and offset.
        return _json_content(_get(fetch, "/pictures/count", _paging(arguments))[1])
    if name == "list_tags":
        return _json_content(_get(fetch, "/tags")[1])
    if name == "list_sets":
        return _json_content(_get(fetch, "/picture_sets")[1])
    if name == "list_characters":
        return _json_content(_get(fetch, "/characters")[1])
    if name == "list_projects":
        return _json_content(_get(fetch, "/projects")[1])
    if name == "get_recipe":
        path = f"/comfyui/pictures/{_picture_id(arguments)}/recipe"
        return _json_content(_get(fetch, path)[1])
    raise ToolError(f"Unknown tool: {name}")


def handle_message(fetch: Fetch, message: dict) -> dict | None:
    """Answer one JSON-RPC message; ``None`` for a notification."""
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None:
        return None  # A notification, or a reply to nothing.
    if method is None:
        # An id with no method is a client response to a request this server
        # never makes, or an invalid request. Either way it needs an answer,
        # or the client waits on that id until its own timeout.
        return _error(msg_id, -32600, "Invalid Request: no method")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(msg_id, -32602, "params must be an object")
    if method == "initialize":
        requested = params.get("protocolVersion")
        result = {
            "protocolVersion": requested
            if requested in SUPPORTED_PROTOCOL_VERSIONS
            else PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": _version()},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise ToolError("arguments must be an object")
            content = call_tool(fetch, params.get("name"), arguments)
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
        return _error(msg_id, -32601, f"Method not found: {method}")
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


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
            # ponytail: batches (2025-03-26 only) are refused, not unpacked.
            reply = (
                handle_message(fetch, message)
                if isinstance(message, dict)
                else _error(None, -32600, "Invalid Request: expected one object")
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


def read_server_config(path: str | None = None) -> dict:
    """The server's own ``server-config.json``, or ``{}`` if it cannot be read.

    Which file this is decides everything below, and there is more than one on
    a machine: a pip install uses the platform config dir, while the desktop
    app keeps its own beside its Electron user data. Getting this wrong reads
    somebody else's port and scheme, so the desktop shim passes its path
    explicitly rather than letting us guess.
    """
    path = path or os.environ.get("PIXLSTASH_SERVER_CONFIG") or SERVER_CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
        if not isinstance(config, dict):
            raise ValueError(f"expected an object, got {type(config).__name__}")
        return config
    except (OSError, ValueError) as exc:
        logger.warning("[mcp] Could not read %s (%s); using defaults", path, exc)
        return {}


def configured_url(config: dict | None = None) -> str:
    """Where the server is, from the config the server itself reads.

    Both halves matter. The port, because the desktop shell serves its window
    on an ephemeral one that changes every launch, so anything taken from a
    browser is stale by the next start-up. And the scheme, because a TLS
    listener answers a plain HTTP request by closing the connection, which
    surfaces as "Remote end closed connection without response" and looks for
    all the world like a crashed server.
    """
    config = read_server_config() if config is None else config
    scheme = "https" if config.get("require_ssl") else "http"
    try:
        port = int(config.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        logger.warning(
            "[mcp] Ignoring unusable port %r; using %s",
            config.get("port"),
            DEFAULT_PORT,
        )
        port = DEFAULT_PORT
    return f"{scheme}://127.0.0.1:{port}"


def ssl_context_for(config: dict) -> ssl.SSLContext | None:
    """Trust the server's own certificate, which is normally self-signed.

    PixlStash generates its own certificate, so the system trust store will
    never contain it. Pinning that one file keeps verification switched on -
    including the hostname check, since the generated certificate carries
    127.0.0.1 in its SANs - rather than reaching for the usual
    ``check_hostname = False`` and accepting anything on the port.
    """
    if not config.get("require_ssl"):
        return None
    certfile = config.get("ssl_certfile")
    if certfile and os.path.exists(certfile):
        return ssl.create_default_context(cafile=certfile)
    logger.warning(
        "[mcp] require_ssl is set but %r is not readable; using the system "
        "trust store, which will reject a self-signed certificate",
        certfile,
    )
    return ssl.create_default_context()


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
        # Resolved in main(), not here: it depends on --server-config, which
        # argparse has not read yet while the defaults are being built.
        default=None,
        help="Base URL of the PixlStash server. Defaults to $PIXLSTASH_URL, "
        "else the scheme and port in server-config.json, else "
        f"http://127.0.0.1:{DEFAULT_PORT}.",
    )
    parser.add_argument(
        "--server-config",
        default=None,
        help="PixlStash's server-config.json, which names the port, whether it "
        "serves https, and its certificate. Defaults to "
        f"$PIXLSTASH_SERVER_CONFIG, else {SERVER_CONFIG_PATH}. The desktop app "
        "keeps its own copy elsewhere and its shim passes that path.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # A client pipes UTF-8; without this Python decodes stdin with the locale
    # encoding, which mangles a non-ASCII query on Windows (cp1252). Replies
    # are ASCII by json.dumps default.
    sys.stdin.reconfigure(encoding="utf-8")
    token = os.environ.get("PIXLSTASH_TOKEN", "").strip()
    if not token:
        print("PIXLSTASH_TOKEN is not set; mint an API token first.", file=sys.stderr)
        return 1
    # One config decides the port, the scheme and which certificate to trust,
    # so it is read once and all three come from the same file.
    config = read_server_config(args.server_config)
    url = args.url or os.environ.get("PIXLSTASH_URL") or configured_url(config)
    fetch = http_fetch(url, token, ssl_context_for(config))
    # Say so now, on stderr, rather than letting the first tool call be the
    # first the owner hears of it: a client that starts this server at launch
    # shows nothing until something is asked of it. Not fatal - PixlStash may
    # simply start later, and exiting would have the client give up for good.
    warn_if_unreachable(fetch, url)
    serve(fetch)
    return 0


def warn_if_unreachable(fetch: Fetch, url: str) -> bool:
    """Probe the server once; return whether it answered."""
    try:
        fetch("/tags", {"limit": 1})
    except ToolError as exc:
        print(f"pixlstash-mcp: {exc}", file=sys.stderr)
        return False
    except Exception as exc:  # A probe must never stop the server starting.
        logger.warning("[mcp] Start-up probe of %s failed: %s", url, exc)
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
