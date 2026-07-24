"""Locality predicates fail closed on unparseable hosts (CSO review, finding 3).

`is_local_ip` / `is_loopback_ip` back the `LOCAL_OWNER_ONLY` / `LOOPBACK_OWNER_ONLY`
host-capability tiers. A genuinely malformed host (e.g. a bogus `X-Forwarded-For`
hop admitted by a mis-trusted proxy) must NOT be admitted as loopback/local — it
fails closed. The one exception is the in-process TestClient sentinel, kept so the
suite is not blocked.
"""

import pytest

from pixlstash.auth import is_local_ip, is_loopback_ip


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
    assert is_loopback_ip("192.168.1.5") is False
    assert is_local_ip("192.168.1.5") is True
    # public is neither
    assert is_loopback_ip("8.8.8.8") is False
    assert is_local_ip("8.8.8.8") is False
