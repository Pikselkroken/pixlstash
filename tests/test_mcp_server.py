"""The MCP server: protocol shape, token scope through every tool, write gating.

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
import datetime
import ipaddress
import re
import socket
import ssl
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
    def fetch(path, params, method="GET", body=None):
        r = client.request(
            method,
            f"{API}{path}",
            params=params,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.status_code, r.headers.get("content-type", ""), r.content

    return fetch


def _call(fetch, tool, allow_write=False, **arguments) -> dict:
    reply = mcp_server.handle_message(
        fetch,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        allow_write,
    )
    return reply["result"]


def _write(fetch, tool, **arguments) -> dict:
    return _call(fetch, tool, allow_write=True, **arguments)


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
                # What the read-only Connect dialog mints: unscoped READ.
                reader=_fetch(bare, mint(scope="READ")),
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
        "count_pictures",
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
    # Naming the shortcut is the point. A general "prefer these tools" loses to
    # a sqlite3 call that looks authoritative, so the instructions have to say
    # which files not to open and give a reason that survives being argued with.
    assert "vault.db" in instructions and "sqlite3" in instructions
    assert "derived" in instructions
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


def test_https_is_taken_from_the_config_not_assumed(tmp_path, monkeypatch):
    """A TLS listener sent plain HTTP just hangs up, which reads as a crash.

    `require_ssl` decides the scheme. Getting it wrong is not a clean failure:
    the connection is accepted and closed with no reply, which surfaces as
    "Remote end closed connection without response".
    """
    config = tmp_path / "server-config.json"
    config.write_text(json.dumps({"port": 9537, "require_ssl": True}), encoding="utf-8")
    monkeypatch.setattr(mcp_server, "SERVER_CONFIG_PATH", str(config))
    monkeypatch.delenv("PIXLSTASH_URL", raising=False)

    assert mcp_server.configured_url() == "https://127.0.0.1:9537"
    # The positive control: the same file without the flag stays on http.
    config.write_text(json.dumps({"port": 9537}), encoding="utf-8")
    assert mcp_server.configured_url() == "http://127.0.0.1:9537"


def test_the_servers_own_certificate_is_trusted_and_still_verified(tmp_path):
    """PixlStash signs its own certificate, so the system store never has it.

    Pinning that one file keeps verification on, including the hostname check.
    Turning verification off instead would accept anything on the port.
    """
    assert mcp_server.ssl_context_for({}) is None
    assert mcp_server.ssl_context_for({"require_ssl": False}) is None

    missing = mcp_server.ssl_context_for(
        {"require_ssl": True, "ssl_certfile": str(tmp_path / "absent.pem")}
    )
    assert missing is not None and missing.verify_mode == ssl.CERT_REQUIRED

    # A real self-signed certificate, trusted by name rather than by giving up.
    certfile = tmp_path / "cert.pem"
    certfile.write_text(_self_signed_pem(), encoding="utf-8")
    context = mcp_server.ssl_context_for(
        {"require_ssl": True, "ssl_certfile": str(certfile)}
    )
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    # get_ca_certs() lists only CA-flagged certificates, and a self-signed
    # server certificate is not one; the store count is what says it loaded.
    assert context.cert_store_stats()["x509"] >= 1


def test_a_tls_listener_sent_plain_http_explains_itself():
    """The exact failure the desktop hit: https on the port, http in the client."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def hang_up_on_plaintext():
            conn, _ = server.accept()
            # What a TLS listener does with a plaintext GET: no reply at all.
            conn.recv(1024)
            conn.close()

        thread = threading.Thread(target=hang_up_on_plaintext, daemon=True)
        thread.start()
        fetch = mcp_server.http_fetch(f"http://127.0.0.1:{port}", "example-token")
        result = _call(fetch, "list_tags")
        thread.join(timeout=5)

    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "closed it without answering" in text
    assert "https" in text and "--server-config" in text


def _self_signed_pem() -> str:
    """A throwaway self-signed certificate, shaped like PixlStash's own."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pixlstash-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2040, 1, 1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


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

    # --url is resolved in main(), because it depends on --server-config, which
    # argparse has not read while the defaults are being built. Left unset it
    # is None and the config answers.
    assert mcp_server.build_parser().parse_args([]).url is None
    assert (
        mcp_server.build_parser().parse_args(["--url", "http://example.test"]).url
        == "http://example.test"
    )

    # And the config a caller names is the one read, which is the whole point:
    # the desktop app keeps its own, elsewhere.
    other = tmp_path / "desktop-server-config.json"
    other.write_text(json.dumps({"port": 9999, "require_ssl": True}), encoding="utf-8")
    assert (
        mcp_server.configured_url(mcp_server.read_server_config(str(other)))
        == "https://127.0.0.1:9999"
    )


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


def test_an_untrusted_certificate_says_so_and_names_the_config():
    """The TLS explanation has to live where the failure actually arrives.

    urllib wraps the handshake in URLError, so an `except ssl.SSLError` around
    the request never runs: reproduced against a local TLS listener, what comes
    out is a URLError carrying the SSLError. The message therefore belongs in
    `unreachable_message`, beside the refused-connection case.
    """
    message = mcp_server.unreachable_message(
        "https://127.0.0.1:9537",
        ssl.SSLCertVerificationError("certificate verify failed: self-signed"),
    )
    assert "secure connection" in message
    assert "--server-config" in message
    assert "self-signed" in message
    # Not mistaken for the port being closed.
    assert "Nothing is listening" not in message


def test_the_start_up_probe_sees_a_rejected_token(monkeypatch):
    """A 401 is an answer, and `fetch` returns it rather than raising.

    Probing through `fetch` therefore called a dead token healthy and left the
    owner to discover it at the first tool call. `_get` is what turns a
    non-200 into a ToolError.
    """
    calls = []

    def unauthorised(path, params):
        calls.append(path)
        return 401, "application/json", b'{"detail":"Invalid token"}'

    assert (
        mcp_server.warn_if_unreachable(unauthorised, "http://127.0.0.1:9537") is False
    )
    # And the cheap count route, not the tag GROUP BY, is what it asks for.
    assert calls == ["/pictures/count"]

    def healthy(path, params):
        return 200, "application/json", b'{"count": 3}'

    assert mcp_server.warn_if_unreachable(healthy, "http://127.0.0.1:9537") is True


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


class _Response:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"[]"


def test_every_read_tool_request_is_a_get_through_http_fetch(monkeypatch):
    seen = []

    def urlopen(request, timeout):
        seen.append((request.get_method(), request.full_url, request.headers))
        return _Response()

    monkeypatch.setattr(
        mcp_server, "_build_opener", lambda context=None: SimpleNamespace(open=urlopen)
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


def test_every_tool_path_resolves_to_a_mounted_route(env, tmp_path):
    """A renamed route must break the tool, not pass a string comparison.

    The READ-token middleware answers a nonexistent path with the same 403 a
    real refusal gets, so the scope tests below cannot tell a dead path from an
    enforced one. The (method, path) pairs here are the ones the tool code
    builds, for the write tools as well as the read ones.
    """
    requests = []

    def fetch(path, params, method="GET", body=None):
        requests.append((method, path))
        answer = {"workflow": {}} if path.endswith("/graph") else {}
        return 200, "application/json", json.dumps(answer).encode()

    _call(fetch, "search_pictures", query="anything")
    _call(fetch, "list_pictures")
    for tool in PICTURE_TOOLS:
        _call(fetch, tool, picture_id=env.pic_a)
    _call(fetch, "count_pictures")
    for tool in ("list_tags", "list_sets", "list_characters", "list_projects"):
        _call(fetch, tool)
    key = "a" * 64
    _write(fetch, "list_workflows")
    _write(fetch, "get_workflow", workflow_key=key)
    _write(
        fetch,
        "export_workflow_graph",
        workflow_key=key,
        out_path=str(tmp_path / "g.json"),
    )
    _write(fetch, "import_workflow_graph", name="x.json", workflow={})
    _write(fetch, "preflight_workflow", workflow_key=key)
    _write(fetch, "run_workflow", workflow_key=key)
    # Every tool is exercised, so a new one cannot skip this check by
    # forgetting to be listed here.
    assert len(requests) == len(mcp_server.tools_for(allow_write=True))

    # Every route the server mounts, via the same inventory the authz
    # guardrails use (FastAPI's lazy routers hide them from app.routes). It
    # reflects over mounted routes, so the import route, which is left out of
    # the OpenAPI document, is still in it.
    mounted = api_endpoint_set(env.api)
    assert ("POST", f"{API}/comfyui/workflows/import") in mounted
    # The SPA catch-all matches every string, so it is excluded or this
    # assertion would pass on any typo.
    patterns = [
        (
            method,
            # Literal parts escaped, or the `.` in `/pictures/{id}.{ext}` would
            # make that template match almost any single-segment path.
            re.compile(
                "^"
                + "[^/]+".join(
                    re.escape(part) for part in re.split(r"\{[^}]+\}", template)
                )
                + "$"
            ),
        )
        for method, template in mounted
        if ":path" not in template
    ]
    assert patterns, "no routes found to match against"
    for method, path in requests:
        assert any(
            method == route_method and pattern.match(f"{API}{path}")
            for route_method, pattern in patterns
        ), f"no mounted route matches {method} {path}"


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


# --- --allow-write --------------------------------------------------------

WRITE_TOOL_NAMES = {
    "list_workflows",
    "get_workflow",
    "export_workflow_graph",
    "import_workflow_graph",
    "preflight_workflow",
    "run_workflow",
}


def _list(allow_write):
    reply = mcp_server.handle_message(
        None, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, allow_write
    )
    return {t["name"]: t for t in reply["result"]["tools"]}


def _instructions(allow_write):
    reply = mcp_server.handle_message(
        None,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        allow_write,
    )
    return reply["result"]["instructions"]


def test_the_workflow_tools_exist_only_with_allow_write():
    read_only = _list(False)
    assert len(read_only) == 10
    assert not WRITE_TOOL_NAMES & set(read_only)

    full = _list(True)
    assert set(full) == set(read_only) | WRITE_TOOL_NAMES
    # A client auto-approves a read-only tool, so each hint is pinned: the
    # file writer and the stored-file overwriter are not reads.
    hints = {
        name: (
            tool["annotations"]["readOnlyHint"],
            tool["annotations"].get("destructiveHint"),
        )
        for name, tool in full.items()
        if name in WRITE_TOOL_NAMES
    }
    assert hints == {
        "list_workflows": (True, None),
        "get_workflow": (True, None),
        "export_workflow_graph": (False, True),
        "import_workflow_graph": (False, True),
        "preflight_workflow": (True, None),
        "run_workflow": (False, True),
    }

    # Not offered, and not callable either: it never reaches the transport.
    result = _call(
        lambda *a, **k: pytest.fail("reached the transport"),
        "import_workflow_graph",
        name="x.json",
        workflow={"1": {}},
    )
    assert result["isError"] is True
    assert "Unknown tool" in result["content"][0]["text"]

    # The read-only promise is made only by the read-only server.
    assert "Everything is read-only" in _instructions(False)
    write = _instructions(True)
    assert "Everything is read-only" not in write
    assert "validate" in write.lower() and "run_workflow" in write


def test_import_posts_json_through_http_fetch(monkeypatch):
    seen = []

    def urlopen(request, timeout):
        seen.append(request)
        return _Response()

    monkeypatch.setattr(
        mcp_server, "_build_opener", lambda context=None: SimpleNamespace(open=urlopen)
    )
    fetch = mcp_server.http_fetch("http://127.0.0.1:9537/", "example-token")
    graph = {"1": {"class_type": "KSampler", "inputs": {}}}
    assert (
        _write(fetch, "import_workflow_graph", name="x.json", workflow=graph)["isError"]
        is False
    )
    (request,) = seen
    assert request.get_method() == "POST"
    assert request.full_url == "http://127.0.0.1:9537/api/v1/comfyui/workflows/import"
    assert request.headers["Content-type"] == "application/json"
    assert request.headers["Authorization"] == "Bearer example-token"
    assert json.loads(request.data) == {
        "name": "x.json",
        "workflow": graph,
        "overwrite": False,
        "keep_both": False,
    }


def test_a_created_answer_is_success_and_a_refusal_carries_its_detail():
    def answering(status, body):
        return lambda path, params, method="GET", body_=None: (
            status,
            "application/json",
            body,
        )

    ok = _write(
        answering(201, b'{"name": "x.json"}'),
        "import_workflow_graph",
        name="x.json",
        workflow={"1": {}},
    )
    assert ok["isError"] is False, ok
    refused = _write(
        answering(403, b'{"detail": "Owner only"}'),
        "import_workflow_graph",
        name="x.json",
        workflow={"1": {}},
    )
    assert refused["isError"] is True
    assert "answered 403: Owner only" in refused["content"][0]["text"]


def test_a_workflow_key_cannot_reshape_the_request_path():
    requested = []

    def fetch(path, params, method="GET", body=None):
        requested.append(path)
        return 200, "application/json", b"{}"

    _write(fetch, "get_workflow", workflow_key="x/../../users/me/tokens")
    assert requested == ["/workflows/x%2F..%2F..%2Fusers%2Fme%2Ftokens"]
    # Positive control: a well-formed key is sent as it is.
    _write(fetch, "get_workflow", workflow_key="abc123")
    assert requested[-1] == "/workflows/abc123"
    # And a non-string never reaches the transport.
    assert _write(fetch, "get_workflow", workflow_key=["abc"])["isError"] is True
    assert len(requested) == 2


def test_the_graph_goes_through_a_file_in_both_directions(tmp_path):
    graph = {"3": {"class_type": "KSampler", "inputs": {"steps": 20}}}
    sent = []

    def fetch(path, params, method="GET", body=None):
        if method == "POST":
            sent.append(body)
            return 200, "application/json", b'{"matched": false}'
        answer = {"name": "Portrait", "workflow": graph, "source": "file"}
        return 200, "application/json", json.dumps(answer).encode()

    out = tmp_path / "nested" / "graph.json"
    result = _write(
        fetch, "export_workflow_graph", workflow_key="abc", out_path=str(out)
    )
    assert result["isError"] is False, result
    summary = json.loads(result["content"][0]["text"])
    assert summary["path"] == str(out)
    assert summary["nodes"] == 1
    # A path, not the graph: the agent reads the file only if it needs to.
    assert "KSampler" not in result["content"][0]["text"]
    assert json.loads(out.read_text()) == graph

    assert (
        _write(fetch, "import_workflow_graph", name="p.json", path=str(out))["isError"]
        is False
    )
    assert sent[0]["workflow"] == graph

    # The refusals say why, and none of them reaches the server.
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "list.json").write_text("[]")
    for path, reason in [
        (tmp_path / "absent.json", "Could not read"),
        (tmp_path / "bad.json", "not valid JSON"),
        (tmp_path / "list.json", "not a workflow object"),
    ]:
        refused = _write(fetch, "import_workflow_graph", name="p.json", path=str(path))
        assert refused["isError"] is True
        assert reason in refused["content"][0]["text"]
    both = _write(
        fetch, "import_workflow_graph", name="p.json", path=str(out), workflow=graph
    )
    assert both["isError"] is True
    assert len(sent) == 1

    # An answer without a graph is said plainly, not as a KeyError.
    def graphless(path, params, method="GET", body=None):
        return 200, "application/json", b'{"name": "x"}'

    empty = _write(graphless, "export_workflow_graph", workflow_key="abc")
    assert empty["isError"] is True
    assert "no workflow graph" in empty["content"][0]["text"]

    # A path that cannot be written is a tool error naming it, not a crash.
    blocked = tmp_path / "a-file"
    blocked.write_text("")
    refused = _write(
        fetch,
        "export_workflow_graph",
        workflow_key="abc",
        out_path=str(blocked / "graph.json"),
    )
    assert refused["isError"] is True
    assert "Could not write" in refused["content"][0]["text"]


def test_import_is_refused_to_a_read_token_and_reaches_the_route_for_the_owner(env):
    """Both directions against the live server, with nothing stored.

    The document is not a workflow, so the owner's call gets the route's own
    400 - which proves it passed the gate - and nothing is written into the
    workflow folder, which this test shares with the machine.
    """
    arguments = {"name": "mcp-probe.json", "workflow": {"not": "a workflow"}}
    owner = _write(env.owner, "import_workflow_graph", **arguments)
    assert owner["isError"] is True
    assert "answered 400" in owner["content"][0]["text"], owner

    for token in (env.reader, env.scoped):
        refused = _write(token, "import_workflow_graph", **arguments)
        assert refused["isError"] is True
        assert "answered 403" in refused["content"][0]["text"], refused


def test_the_start_up_probe_names_a_token_that_cannot_write(capsys):
    def read_only(path, params):
        assert path == "/recipes"
        return 403, "application/json", b'{"detail": "Owner only"}'

    assert mcp_server.warn_if_not_owner(read_only) is False
    assert "full-access token" in capsys.readouterr().err

    assert (
        mcp_server.warn_if_not_owner(
            lambda path, params: (200, "application/json", b"[]")
        )
        is True
    )
    assert mcp_server.build_parser().parse_args([]).allow_write is False
    assert mcp_server.build_parser().parse_args(["--allow-write"]).allow_write is True


def test_the_default_export_goes_to_the_cache_under_a_safe_name(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "_graph_dir", lambda: str(tmp_path / "graphs"))

    def fetch(path, params, method="GET", body=None):
        return 200, "application/json", b'{"workflow": {"1": {}}}'

    result = _write(fetch, "export_workflow_graph", workflow_key="ab/../c")
    path = json.loads(result["content"][0]["text"])["path"]
    # The key cannot climb out of the cache folder through the file name.
    assert path == str(tmp_path / "graphs" / "ab____c.json")
    assert json.loads(open(path).read()) == {"1": {}}


def test_main_probes_for_an_owner_token_only_with_allow_write(monkeypatch):
    probed = []
    monkeypatch.setenv("PIXLSTASH_TOKEN", "example-token")
    monkeypatch.setattr(mcp_server, "http_fetch", lambda *a: None)
    monkeypatch.setattr(mcp_server, "warn_if_unreachable", lambda fetch, url: True)
    monkeypatch.setattr(
        mcp_server, "warn_if_not_owner", lambda fetch: probed.append(True)
    )
    served = []
    monkeypatch.setattr(
        mcp_server, "serve", lambda fetch, allow_write=False: served.append(allow_write)
    )
    monkeypatch.setattr(
        mcp_server.sys, "stdin", SimpleNamespace(reconfigure=lambda **k: None)
    )

    assert mcp_server.main(["--url", "http://example.test"]) == 0
    assert (probed, served) == ([], [False])
    assert mcp_server.main(["--url", "http://example.test", "--allow-write"]) == 0
    assert (probed, served) == ([True], [False, True])
