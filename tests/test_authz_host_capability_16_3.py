"""§16.3 host-capability access design (principal ruling 2026-07-21).

Covers the three-lens (CSO/Principal/CEO) decided design landing before Step 5:

* ``LOCAL_OWNER_ONLY`` (13 filesystem/folder routes) — loopback / RFC1918 LAN /
  **Tailscale CGNAT ``100.64.0.0/10``** all count as local; a genuinely remote
  owner is 403'd with a message NAMING ``allow_remote_host_ops`` unless that
  dedicated flag is set, which then admits the remote owner.
* ``LOOPBACK_OWNER_ONLY`` (3 host-shell red-line routes) — strictly loopback; the
  ``allow_remote_host_ops`` flag can NEVER loosen them (RFC1918 + flag-on is still
  403).
* Reverse-proxy: with ``trusted_proxies`` set the owner's real (spoofed) client IP
  drives the gate — a public real client is 403'd (flag off), a LAN real client
  is allowed.

Both-directional per CLAUDE.md / §16.1: every deny is paired with an in-scope
allow so over-blocking is caught as its own regression. The shipped constant
``AUTHZ_GATE_ENFORCING`` stays ``False``; enforcement is proven behind
``enforcing=True`` exactly as the Step-3 suite does.
"""

import contextlib
import json
import os
import tempfile

from pixlstash.auth import (
    is_local_ip,
    is_local_or_tailscale_ip,
    is_loopback_ip,
    is_tailscale_ip,
)
from pixlstash.authz.policy import (
    JUSTIFICATION_REQUIRED,
    AccessPolicy,
    RoutePolicy,
    validate_policy_declarations,
)
from pixlstash.authz.registry import ROUTE_POLICIES

API = "/api/v1"

# The 4 host-shell red-line routes on the stricter loopback-only tier. All four
# spawn a host GUI process (os.startfile / open / xdg-open); server-config/open was
# a byte-identical sibling that shipped owner_only with no locality check (CSO
# Condition 1, 2026-07-21) and is reclassified here.
_LOOPBACK_ROUTE_KEYS = {
    ("POST", "/api/v1/server/restart"),
    ("POST", "/api/v1/reference-folders/{folder_id}/open"),
    ("POST", "/api/v1/pictures/{id}/open-location"),
    ("POST", "/api/v1/server-config/open"),
}


# ===========================================================================
# Locality-predicate unit tests (no server) — the Tailscale fix is scoped
# ===========================================================================


def test_is_local_ip_not_widened_to_tailscale():
    """The SHARED ``is_local_ip`` must stay loopback|RFC1918 ONLY: widening it
    would silently loosen its unrelated callers (require_local_for_write, the
    middleware ALL-token block, the HTTPS-skip carve-out). Tailscale CGNAT is NOT
    private, so it must remain False on the shared predicate."""
    assert is_local_ip("127.0.0.1") is True
    assert is_local_ip("10.0.0.5") is True
    assert is_local_ip("192.168.1.9") is True
    assert is_local_ip("100.64.0.5") is False  # Tailscale CGNAT — NOT widened here
    assert is_local_ip("8.8.8.8") is False


def test_is_tailscale_ip_covers_cgnat_and_ula():
    """Tailscale addresses out of RFC 6598 ``100.64.0.0/10`` (v4) and the ULA
    ``fd7a:115c:a1e0::/48`` (v6); nothing outside those ranges."""
    assert is_tailscale_ip("100.64.0.1") is True
    assert is_tailscale_ip("100.100.100.100") is True
    assert is_tailscale_ip("100.127.255.255") is True
    assert is_tailscale_ip("fd7a:115c:a1e0::1") is True
    # Just outside the /10 boundaries.
    assert is_tailscale_ip("100.63.255.255") is False
    assert is_tailscale_ip("100.128.0.1") is False
    assert is_tailscale_ip("8.8.8.8") is False
    assert is_tailscale_ip("10.0.0.1") is False


def test_is_local_or_tailscale_ip_is_the_host_ops_predicate():
    """The scoped host-ops predicate accepts loopback, RFC1918, AND Tailscale —
    the union the §16.3 gate uses (and only the gate)."""
    for ip in (
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.9",
        "100.64.0.5",
        "fd7a:115c:a1e0::1",
    ):
        assert is_local_or_tailscale_ip(ip) is True, ip
    assert is_local_or_tailscale_ip("8.8.8.8") is False


def test_is_loopback_ip_rejects_lan_and_tailscale():
    """The red-line predicate: loopback only. RFC1918 and Tailscale are NOT
    loopback — the flag-immune tier can never be reached from them."""
    assert is_loopback_ip("127.0.0.1") is True
    assert is_loopback_ip("::1") is True
    assert is_loopback_ip("10.0.0.5") is False
    assert is_loopback_ip("192.168.1.9") is False
    assert is_loopback_ip("100.64.0.5") is False
    assert is_loopback_ip("8.8.8.8") is False


# ===========================================================================
# Policy / registry structure — the closed-enum extension and the 3+13 split
# ===========================================================================


def test_loopback_owner_only_is_justification_required():
    """The new host-shell tier grants host authority — it must carry a written
    justification, exactly like PUBLIC / LOCAL_OWNER_ONLY."""
    assert AccessPolicy.LOOPBACK_OWNER_ONLY in JUSTIFICATION_REQUIRED

    missing = validate_policy_declarations(
        {("POST", "/x"): RoutePolicy(AccessPolicy.LOOPBACK_OWNER_ONLY)}
    )
    assert any("justification" in problem for problem in missing), missing
    ok = validate_policy_declarations(
        {
            ("POST", "/x"): RoutePolicy(
                AccessPolicy.LOOPBACK_OWNER_ONLY, justification="host shell red line"
            )
        }
    )
    assert ok == []


def test_host_capability_tier_split_is_13_local_4_loopback():
    """The 4 host-shell GUI-spawn routes are LOOPBACK_OWNER_ONLY; the 13
    filesystem/folder routes stay LOCAL_OWNER_ONLY. Corrected arithmetic after CSO
    Condition 1 folded server-config/open into the loopback tier: 17 host-capability
    routes carry a locality tier = 13 local + 4 loopback (was 16 = 13 + 3).
    Arithmetic, not judgement."""
    loopback = {
        key
        for key, rp in ROUTE_POLICIES.items()
        if rp.policy is AccessPolicy.LOOPBACK_OWNER_ONLY
    }
    local = {
        key
        for key, rp in ROUTE_POLICIES.items()
        if rp.policy is AccessPolicy.LOCAL_OWNER_ONLY
    }
    assert loopback == _LOOPBACK_ROUTE_KEYS, loopback
    assert len(loopback) == 4, sorted(loopback)
    assert len(local) == 13, sorted(local)


# ===========================================================================
# Integration: one real server, owner cookie, spoofable client IP
# ===========================================================================


@contextlib.contextmanager
def _owner_env():
    """Real Server + owner cookie login. ``trusted_proxies=["testclient"]`` lets a
    test spoof the real client IP via ``X-Forwarded-For``; without the header the
    in-process ``testclient`` peer is treated as loopback."""
    from starlette.testclient import TestClient

    tmp = tempfile.TemporaryDirectory()
    cfg = os.path.join(tmp.name, "server-config.json")
    with open(cfg, "w") as fh:
        json.dump({"port": 8000, "trusted_proxies": ["testclient"]}, fh)
    server = Server(cfg)
    server.__enter__()
    try:
        client = TestClient(server.api, raise_server_exceptions=True)
        r = client.post(
            f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
        )
        assert r.status_code == 200, r.text
        yield {"server": server, "owner": client, "tmp": tmp}
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


@contextlib.contextmanager
def _enforcing(server):
    prev = server.authz._enforcing
    server.authz._enforcing = True
    try:
        yield
    finally:
        server.authz._enforcing = prev


@contextlib.contextmanager
def _remote_host_ops(server, enabled):
    """Toggle the live ``allow_remote_host_ops`` flag (the property reads the
    config dict live, so mutating it is enough)."""
    cfg = server.auth._server_config
    prev = cfg.get("allow_remote_host_ops")
    cfg["allow_remote_host_ops"] = enabled
    try:
        yield
    finally:
        if prev is None:
            cfg.pop("allow_remote_host_ops", None)
        else:
            cfg["allow_remote_host_ops"] = prev


def _xff(ip):
    return {"X-Forwarded-For": ip}


def _is_locality_403(resp):
    return resp.status_code == 403 and "restricted to local" in resp.text


def _is_loopback_403(resp):
    return resp.status_code == 403 and "restricted to loopback" in resp.text


_BROWSE = f"{API}/filesystem/browse"  # a LOCAL_OWNER_ONLY route
_OPEN_LOCATION = f"{API}/pictures/999999/open-location"  # a LOOPBACK_OWNER_ONLY route


# ---- LOCAL_OWNER_ONLY (13) ------------------------------------------------


def test_local_owner_only_allows_loopback_lan_and_tailscale():
    """Loopback, RFC1918 LAN, and Tailscale CGNAT are all admitted (flag off) —
    the Tailscale case is the false-deny fix. Asserted as 'not locality-403'."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server):
            # Loopback (in-process peer, no XFF).
            assert not _is_locality_403(owner.get(_BROWSE)), "loopback must pass"
            for ip in ("10.0.0.5", "192.168.1.9", "100.64.0.5"):
                r = owner.get(_BROWSE, headers=_xff(ip))
                assert not _is_locality_403(r), (
                    f"{ip} must count as local for host-ops; got {r.status_code}: {r.text}"
                )


def test_local_owner_only_remote_public_403s_naming_the_flag():
    """A genuinely remote owner (public IP) with the flag OFF is 403'd, and the
    message names ``allow_remote_host_ops`` so the operator knows the exact setting
    that enables it."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, False):
            r = owner.get(_BROWSE, headers=_xff("8.8.8.8"))
            assert r.status_code == 403, r.text
            assert "allow_remote_host_ops" in r.text, (
                f"the deny must name allow_remote_host_ops; got: {r.text}"
            )


def test_local_owner_only_remote_public_allowed_with_flag_on():
    """The dedicated flag admits a remote authenticated owner on the 13
    LOCAL_OWNER_ONLY routes."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            r = owner.get(_BROWSE, headers=_xff("8.8.8.8"))
            assert not _is_locality_403(r), (
                f"allow_remote_host_ops=true must admit a remote owner; "
                f"got {r.status_code}: {r.text}"
            )


# ---- LOOPBACK_OWNER_ONLY (3) — the flag-immune red line --------------------


def test_loopback_owner_only_allows_loopback():
    """A loopback owner reaches the red-line route (the gate passes; the handler
    then 404s on the bogus id — never a locality/loopback 403)."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server):
            r = owner.post(_OPEN_LOCATION)
            assert not _is_loopback_403(r), (
                f"loopback owner must reach the red-line route; got {r.status_code}: {r.text}"
            )
            assert r.status_code == 404, (
                f"expected the handler's picture-not-found 404 past the gate, got {r.status_code}"
            )


def test_loopback_owner_only_rfc1918_403_even_with_flag_on():
    """THE CARVE-OUT: an RFC1918 LAN owner is 403'd on the red-line route EVEN with
    ``allow_remote_host_ops=True`` — the flag can never loosen this tier."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            r = owner.post(_OPEN_LOCATION, headers=_xff("192.168.1.9"))
            assert _is_loopback_403(r), (
                f"RFC1918 must be 403'd on a LOOPBACK_OWNER_ONLY route even with "
                f"allow_remote_host_ops=true; got {r.status_code}: {r.text}"
            )
            # And a Tailscale client is equally excluded from the red line.
            r = owner.post(_OPEN_LOCATION, headers=_xff("100.64.0.5"))
            assert _is_loopback_403(r), (
                f"Tailscale must be 403'd on the red line even with the flag on; "
                f"got {r.status_code}: {r.text}"
            )


def test_loopback_owner_only_public_403():
    """A public remote owner is 403'd on the red-line route (flag off)."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, False):
            r = owner.post(_OPEN_LOCATION, headers=_xff("8.8.8.8"))
            assert _is_loopback_403(r), (
                f"public owner must be 403'd on the red line; got {r.status_code}: {r.text}"
            )


# ---- server-config/open — the CSO Condition-1 sibling hole -----------------

_CONFIG_OPEN = f"{API}/server-config/open"


def test_server_config_open_loopback_owner_only_carve_out():
    """CSO Condition 1: ``POST /server-config/open`` spawns the host file browser
    via the byte-identical ``_open_in_os`` mechanism as the other 3 red-line
    routes, but shipped ``owner_only`` with NO locality check. It is reclassified
    LOOPBACK_OWNER_ONLY. Same carve-out proof: loopback allowed; RFC1918 /
    Tailscale / public 403 EVEN with ``allow_remote_host_ops=True``.

    The loopback-allow path reaches the handler, which would spawn a real file
    browser — patch the config module's ``subprocess.run`` so the test never
    launches a GUI (the gate, which runs before the handler, is what we assert)."""
    from unittest import mock

    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            # NEGATIVE carve-out: none of these may pass even with the flag ON.
            for ip in ("192.168.1.9", "100.64.0.5", "8.8.8.8"):
                r = owner.post(_CONFIG_OPEN, headers=_xff(ip))
                assert _is_loopback_403(r), (
                    f"{ip} must be 403'd on server-config/open even with "
                    f"allow_remote_host_ops=true; got {r.status_code}: {r.text}"
                )
            # POSITIVE: a loopback owner passes the gate (handler spawn stubbed).
            with mock.patch("pixlstash.routes.config.subprocess.run"):
                r = owner.post(_CONFIG_OPEN)
            assert not _is_loopback_403(r), (
                f"loopback owner must reach server-config/open; "
                f"got {r.status_code}: {r.text}"
            )
            assert r.status_code == 200, (
                f"expected the handler to run past the gate for a loopback owner, "
                f"got {r.status_code}: {r.text}"
            )


# ---- Reverse-proxy: trusted_proxies surfaces the real client IP ------------


def test_reverse_proxy_real_public_client_403_flag_off():
    """With ``trusted_proxies`` set, the owner's REAL client IP (from XFF) drives
    the gate: a public real client is 403'd on a LOCAL_OWNER_ONLY route (flag
    off). This is the 'set correctly surfaces the real public IP' direction."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, False):
            r = owner.get(_BROWSE, headers=_xff("1.1.1.1"))
            assert r.status_code == 403 and "allow_remote_host_ops" in r.text, r.text


def test_reverse_proxy_real_lan_client_allowed():
    """The other direction (over-blocking is its own regression): a LAN real
    client behind the trusted proxy is admitted."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server):
            r = owner.get(_BROWSE, headers=_xff("192.168.1.50"))
            assert not _is_locality_403(r), (
                f"a LAN real client must be admitted; got {r.status_code}: {r.text}"
            )


# Import Server after the pure-unit tests are defined so predicate/policy tests
# do not depend on the heavier server import path.
from pixlstash.server import Server  # noqa: E402
