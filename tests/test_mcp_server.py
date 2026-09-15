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
import tempfile
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pixlstash import mcp_server
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
                    "params": {"protocolVersion": "2025-03-26"},
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
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, None, 8, None]
    assert replies[0]["result"]["protocolVersion"] == "2025-03-26"
    assert replies[0]["result"]["capabilities"] == {"tools": {}}
    tools = replies[1]["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "search_pictures",
        "list_pictures",
        "get_picture",
        "view_picture",
        "list_tags",
        "get_recipe",
    }
    assert all(t["annotations"]["readOnlyHint"] is True for t in tools)
    assert replies[2]["error"]["code"] == -32601
    assert replies[3]["error"]["code"] == -32602
    assert replies[4]["error"]["code"] == -32602
    assert replies[5]["error"]["code"] == -32600
    assert replies[6]["result"] == {}
    assert replies[7]["error"]["code"] == -32700


def test_a_redirect_is_refused_rather_than_followed():
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.end_headers()

        def log_message(self, *args):
            return None

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        fetch = mcp_server.http_fetch(
            f"http://127.0.0.1:{httpd.server_address[1]}", "example-token"
        )
        result = _call(fetch, "list_tags")
    finally:
        httpd.shutdown()
    assert result["isError"] is True
    assert "answered 302" in result["content"][0]["text"]
    # Positive control that the server was reached, and only once.
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

    monkeypatch.setattr(mcp_server._opener, "open", urlopen)
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


def test_list_tags_answers_a_scoped_token(env):
    result = _call(env.scoped, "list_tags")
    assert result["isError"] is False, result
    assert isinstance(json.loads(result["content"][0]["text"]), list)
