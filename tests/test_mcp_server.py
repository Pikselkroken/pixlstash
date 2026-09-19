"""The read-only MCP server: protocol shape, and token scope through every tool.

The scope tests route the MCP tool layer through a TestClient against a real
Server, so what they prove is that the tools reach the library only through the
auth middleware and the authz gate. Every negative sits beside an in-scope
positive with the same token, and beside the same call made with an owner
token, so a refusal cannot pass because the credential was dead or the picture
missing.
"""

import http.server
import io
import json
import re
import socket
import tempfile
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pixlstash import mcp_server
from pixlstash.route_inventory import api_endpoint_set
from pixlstash.server import Server
from tests.utils import upload_pictures_and_wait

API = "/api/v1"
PICTURE_TOOLS = ("get_picture", "view_picture", "get_recipe")


def _png(size: int, colour: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color=colour).save(buf, format="PNG")
    return buf.getvalue()


def _fetch(client: TestClient, token: str) -> mcp_server.Fetch:
    def fetch(path, params):
        r = client.get(
            f"{API}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.status_code, r.headers.get("content-type", ""), r.content

    return fetch


def _call(fetch, name, **arguments) -> dict:
    reply = mcp_server.handle_message(
        fetch,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    return reply["result"]


def _picture_ids(result: dict) -> set[int]:
    assert result["isError"] is False, result
    return {row["id"] for row in json.loads(result["content"][0]["text"])}


@pytest.fixture(scope="module")
def env():
    with tempfile.TemporaryDirectory() as tmp:
        with Server(f"{tmp}/server-config.json") as server:
            client = TestClient(server.api, raise_server_exceptions=True)
            r = client.post(
                f"{API}/login",
                json={"username": "owner", "password": "example-owner-password"},
            )
            assert r.status_code == 200, r.text
            for name, size, colour in [
                ("a.png", 64, (200, 40, 40)),
                ("b.png", 48, (40, 40, 200)),
            ]:
                status = upload_pictures_and_wait(
                    client, [("file", (name, _png(size, colour), "image/png"))]
                )
                assert status["status"] == "completed", status
            ids = [p["id"] for p in client.get(f"{API}/pictures").json()]
            assert len(ids) == 2, ids

            def mint(**body):
                r = client.post(
                    f"{API}/users/me/token", json={"description": "mcp", **body}
                )
                assert r.status_code == 200, r.text
                return r.json()["token"]

            # A client with no session cookie, so the token is the only credential.
            bare = TestClient(server.api, raise_server_exceptions=True)
            yield SimpleNamespace(
                api=server.api,
                pic_a=ids[0],
                pic_b=ids[1],
                scoped=_fetch(
                    bare,
                    mint(scope="READ", resource_type="picture", resource_id=ids[0]),
                ),
                owner=_fetch(bare, mint(scope="ALL")),
            )


def test_protocol_handshake_and_tool_list():
    stdin = io.StringIO(
        "\n".join(
            json.dumps(m)
            for m in [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"},
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
                {"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": [1]},
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": [1]},
                [{"jsonrpc": "2.0", "id": 6, "method": "ping"}],
                {"jsonrpc": "2.0", "id": 7, "result": {}},
                {"jsonrpc": "2.0", "id": 8, "method": "ping"},
            ]
        )
        + "\nnot json\n"
    )
    stdout = io.StringIO()
    mcp_server.serve(
        lambda path, params: pytest.fail("no tool was called"), stdin, stdout
    )
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]

    # Notifications and client responses are not answered; everything else
    # is, and a malformed message does not end the session.
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, None, 7, 8, None]
    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    assert replies[0]["result"]["capabilities"] == {"tools": {}}
    tools = replies[1]["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "search_pictures",
        "list_pictures",
        "get_picture",
        "view_picture",
        "list_tags",
        "list_sets",
        "list_characters",
        "list_projects",
        "get_recipe",
    }
    assert all(t["annotations"]["readOnlyHint"] is True for t in tools)
    # The handshake has to say what the server is for. Without `instructions`
    # a client sees six names and reaches for `ls` instead, which cannot see
    # tags, sets or recipes at all.
    instructions = replies[0]["result"]["instructions"]
    assert "PixlStash" in instructions
    for mentioned in ("list_sets", "search_pictures", "get_recipe"):
        assert mentioned in instructions
    assert replies[2]["error"]["code"] == -32601
    assert replies[3]["error"]["code"] == -32602
    assert replies[4]["error"]["code"] == -32602
    # A batch, and an id with no method, are both invalid requests.
    assert replies[5]["error"]["code"] == -32600
    assert replies[6]["error"]["code"] == -32600
    assert replies[7]["result"] == {}
    assert replies[8]["error"]["code"] == -32700


def test_an_unknown_protocol_version_gets_ours():
    reply = mcp_server.handle_message(
        None,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01"},
        },
    )
    assert reply["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION


def test_the_default_url_is_the_configured_port_not_a_guess(tmp_path, monkeypatch):
    """The port the server is configured for, read from the file it reads.

    The desktop shell serves its window from an ephemeral loopback port that
    changes on every launch, so a URL taken from the browser is written into a
    client's config file and is dead by the next start-up. Only
    server-config.json is authoritative.
    """
    config = tmp_path / "server-config.json"
    config.write_text(json.dumps({"port": 12345}), encoding="utf-8")
    monkeypatch.setattr(mcp_server, "SERVER_CONFIG_PATH", str(config))
    monkeypatch.delenv("PIXLSTASH_URL", raising=False)

    assert mcp_server.configured_url() == "http://127.0.0.1:12345"
    assert mcp_server.build_parser().parse_args([]).url == "http://127.0.0.1:12345"

    # An explicit --url still wins, and so does the environment.
    assert (
        mcp_server.build_parser().parse_args(["--url", "http://example.test"]).url
        == "http://example.test"
    )
    monkeypatch.setenv("PIXLSTASH_URL", "http://127.0.0.1:1")
    assert mcp_server.build_parser().parse_args([]).url == "http://127.0.0.1:1"


@pytest.mark.parametrize(
    "contents", ["", "not json", json.dumps([]), json.dumps({"port": "nonsense"})]
)
def test_an_unreadable_config_falls_back_to_the_default_port(
    tmp_path, monkeypatch, contents
):
    """A broken or absent config must not stop the server starting."""
    config = tmp_path / "server-config.json"
    config.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(mcp_server, "SERVER_CONFIG_PATH", str(config))

    assert mcp_server.configured_url() == f"http://127.0.0.1:{mcp_server.DEFAULT_PORT}"

    # And when the file is not there at all.
    monkeypatch.setattr(mcp_server, "SERVER_CONFIG_PATH", str(tmp_path / "absent.json"))
    assert mcp_server.configured_url() == f"http://127.0.0.1:{mcp_server.DEFAULT_PORT}"


def test_a_closed_port_says_why_rather_than_connection_refused():
    """The desktop app binds the configured port only for remote access.

    "Connection refused" alone sends the owner hunting for a crashed server,
    when the likelier cause is that the port is simply not being served.
    """
    # Bind and drop a port, so nothing is listening on an address that exists.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]

    fetch = mcp_server.http_fetch(f"http://127.0.0.1:{dead_port}", "example-token")
    result = _call(fetch, "list_tags")
    text = result["content"][0]["text"]

    assert result["isError"] is True
    assert f"Nothing is listening on http://127.0.0.1:{dead_port}" in text
    assert "remote access" in text
    assert "--url" in text
    # The probe reports the same thing, so start-up says it before any tool.
    assert mcp_server.warn_if_unreachable(fetch, "irrelevant") is False


def test_a_non_refusal_keeps_its_own_reason():
    """Only a refused connection gets the remote-access explanation."""
    message = mcp_server.unreachable_message(
        "http://127.0.0.1:9537", OSError("name resolution went wrong")
    )
    assert "name resolution went wrong" in message
    assert "remote access" not in message


def test_membership_filters_reach_the_listing_as_query_params():
    seen = []

    def fetch(path, params):
        seen.append((path, dict(params)))
        return 200, "application/json", b"[]"

    _call(fetch, "list_pictures", set_id=7, character_id=3, project_id="UNASSIGNED")
    path, params = seen[0]
    assert path == "/pictures"
    assert params["set_id"] == "7"
    assert params["character_id"] == "3"
    assert params["project_id"] == "UNASSIGNED"

    # A filter that is not an id is refused rather than sent.
    assert _call(fetch, "list_pictures", set_id={"a": 1})["isError"] is True
    assert len(seen) == 1


@contextmanager
def _recording_server(status: int = 200, body: bytes = b"[]"):
    """A loopback HTTP server that records what actually reached it."""
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.path, self.headers.get("Authorization")))
            self.send_response(status)
            if status >= 300:
                self.send_header("Location", "/elsewhere")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return None

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], hits
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def test_a_redirect_is_refused_rather_than_followed():
    with _recording_server(status=302) as (port, hits):
        fetch = mcp_server.http_fetch(f"http://127.0.0.1:{port}", "example-token")
        result = _call(fetch, "list_tags")
    assert result["isError"] is True
    assert "answered 302" in result["content"][0]["text"]
    # Positive control that the server was reached, and only once.
    assert hits == [("/api/v1/tags", "Bearer example-token")]


def test_a_proxy_in_the_environment_never_sees_the_token(monkeypatch):
    """The token goes to the named URL, whatever the shell's proxy vars say.

    urllib's default ProxyHandler reads these and does not bypass loopback, so
    without the empty ProxyHandler this request would carry the Authorization
    header to proxy.example.test instead.
    """
    for var in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, "http://proxy.example.test:3128")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    with _recording_server() as (port, hits):
        fetch = mcp_server.http_fetch(f"http://127.0.0.1:{port}", "example-token")
        result = _call(fetch, "list_tags")
    assert result["isError"] is False, result
    assert hits == [("/api/v1/tags", "Bearer example-token")]


def test_a_picture_id_cannot_reshape_the_request_path():
    requested = []

    def fetch(path, params):
        requested.append(path)
        return 200, "application/json", b"{}"

    for name in PICTURE_TOOLS:
        result = _call(fetch, name, picture_id="1/../../users/me/tokens")
        assert result["isError"] is True
    assert requested == []
    # Positive control: a well-formed id does reach the transport.
    assert _call(fetch, "get_picture", picture_id="7")["isError"] is False
    assert requested == ["/pictures/7/metadata"]


def test_a_broken_answer_is_a_tool_error_not_a_crash():
    def fetch(path, params):
        return 200, "text/html", b"<html>proxy login</html>"

    result = _call(fetch, "list_tags")
    assert result["isError"] is True


def test_every_tool_request_is_a_get_through_http_fetch(monkeypatch):
    seen = []

    class Response:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"[]"

    def urlopen(request, timeout):
        seen.append((request.get_method(), request.full_url, request.headers))
        return Response()

    monkeypatch.setattr(
        mcp_server, "_build_opener", lambda: SimpleNamespace(open=urlopen)
    )
    fetch = mcp_server.http_fetch("http://127.0.0.1:9537/", "example-token")
    _call(fetch, "list_pictures", tags=["cat", "dog"], limit=5000)
    method, url, headers = seen[0]
    assert method == "GET"
    assert url.startswith("http://127.0.0.1:9537/api/v1/pictures?")
    assert "tag=cat&tag=dog" in url and "limit=200" in url
    assert headers["Authorization"] == "Bearer example-token"

    # Search is checked here rather than against a live server: the first
    # search loads the text encoder, and doing that while the planner imports
    # the same libraries is a race of its own in a freshly booted test server.
    _call(fetch, "search_pictures", query="red square", tags=["cat"])
    method, url, _headers = seen[1]
    assert method == "GET"
    assert url.startswith("http://127.0.0.1:9537/api/v1/pictures/search?")
    assert "query=red+square" in url and "tag=cat" in url


def test_every_tool_path_resolves_to_a_mounted_route(env):
    """A renamed route must break the tool, not pass a string comparison.

    The READ-token middleware answers a nonexistent path with the same 403 a
    real refusal gets, so the scope tests below cannot tell a dead path from an
    enforced one. The paths here are the ones the tool code builds.
    """
    paths = []

    def fetch(path, params):
        paths.append(path)
        return 200, "application/json", b"[]"

    _call(fetch, "search_pictures", query="anything")
    _call(fetch, "list_pictures")
    for tool in PICTURE_TOOLS:
        _call(fetch, tool, picture_id=env.pic_a)
    for tool in ("list_tags", "list_sets", "list_characters", "list_projects"):
        _call(fetch, tool)
    # Every tool is exercised, so a new one cannot skip this check by
    # forgetting to be listed here.
    assert len(paths) == len(mcp_server.TOOLS)

    # Every GET path template the server mounts, via the same inventory the
    # authz guardrails use (FastAPI's lazy routers hide them from app.routes).
    templates = [path for method, path in api_endpoint_set(env.api) if method == "GET"]
    # The SPA catch-all matches every string, so it is excluded or this
    # assertion would pass on any typo.
    patterns = [
        # Literal parts escaped, or the `.` in `/pictures/{id}.{ext}` would
        # make that template match almost any single-segment path.
        re.compile(
            "^"
            + "[^/]+".join(re.escape(part) for part in re.split(r"\{[^}]+\}", template))
            + "$"
        )
        for template in templates
        if ":path" not in template
    ]
    assert patterns, "no GET routes found to match against"
    for path in paths:
        assert any(pattern.match(f"{API}{path}") for pattern in patterns), (
            f"no mounted route matches {path}"
        )


def test_listing_sees_only_what_the_token_reaches(env):
    assert _picture_ids(_call(env.owner, "list_pictures")) == {env.pic_a, env.pic_b}
    assert _picture_ids(_call(env.scoped, "list_pictures")) == {env.pic_a}


@pytest.mark.parametrize("tool", PICTURE_TOOLS)
def test_picture_tools_refuse_a_picture_outside_the_token(env, tool):
    # In scope, the same token reads.
    assert _call(env.scoped, tool, picture_id=env.pic_a)["isError"] is False
    # The picture exists and is readable with an owner token.
    assert _call(env.owner, tool, picture_id=env.pic_b)["isError"] is False

    refused = _call(env.scoped, tool, picture_id=env.pic_b)
    assert refused["isError"] is True
    assert "answered 403" in refused["content"][0]["text"]


def test_view_picture_returns_an_image(env):
    content = _call(env.scoped, "view_picture", picture_id=env.pic_a)["content"]
    assert content[0]["type"] == "image"
    assert content[0]["mimeType"] == "image/webp"
    assert content[0]["data"]


@pytest.mark.parametrize("tool", ["list_sets", "list_characters", "list_projects"])
def test_the_collection_tools_answer_the_owner_with_a_list(env, tool):
    """Each is wired to a route that exists and answers.

    The READ middleware refuses a path that does not exist with the same 403 a
    real refusal gets, so an owner token answering with a list is what tells a
    working tool from a typo.
    """
    result = _call(env.owner, tool)
    assert result["isError"] is False, result
    assert isinstance(json.loads(result["content"][0]["text"]), list)


def test_a_picture_scoped_token_sees_collections_only_where_its_scope_allows(env):
    """Scope is the route's, not the client's, and it is not uniform.

    A picture-scoped token still gets the set and character listings (narrowed
    by `SCOPED_LIST`), but `/projects` refuses a picture-scoped token outright
    by resource type. Pinned because the difference is the routes' decision and
    the tools must pass it through rather than paper over it.
    """
    for tool in ("list_sets", "list_characters"):
        result = _call(env.scoped, tool)
        assert result["isError"] is False, result
        assert isinstance(json.loads(result["content"][0]["text"]), list)

    refused = _call(env.scoped, "list_projects")
    assert refused["isError"] is True
    assert "answered 403" in refused["content"][0]["text"]


def test_list_tags_answers_a_scoped_token(env):
    result = _call(env.scoped, "list_tags")
    assert result["isError"] is False, result
    assert isinstance(json.loads(result["content"][0]["text"]), list)
