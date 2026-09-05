"""Locality predicates fail closed on unparseable hosts (CSO review, finding 3).

`is_local_ip` / `is_loopback_ip` back the `LOCAL_OWNER_ONLY` / `LOOPBACK_OWNER_ONLY`
host-capability tiers. A genuinely malformed host (e.g. a bogus `X-Forwarded-For`
hop admitted by a mis-trusted proxy) must NOT be admitted as loopback/local - it
fails closed. The one exception is the in-process TestClient sentinel, kept so the
suite is not blocked.

The integration cases below (added per the finding-3 CSO sign-off) exercise the
real resolution path `get_real_client_ip` → predicate, and pin the security
property that an *untrusted* peer cannot spoof loopback/local via `X-Forwarded-For`.
"""

import pytest

from pixlstash.auth import get_real_client_ip, is_local_ip, is_loopback_ip
from tests.network_vectors import LAN_IPV4, PRIVATE_10_IPV4


@pytest.mark.parametrize("bogus", ["garbage", "", "not-an-ip", "999.999.999.999"])
def test_unparseable_host_fails_closed(bogus):
    assert is_loopback_ip(bogus) is False
    assert is_local_ip(bogus) is False


def test_testclient_sentinel_still_admitted():
    assert is_loopback_ip("testclient") is True
    assert is_local_ip("testclient") is True


def test_real_addresses_classified_correctly():
    # loopback
    assert is_loopback_ip("127.0.0.1") is True
    assert is_local_ip("127.0.0.1") is True
    # LAN (RFC1918) is local but NOT loopback
    assert is_loopback_ip(LAN_IPV4) is False
    assert is_local_ip(LAN_IPV4) is True
    # public is neither
    assert is_loopback_ip("8.8.8.8") is False
    assert is_local_ip("8.8.8.8") is False


def test_ipv6_addresses_classified_correctly():
    # ::1 loopback
    assert is_loopback_ip("::1") is True
    assert is_local_ip("::1") is True
    # Unique-local (ULA, fc00::/7 - the Tailscale ULA prefix lives here) is
    # private but not loopback
    assert is_loopback_ip("fd7a:115c:a1e0::1") is False
    assert is_local_ip("fd7a:115c:a1e0::1") is True
    # global IPv6 is neither
    assert is_loopback_ip("2001:4860:4860::8888") is False
    assert is_local_ip("2001:4860:4860::8888") is False


# ── Integration: get_real_client_ip → predicate (finding-3 sign-off) ──────────


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    """Minimal stand-in for starlette Request: `.client.host` + `.headers.get`."""

    def __init__(self, host, xff=None):
        self.client = _FakeClient(host) if host is not None else None
        self.headers = {"X-Forwarded-For": xff} if xff is not None else {}


def test_xff_from_trusted_proxy_resolves_to_real_client():
    # A LAN client behind a trusted proxy resolves to the client and is judged
    # local (not loopback) - the legitimate reverse-proxy path still works.
    req = _FakeRequest(host=PRIVATE_10_IPV4, xff=LAN_IPV4)
    ip = get_real_client_ip(req, trusted_proxies=[PRIVATE_10_IPV4])
    assert ip == LAN_IPV4
    assert is_local_ip(ip) is True
    assert is_loopback_ip(ip) is False


def test_xff_from_untrusted_peer_cannot_spoof_loopback():
    # An untrusted direct peer setting X-Forwarded-For: 127.0.0.1 must NOT be
    # admitted as loopback - the direct (untrusted) peer stands.
    req = _FakeRequest(host="8.8.8.8", xff="127.0.0.1")
    ip = get_real_client_ip(req, trusted_proxies=[PRIVATE_10_IPV4])
    assert ip == "8.8.8.8"
    assert is_loopback_ip(ip) is False
    assert is_local_ip(ip) is False


def test_malformed_direct_host_denied_end_to_end():
    req = _FakeRequest(host="garbage")
    ip = get_real_client_ip(req, trusted_proxies=[])
    assert ip == "garbage"
    assert is_loopback_ip(ip) is False
    assert is_local_ip(ip) is False


def test_missing_client_defaults_to_loopback():
    # No socket peer (e.g. some ASGI edge cases) defaults to 127.0.0.1 - a
    # parseable loopback, so the desktop/loopback paths are not over-blocked.
    req = _FakeRequest(host=None)
    ip = get_real_client_ip(req, trusted_proxies=[])
    assert ip == "127.0.0.1"
    assert is_loopback_ip(ip) is True
    assert is_local_ip(ip) is True
