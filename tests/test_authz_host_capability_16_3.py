"""§16.3 host-capability access design.

Covers:

* ``LOCAL_OWNER_ONLY`` (the filesystem/folder routes, the library switch, and the
  host-path disclosures; the tier-split test's own name carries the count, and
  it is the only place that does - prose here goes stale, an assertion cannot)
  - loopback / RFC1918 LAN /
  **Tailscale CGNAT ``100.64.0.0/10``** all count as local; a genuinely remote
  owner is 403'd with a message NAMING ``allow_remote_host_ops`` unless that
  dedicated flag is set, which then admits the remote owner.
* ``LOOPBACK_OWNER_ONLY`` (the host-shell red-line routes) - strictly loopback; the
  ``allow_remote_host_ops`` flag can NEVER loosen them (RFC1918 + flag-on is still
  403).
* Reverse-proxy: with ``trusted_proxies`` set the owner's real (spoofed) client IP
  drives the gate - a public real client is 403'd (flag off), a LAN real client
  is allowed.

Both-directional per CLAUDE.md / §16.1: every deny is paired with an in-scope
allow so over-blocking is caught as its own regression. These tests set
``enforcing`` explicitly rather than relying on the shipped default - one test
deliberately turns it **off**, to prove the middleware's
``READ_BLOCKED_GET_PATHS`` still refuses a share token if the documented
one-line rollback is ever taken.
"""

import contextlib
import json
import os
import subprocess
import tempfile

import pytest
from starlette.testclient import TestClient

from pixlstash.auth import (
    READ_BLOCKED_GET_PATHS,
    READ_BLOCKED_GET_PREFIXES,
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
from pixlstash.db_models import Picture, ReferenceFolder
from pixlstash.route_inventory import api_endpoint_set
from pixlstash.services import node_pack_install_service
from tests.authz_guard import no_spa_fallback  # noqa: F401
from tests.network_vectors import LAN_IPV4, PRIVATE_10_IPV4

API = "/api/v1"

# The SPA catch-all answers unmatched GETs with 200, so a wrong URL can make a
# positive assertion vacuous. See tests/authz_guard.py. The deliberate
# absent-route probe below asserts 404/405 and is unaffected (non-2xx only).
pytestmark = pytest.mark.usefixtures("no_spa_fallback")

# The 7 red-line routes on the stricter loopback-only tier. FIVE of them spawn a
# host GUI process (os.startfile / open / xdg-open); server/restart re-execs the
# process and spawns nothing, which the comment here counted as a GUI spawn from
# 2026-07-21 until #933 came to add a real one. server-config/open was a
# byte-identical sibling that shipped owner_only with no locality check (CSO
# Condition 1) and is reclassified here; the model shelf's `Open in file manager`
# (#933) is the fourth spawn and the first to reach it through the shared
# pixlstash/utils/host_open.py, which reads the opener's exit status where the
# three inline copies discard it. Export-to-folder (#291) is the fifth spawn and
# the second to reach it through host_open.py - it writes the exported pictures
# onto the host disk first and then opens the destination, so the tier follows
# the same write-then-open shape as the shelf's `Open in file manager` follows a
# move, not a bare disclosure.
#
# The seventh is the e2e test hook. It spawns nothing, but it synthesises arbitrary
# WebSocket grid events broadcast to every connected client - a capability over
# OTHER clients' state rather than over the caller's own data - and it is mounted
# only by the e2e backend, which binds 127.0.0.1 and is driven from the same
# host. Loopback therefore costs nothing and removes the dependence on
# enable_test_hooks staying off in production.
_LOOPBACK_ROUTE_KEYS = {
    ("POST", "/api/v1/server/restart"),
    ("POST", "/api/v1/reference-folders/{folder_id}/open"),
    ("POST", "/api/v1/pictures/{id}/open-location"),
    ("POST", "/api/v1/models/{model_id}/open-location"),
    ("POST", "/api/v1/model-folders/{folder_id}/open"),
    ("POST", "/api/v1/server-config/open"),
    ("POST", "/api/v1/pictures/export/folder"),
    ("POST", "/api/v1/pictures/share-copies"),
    ("GET", "/api/v1/open-with"),
    ("POST", "/api/v1/open-with"),
    ("POST", "/api/v1/library/folders/open"),
    ("POST", "/api/v1/test-hooks/ws-event"),
    ("POST", "/api/v1/test-hooks/reference-folders/{folder_id}/rescan"),
    ("POST", "/api/v1/test-hooks/file-pictures"),
    ("POST", "/api/v1/comfyui/pack/install"),
    ("POST", "/api/v1/comfyui/node-packs/{pack_id}/install"),
    ("POST", "/api/v1/training/ai-toolkit/check"),
    ("PUT", "/api/v1/training/ai-toolkit/config"),
}


# ===========================================================================
# Locality-predicate unit tests (no server) - the Tailscale fix is scoped
# ===========================================================================


def test_is_local_ip_not_widened_to_tailscale():
    """The SHARED ``is_local_ip`` must stay loopback|RFC1918 ONLY: widening it
    would silently loosen its unrelated callers (require_local_for_write, the
    middleware ALL-token block, the HTTPS-skip carve-out). Tailscale CGNAT is NOT
    private, so it must remain False on the shared predicate."""
    assert is_local_ip("127.0.0.1") is True
    assert is_local_ip(PRIVATE_10_IPV4) is True
    assert is_local_ip(LAN_IPV4) is True
    assert is_local_ip("100.64.0.5") is False  # Tailscale CGNAT - NOT widened here
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
    assert is_tailscale_ip(PRIVATE_10_IPV4) is False


def test_is_local_or_tailscale_ip_is_the_host_ops_predicate():
    """The scoped host-ops predicate accepts loopback, RFC1918, AND Tailscale -
    the union the §16.3 gate uses (and only the gate)."""
    for ip in (
        "127.0.0.1",
        PRIVATE_10_IPV4,
        LAN_IPV4,
        "100.64.0.5",
        "fd7a:115c:a1e0::1",
    ):
        assert is_local_or_tailscale_ip(ip) is True, ip
    assert is_local_or_tailscale_ip("8.8.8.8") is False


def test_is_loopback_ip_rejects_lan_and_tailscale():
    """The red-line predicate: loopback only. RFC1918 and Tailscale are NOT
    loopback - the flag-immune tier can never be reached from them."""
    assert is_loopback_ip("127.0.0.1") is True
    assert is_loopback_ip("::1") is True
    assert is_loopback_ip(PRIVATE_10_IPV4) is False
    assert is_loopback_ip(LAN_IPV4) is False
    assert is_loopback_ip("100.64.0.5") is False
    assert is_loopback_ip("8.8.8.8") is False


# ===========================================================================
# Policy / registry structure - the closed-enum extension and the 3+13 split
# ===========================================================================


def test_loopback_owner_only_is_justification_required():
    """The new host-shell tier grants host authority - it must carry a written
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


def test_host_capability_tier_split_is_49_local_7_loopback():
    """The loopback tier is the host-shell red-line set (host-GUI spawns, the
    process restarts, the e2e test hooks and the code installs); the
    filesystem/folder routes stay LOCAL_OWNER_ONLY. The counts asserted below
    are arithmetic, not judgement: a change to either arrives with its reason.
    """
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
    assert len(loopback) == 18, sorted(loopback)
    assert len(local) == 75, sorted(local)


# ===========================================================================
# Integration: one real server, owner cookie, spoofable client IP
# ===========================================================================


@contextlib.contextmanager
def _owner_env():
    """Real Server + owner cookie login. ``trusted_proxies=["testclient"]`` lets a
    test spoof the real client IP via ``X-Forwarded-For``; without the header the
    in-process ``testclient`` peer is treated as loopback."""

    tmp = tempfile.TemporaryDirectory()
    cfg = os.path.join(tmp.name, "server-config.json")
    with open(cfg, "w") as fh:
        json.dump({"port": 8000, "trusted_proxies": ["testclient"]}, fh)
    server = Server(cfg)
    server.__enter__()
    try:
        client = TestClient(server.api, raise_server_exceptions=True)
        r = client.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
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
# LOCAL_OWNER_ONLY for disclosure alone.
_TAGGER_DIAGNOSTICS = f"{API}/taggers/plugin-diagnostics"


# ---- LOCAL_OWNER_ONLY (27) ------------------------------------------------


def test_local_owner_only_allows_loopback_lan_and_tailscale():
    """Loopback, RFC1918 LAN, and Tailscale CGNAT are all admitted (flag off) -
    the Tailscale case is the false-deny fix. Asserted as 'not locality-403'."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server):
            # Loopback (in-process peer, no XFF).
            assert not _is_locality_403(owner.get(_BROWSE)), "loopback must pass"
            for ip in (PRIVATE_10_IPV4, LAN_IPV4, "100.64.0.5"):
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
    """The dedicated flag admits a remote authenticated owner on the
    LOCAL_OWNER_ONLY tier."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            r = owner.get(_BROWSE, headers=_xff("8.8.8.8"))
            assert not _is_locality_403(r), (
                f"allow_remote_host_ops=true must admit a remote owner; "
                f"got {r.status_code}: {r.text}"
            )


def test_the_layout_migration_routes_answer_a_local_owner_end_to_end():
    """The in-scope 200 half of the layout-migration declaration, over a real
    server.

    Reuses this module's own owner environment rather than standing up another:
    what is being proved is the route contract - the preview moves nothing, the
    run reports a cursor and a `batch_id`, and an id this route did not mint is
    refused - and none of that needs pictures on a disk. The moving itself is
    `tests/test_library_layout.py`'s, against a real tree.
    """
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server):
            # No layout: the preview is honest about it rather than 404ing.
            r = owner.get(f"{API}/server-config/layout/migration")
            assert r.status_code == 200, r.text
            assert r.json()["layout"] is None
            assert r.json()["picture_count"] == 0

            assert (
                owner.patch(
                    f"{API}/server-config/layout",
                    json={"layout": "project/person,set"},
                ).status_code
                == 200
            )

            r = owner.get(f"{API}/server-config/layout/migration")
            assert r.status_code == 200, r.text
            preview = r.json()
            assert preview["layout"] == "project/person,set"
            # An empty library has nothing to move, and every cost is zero
            # rather than absent - the screen reads these unconditionally.
            assert preview["picture_count"] == 0
            assert preview["collision_count"] == 0
            assert preview["cross_volume_count"] == 0
            assert preview["skipped_counts"] == {}

            r = owner.post(
                f"{API}/server-config/layout/migration", json={"after_id": 0}
            )
            assert r.status_code == 200, r.text
            run = r.json()
            assert run["done"] is True
            assert run["moved_count"] == 0
            # Minted server-side and echoed back, so the next pass joins the
            # same undo unit.
            assert run["batch_id"].startswith("srv-layout-migration-")
            assert (
                owner.post(
                    f"{API}/server-config/layout/migration",
                    json={
                        "after_id": run["next_after_id"],
                        "batch_id": run["batch_id"],
                    },
                ).status_code
                == 200
            )

            # And an id the route did not mint is refused: batch_id decides
            # what one undo reverses.
            r = owner.post(
                f"{API}/server-config/layout/migration",
                json={"after_id": 0, "batch_id": "cli-someone-elses-gesture"},
            )
            assert r.status_code == 400, r.text


def test_the_layout_migration_is_refused_to_a_remote_owner():
    """The out-of-scope half. The POST renames every picture in the library, so
    a genuinely remote owner must not reach it with the flag off - and the GET
    must not hand them the count and the sample paths either."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, False):
            for call in (
                lambda: owner.get(
                    f"{API}/server-config/layout/migration", headers=_xff("8.8.8.8")
                ),
                lambda: owner.post(
                    f"{API}/server-config/layout/migration",
                    json={"after_id": 0},
                    headers=_xff("8.8.8.8"),
                ),
            ):
                r = call()
                assert r.status_code == 403, r.text
                assert "allow_remote_host_ops" in r.text, r.text
        # And the tier is a locality one, not a blanket refusal: the same owner
        # on the LAN is admitted, so this is not passing because the route is
        # broken.
        with _enforcing(server):
            assert not _is_locality_403(
                owner.get(
                    f"{API}/server-config/layout/migration", headers=_xff(LAN_IPV4)
                )
            )


def test_tagger_diagnostics_is_local_and_the_any_token_routes_carry_no_host_path():
    """Both directions on the tagger diagnostics split, plus the reason it exists.

    The plugin folder and the load-failure messages are host-path disclosures,
    so a remote owner is refused them (flag off) and a share-scoped token is
    refused them from anywhere; a loopback owner still gets them, because
    over-blocking is its own regression.

    The negatives assert on the **path string**, not on a field name:
    grepping the serialised body for the owner's home directory is the
    assertion that cannot be satisfied by moving the leak to another key - or
    to another directory.

    ``GET /taggers`` is owner-only and gets the stronger check of the two:
    a share token may not read it at all.
    """
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, False):
            allowed = owner.get(_TAGGER_DIAGNOSTICS)
            assert allowed.status_code == 200, allowed.text
            plugin_dir = allowed.json()["plugin_dirs"]["user"]
            assert plugin_dir, "the folder must be named"

            refused = owner.get(_TAGGER_DIAGNOSTICS, headers=_xff("8.8.8.8"))
            assert _is_locality_403(refused), (
                f"a remote owner must not read the host path; "
                f"got {refused.status_code}: {refused.text}"
            )

            # The actual threat model: a share link, from anywhere at all.
            minted = owner.post(
                f"{API}/users/me/token",
                json={
                    "description": "set share",
                    "scope": "READ",
                    "resource_type": "picture_set",
                    "resource_id": 1,
                },
            )
            assert minted.status_code == 200, minted.text
            # A cookie-less client: the middleware prefers the owner's session
            # cookie over a Bearer token, which would never exercise the scope.

            anon = TestClient(server.api, raise_server_exceptions=True)
            share = {"Authorization": f"Bearer {minted.json()['token']}"}
            scoped = anon.get(_TAGGER_DIAGNOSTICS, headers=share)
            assert scoped.status_code == 403, (
                f"a resource-scoped token must never reach it; got {scoped.status_code}"
            )

            # GET /taggers itself is no longer reachable by that token at all:
            # it carries the caller's own tagger_settings, so a plugin with a
            # "string" parameter puts whatever the owner typed into it - a
            # model path as easily as a prompt - in front of a share link.
            listing = anon.get(f"{API}/taggers", headers=share)
            assert listing.status_code == 403, (
                f"the plugin list is owner-only; got {listing.status_code}"
            )

            # ...and no **parameterless GET declared any_token or public**
            # discloses a path under the owner's home directory, under any key.
            #
            # Read that scope literally. It is NOT "no route a share token can
            # reach": the picture rows themselves carry host paths in
            # `import_source_folder` and `tags_file`, which a picture-scoped
            # token reads through `GET /pictures/{id}/{field}` today. That is a
            # pre-existing disclosure of a different class - per-object columns
            # behind a membership check, not a global one behind none - and it
            # is recorded as a follow-up rather than fixed here. Claiming the
            # wider invariant in this comment would have been the third version
            # of exactly the mistake below.
            #
            # Two deliberate choices, both learned from this change's own
            # review rounds. The home directory rather than the plugin folder:
            # a check anchored on the folder passes any leak one directory
            # over, which is how the first version of this test passed while
            # `load_error` still carried the path. And the route list is
            # *derived from the registry*, not written down: the first two
            # rounds each missed a sibling because they fixed the field they
            # knew about instead of enumerating the disclosure, and a hand-kept
            # list here would reproduce exactly that.
            home = os.path.expanduser("~")
            # JSON escapes backslashes, so the raw form never substring-matches
            # a Windows path in a serialised body.
            home_forms = (home, home.replace("\\", "\\\\"))
            #
            # Two filters on the derivation, both about what the probe can
            # honestly reach rather than about which routes matter:
            #
            # * ``/api/v1`` only. ``tests/conftest.py`` wraps ``TestClient.get``
            #   and prefixes ``/api/v1`` to any path that lacks it, bar three
            #   root paths. So a probe of the registry's ``/docs``, ``/scalar``
            #   or ``/openapi.json`` is sent as ``/api/v1/docs``, where nothing
            #   is mounted and the SPA catch-all answers 200 - a vacuous
            #   assertion, and `no_spa_fallback` says so. It fails on CI (which
            #   builds the frontend) and passed locally (which does not), which
            #   is exactly the shape of bug that guard exists for.
            # * Mounted on this app. The registry also declares
            #   conditionally-mounted routes, and an unmounted one lands on the
            #   same catch-all.
            mounted = {
                path for method, path in api_endpoint_set(server.api) if method == "GET"
            }
            reachable = sorted(
                path
                for (method, path), rp in ROUTE_POLICIES.items()
                if method == "GET"
                and rp.policy in (AccessPolicy.ANY_TOKEN, AccessPolicy.PUBLIC)
                and "{" not in path
                and path.startswith(f"{API}/")
                and path in mounted
            )
            inspected = 0
            for route in reachable:
                body = anon.get(route, headers=share)
                if body.status_code != 200:
                    continue  # refused outright is a stronger answer than clean
                inspected += 1
                for form in home_forms:
                    assert form not in body.text, (
                        f"{route} is reachable by a share token and disclosed "
                        f"a host path"
                    )
            # Not a count of *declarations* - a count of bodies actually read.
            # Every route 403ing would otherwise make the loop silently green.
            assert inspected >= 8, f"only {inspected} of {len(reachable)} answered 200"


def test_owner_only_path_disclosing_gets_survive_the_documented_gate_rollback():
    """The second belt: ``READ_BLOCKED_GET_PATHS``, with the gate switched off.

    ``AUTHZ_GATE_ENFORCING = False`` is a documented one-line rollback, and ``GET
    /filesystem/browse`` is on both belts precisely so taking it does not re-open a
    filesystem disclosure. The two tagger routes are GETs on the same tier, so they need
    the same pair. The owner (no token, session cookie) is unaffected either way.

    ``GET /insights`` and ``GET /moves/pending`` are here for the same reason: both are
    plain ``OWNER_ONLY`` rather than locality-tier, and both serve absolute host paths
    (the folder behind every insight finding; ``old_path``/``new_path`` for every
    externally-moved file). The owner assertions below are not decoration - the READ
    middleware runs ahead of routing, so a renamed or dead path 403s a share token
    exactly like a real refusal, and the owner's 200 is what proves the negative was
    measured against a live route.
    """
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        minted = owner.post(
            f"{API}/users/me/token",
            json={
                "description": "set share",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": 1,
            },
        )
        assert minted.status_code == 200, minted.text

        anon = TestClient(server.api, raise_server_exceptions=True)
        share = {"Authorization": f"Bearer {minted.json()['token']}"}
        # The rollback state, set explicitly: the gate ships enforcing.
        previously_enforcing = server.authz._enforcing
        server.authz._enforcing = False
        try:
            assert anon.get(f"{API}/pictures", headers=share).status_code == 200, (
                "the share token is dead; the refusals below would prove nothing"
            )
            routes = (
                _TAGGER_DIAGNOSTICS,
                f"{API}/taggers",
                f"{API}/insights",
                f"{API}/moves/pending",
            )
            for route in routes:
                refused = anon.get(route, headers=share)
                assert refused.status_code == 403, (
                    f"{route} must stay closed to a READ token with the gate "
                    f"off; got {refused.status_code}"
                )
            # The owner still reaches every one, from the same middleware -
            # which is also the proof that each path above is a live route and
            # not a 403 from the READ middleware refusing a name nothing serves.
            for route in routes:
                allowed = owner.get(route)
                assert allowed.status_code == 200, (
                    f"{route} must still answer the owner; got "
                    f"{allowed.status_code} - {allowed.text}"
                )
        finally:
            server.authz._enforcing = previously_enforcing


def test_every_untemplated_owner_class_get_is_on_the_read_blocked_belt():
    """Derive the belt's membership instead of writing it down.

    ``READ_BLOCKED_GET_PATHS`` matches literal paths, so an owner-class GET is
    only protected under the documented ``AUTHZ_GATE_ENFORCING = False``
    rollback if its own path is in that frozenset. The rollback test beside this
    one names two paths; this one asserts the *rule*, so the next such GET fails
    the build rather than waiting for a review to notice it.

    **The rule covers the whole owner class, not just the locality tier**,
    because the belt is about what a share token may *see* under the rollback:
    ``GET /insights`` and ``GET /moves/pending`` are ``OWNER_ONLY`` and serve
    absolute host paths. An entry is owed by *tier*, not by someone grading the
    payload.

    The templated ones cannot be expressed in an exact-match frozenset at all, so every
    templated owner-class GET must sit under a ``READ_BLOCKED_GET_PREFIXES`` prefix. A
    new one under a path share tokens also use (``/pictures/{id}/…``) cannot take a
    prefix; that is a real hole under the rollback, and this is where it is decided
    rather than discovered.

    **``GET /adapters/{sha256}/file`` is the sharpest member of that set**: it
    streams model weights, so under the documented
    ``AUTHZ_GATE_ENFORCING = False`` rollback a share token would download
    every adapter on the shelf. The gate refuses it and
    ``tests/test_model_shelf_api.py::test_no_share_token_can_download_a_model_file``
    proves that by mutation; this note is about the rollback.
    """
    owner_class = (
        AccessPolicy.OWNER_ONLY,
        AccessPolicy.LOCAL_OWNER_ONLY,
        AccessPolicy.LOOPBACK_OWNER_ONLY,
    )
    owner_class_gets = {
        path
        for (method, path), rp in ROUTE_POLICIES.items()
        if method == "GET" and rp.policy in owner_class
    }
    untemplated = {path for path in owner_class_gets if "{" not in path}
    assert untemplated, "the derivation found nothing - it has stopped working"
    missing = sorted(untemplated - READ_BLOCKED_GET_PATHS)
    assert missing == [], (
        f"owner-class GETs off the second belt: {missing}. Add each to "
        f"READ_BLOCKED_GET_PATHS in pixlstash/auth.py."
    )

    # The templated ones are held by READ_BLOCKED_GET_PREFIXES (#1293), which
    # closed the gap this test used to pin as five known routes.
    must_be_prefixed = {path for path in owner_class_gets if "{" in path}
    assert len(must_be_prefixed) >= 16, sorted(must_be_prefixed)
    uncovered = sorted(
        path
        for path in must_be_prefixed
        if not path.startswith(READ_BLOCKED_GET_PREFIXES)
    )
    assert uncovered == [], (
        f"templated owner-class GETs no READ_BLOCKED_GET_PREFIXES entry covers: "
        f"{uncovered}"
    )


# ---- LOOPBACK_OWNER_ONLY (7) - the flag-immune red line --------------------


def test_loopback_owner_only_allows_loopback():
    """A loopback owner reaches the red-line route (the gate passes; the handler
    then 404s on the bogus id - never a locality/loopback 403)."""
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
    ``allow_remote_host_ops=True`` - the flag can never loosen this tier."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            r = owner.post(_OPEN_LOCATION, headers=_xff(LAN_IPV4))
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


def test_choosing_the_trainers_python_is_loopback_only():
    """``/training/ai-toolkit/check`` runs the Python it is given and
    ``PUT /training/ai-toolkit/config`` makes it the trainer's argv[0], so a LAN
    or Tailscale owner is refused both even with ``allow_remote_host_ops``,
    while a loopback owner reaches the handler (its 422 for a path that is not
    a checkout, never a loopback 403)."""
    body = {"repo_path": "/home/me/no-such-ai-toolkit", "python": None}
    calls = (
        ("post", f"{API}/training/ai-toolkit/check"),
        ("put", f"{API}/training/ai-toolkit/config"),
    )
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            for method, url in calls:
                r = getattr(owner, method)(url, json=body)
                assert not _is_loopback_403(r), (
                    f"loopback owner must reach {url}; got {r.status_code}: {r.text}"
                )
                assert r.status_code == 422, (url, r.status_code, r.text)
                for ip in (LAN_IPV4, "100.64.0.5", "8.8.8.8"):
                    r = getattr(owner, method)(url, json=body, headers=_xff(ip))
                    assert _is_loopback_403(r), (
                        f"{ip} must be refused {url}; got {r.status_code}: {r.text}"
                    )


def test_config_names_the_ai_toolkit_paths_only_to_a_local_owner(tmp_path):
    """``GET /users/me/config`` is owner-only, so a remote owner reads it
    too; the checkout and its Python are host paths, so they read as null there
    with ``ai_toolkit_configured`` still saying an install is saved. A loopback
    or LAN owner reads them, as ``/training/ai-toolkit/status`` gives them."""
    repo = tmp_path / "ai-toolkit"
    (repo / "toolkit").mkdir(parents=True)
    (repo / "run.py").write_text("", encoding="utf-8")
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, False):
            r = owner.put(
                f"{API}/training/ai-toolkit/config",
                json={"repo_path": str(repo), "python": str(python)},
            )
            assert r.status_code == 200, r.text
            for headers in ({}, _xff(LAN_IPV4)):
                cfg = owner.get(f"{API}/users/me/config", headers=headers).json()
                assert cfg["ai_toolkit_repo_path"] == str(repo), cfg
                assert cfg["ai_toolkit_python"] == str(python), cfg
                assert cfg["ai_toolkit_configured"] is True
            cfg = owner.get(f"{API}/users/me/config", headers=_xff("8.8.8.8")).json()
            assert cfg["ai_toolkit_repo_path"] is None, cfg
            assert cfg["ai_toolkit_python"] is None, cfg
            assert cfg["ai_toolkit_configured"] is True
            assert str(tmp_path) not in json.dumps(cfg)
            r = owner.put(
                f"{API}/training/ai-toolkit/config",
                json={"repo_path": None, "python": None},
            )
            assert r.status_code == 200, r.text


def test_loopback_owner_only_public_403():
    """A public remote owner is 403'd on the red-line route (flag off)."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, False):
            r = owner.post(_OPEN_LOCATION, headers=_xff("8.8.8.8"))
            assert _is_loopback_403(r), (
                f"public owner must be 403'd on the red line; got {r.status_code}: {r.text}"
            )


# ---- library/folders/open - the Folders tree's Open in File Manager (#2448) --

_OPEN_TREE_FOLDER = f"{API}/library/folders/open"


def test_open_tree_folder_is_loopback_only_and_opens_only_a_listed_folder(
    monkeypatch,
):
    """Both directions on one server. A loopback owner opens a folder the
    Folders tree lists (one a picture is filed in, or above one), and nothing
    else: an unlisted path, a climb out and an empty folder on disk are all
    404. A LAN owner is refused with the flag on."""
    from pixlstash.routes import library_layout as layout_routes

    opened = []

    def fake_open(path):
        opened.append(path)
        return True

    monkeypatch.setattr(layout_routes, "open_in_file_manager", fake_open)
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        root = server.vault.image_root
        os.makedirs(os.path.join(root, "Global", "Mira"), exist_ok=True)
        os.makedirs(os.path.join(root, "Empty"), exist_ok=True)

        def _add(session):
            pic = Picture(file_path="Global/Mira/a.png")
            session.add(pic)
            session.commit()
            return pic.id

        server.vault.db.run_task(_add)
        with _enforcing(server):
            r = owner.get(f"{API}/library/folders")
            assert r.status_code == 200, r.text
            assert [f["path"] for f in r.json()["folders"]] == [
                "Global",
                "Global/Mira",
            ]

            r = owner.post(_OPEN_TREE_FOLDER, json={"path": "Global/Mira"})
            assert r.status_code == 200, r.text
            assert r.json()["folder"] == "Global/Mira"
            assert opened == [os.path.realpath(os.path.join(root, "Global", "Mira"))]

            # On disk but holding no picture, a climb out, and the root itself.
            for path in ("Empty", "..", "Global/..", "", "/"):
                r = owner.post(_OPEN_TREE_FOLDER, json={"path": path})
                assert r.status_code == 404, (path, r.text)
            r = owner.post(_OPEN_TREE_FOLDER, json={"kind": "global"})
            assert r.status_code == 422, r.text

            # A listed folder replaced by a symlink leading out of the library
            # is refused. Creating one needs a privilege Windows runners may
            # lack; there the escape is not set up, so it is not asserted.
            outside = tempfile.mkdtemp()
            moved = os.path.join(root, "Global", "Mira-moved")
            link = os.path.join(root, "Global", "Mira")
            os.rename(link, moved)
            try:
                try:
                    os.symlink(outside, link, target_is_directory=True)
                except OSError as e:
                    print(f"symlink refused on this host ({e}); escape check skipped")
                else:
                    try:
                        r = owner.post(_OPEN_TREE_FOLDER, json={"path": "Global/Mira"})
                        assert r.status_code == 404, r.text
                    finally:
                        os.unlink(link)
                # Listed, but gone from disk.
                r = owner.post(_OPEN_TREE_FOLDER, json={"path": "Global/Mira"})
                assert r.status_code == 404, r.text
            finally:
                os.rename(moved, link)
                os.rmdir(outside)

            # A host with no desktop says so rather than answering ok.
            monkeypatch.setattr(layout_routes, "open_in_file_manager", lambda p: False)
            r = owner.post(_OPEN_TREE_FOLDER, json={"path": "Global"})
            assert r.status_code == 500, r.text
            monkeypatch.setattr(layout_routes, "open_in_file_manager", fake_open)

            with _remote_host_ops(server, True):
                r = owner.post(
                    _OPEN_TREE_FOLDER,
                    json={"path": "Global"},
                    headers=_xff(LAN_IPV4),
                )
                assert _is_loopback_403(r), f"{r.status_code}: {r.text}"
            assert len(opened) == 1


# ---- comfyui/node-packs install - third-party code into ComfyUI (#2054) ----

_NODE_PACK_INSTALL = f"{API}/comfyui/node-packs/comfyui-inpaint-cropandstitch/install"


def test_node_pack_install_is_loopback_only_even_with_flag_on(monkeypatch):
    """Both directions on one server: a loopback owner reaches the install (the
    positive control, which also proves the route resolves), and an owner on
    the LAN, Tailscale or the internet is 403'd before the service is asked."""
    asked = []

    def install(comfyui_url, pack_id):
        asked.append(pack_id)
        return {
            "pack": pack_id,
            "version": "3.0.11",
            "manager": "v3",
            "restart": "requested",
            "detail": None,
        }

    monkeypatch.setattr(node_pack_install_service, "install", install)
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            r = owner.post(_NODE_PACK_INSTALL)
            assert r.status_code == 200, r.text
            assert asked == ["comfyui-inpaint-cropandstitch"]
            for ip in (LAN_IPV4, PRIVATE_10_IPV4, "100.64.0.5", "8.8.8.8"):
                r = owner.post(_NODE_PACK_INSTALL, headers=_xff(ip))
                assert _is_loopback_403(r), (
                    f"{ip} must be 403'd on the node-pack install even with "
                    f"allow_remote_host_ops=true; got {r.status_code}: {r.text}"
                )
            assert asked == ["comfyui-inpaint-cropandstitch"], (
                "a refused caller must never reach the install"
            )


# ---- pictures/export/folder - the export-to-folder red line (#291) --------

# A destination that cannot exist, so the gate's pass is provable: the handler
# past it 404s on "not a directory" rather than actually writing anything or
# spawning a file manager.
_EXPORT_FOLDER = (
    f"{API}/pictures/export/folder?destination=/nonexistent-291-export-destination"
)


def test_export_folder_loopback_owner_only_allows_loopback():
    """A loopback owner reaches the red-line route (the gate passes; the handler
    then 404s on the bogus destination - never a locality/loopback 403)."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server):
            r = owner.post(_EXPORT_FOLDER)
            assert not _is_loopback_403(r), (
                f"loopback owner must reach the red-line route; got {r.status_code}: {r.text}"
            )
            assert r.status_code == 404, (
                f"expected the handler's destination-not-found 404 past the "
                f"gate, got {r.status_code}: {r.text}"
            )


def test_export_folder_loopback_owner_only_rfc1918_403_even_with_flag_on():
    """THE CARVE-OUT: an RFC1918 LAN owner is 403'd on the red-line route EVEN
    with ``allow_remote_host_ops=True`` - the flag can never loosen this tier."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            r = owner.post(_EXPORT_FOLDER, headers=_xff(LAN_IPV4))
            assert _is_loopback_403(r), (
                f"RFC1918 must be 403'd on a LOOPBACK_OWNER_ONLY route even with "
                f"allow_remote_host_ops=true; got {r.status_code}: {r.text}"
            )
            r = owner.post(_EXPORT_FOLDER, headers=_xff("100.64.0.5"))
            assert _is_loopback_403(r), (
                f"Tailscale must be 403'd on the red line even with the flag on; "
                f"got {r.status_code}: {r.text}"
            )


def test_export_folder_loopback_owner_only_public_403():
    """A public remote owner is 403'd on the red-line route (flag off)."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, False):
            r = owner.post(_EXPORT_FOLDER, headers=_xff("8.8.8.8"))
            assert _is_loopback_403(r), (
                f"public owner must be 403'd on the red line; got {r.status_code}: {r.text}"
            )


_SHARE_COPIES = f"{API}/pictures/share-copies"  # #2417


def test_share_copies_is_loopback_only_even_with_the_flag_on():
    """Share copies hand back absolute host paths, so only a loopback
    owner passes the gate; RFC1918, Tailscale and public stay 403 with
    ``allow_remote_host_ops`` on. The positive half names an id no vault holds,
    so the handler answers 200 with it skipped and copies nothing."""
    body = {"picture_ids": [999999]}
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server):
            r = owner.post(_SHARE_COPIES, json=body)
            assert r.status_code == 200, r.text
            assert r.json()["skipped"] == [
                {"picture_id": 999999, "reason": "not_found"}
            ]
        with _enforcing(server), _remote_host_ops(server, True):
            for ip in (LAN_IPV4, "100.64.0.5", "8.8.8.8"):
                r = owner.post(_SHARE_COPIES, json=body, headers=_xff(ip))
                assert _is_loopback_403(r), (
                    f"{ip} must be 403'd on share-copies; got {r.status_code}: {r.text}"
                )


_OPEN_WITH = f"{API}/open-with"
_OPEN_WITH_LIST = {"picture_id": 999999}
_OPEN_WITH_BODY = {"picture_id": 999999, "app_id": "x"}


def test_open_with_is_loopback_only_and_closed_to_a_read_token(monkeypatch):
    """Both open-with routes pass a loopback owner and 403 RFC1918, Tailscale
    and public even with ``allow_remote_host_ops`` on. The GET lists the host's
    installed applications, so a READ token is refused by each belt on its
    own: the gate with the READ belt emptied of it, and the READ belt with the
    gate rolled back. Emptying both lets the same token reach the handler's
    404, which is what proves each refusal above was a refusal.

    The id is one no vault holds, so the loopback owner gets the handler's 404:
    the route resolves, and nothing is listed or launched."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            listed = owner.get(_OPEN_WITH, params=_OPEN_WITH_LIST)
            assert listed.status_code == 404, listed.text
            opened = owner.post(_OPEN_WITH, json=_OPEN_WITH_BODY)
            assert opened.status_code == 404, opened.text
            for ip in (LAN_IPV4, "100.64.0.5", "8.8.8.8"):
                for r in (
                    owner.get(_OPEN_WITH, params=_OPEN_WITH_LIST, headers=_xff(ip)),
                    owner.post(_OPEN_WITH, json=_OPEN_WITH_BODY, headers=_xff(ip)),
                ):
                    assert _is_loopback_403(r), (
                        f"{ip} must be 403'd on open-with; got {r.status_code}: {r.text}"
                    )

        minted = owner.post(
            f"{API}/users/me/token",
            json={"description": "full read", "scope": "READ"},
        )
        assert minted.status_code == 200, minted.text
        anon = TestClient(server.api, raise_server_exceptions=True)
        share = {"Authorization": f"Bearer {minted.json()['token']}"}
        assert anon.get(f"{API}/pictures", headers=share).status_code == 200, (
            "the READ token is dead; the refusals below would prove nothing"
        )

        def read_as_token():
            return anon.get(_OPEN_WITH, params=_OPEN_WITH_LIST, headers=share)

        assert "/api/v1/open-with" in READ_BLOCKED_GET_PATHS
        previously_enforcing = server.authz._enforcing
        try:
            # The READ belt alone, with the gate rolled back.
            server.authz._enforcing = False
            assert read_as_token().status_code == 403
            monkeypatch.setattr(
                "pixlstash.auth.READ_BLOCKED_GET_PATHS",
                READ_BLOCKED_GET_PATHS - {"/api/v1/open-with"},
            )
            # Neither: the token reaches the handler.
            assert read_as_token().status_code == 404
            # The gate alone.
            server.authz._enforcing = True
            assert read_as_token().status_code == 403
        finally:
            server.authz._enforcing = previously_enforcing


_SHELF_OPEN = f"{API}/models/999999/open-location"  # the #933 red-line route


def test_the_shelf_open_location_is_on_the_red_line_too():
    """The model shelf's `Open in file manager` spawns the same host GUI
    process as the other file-manager routes, so it gets the same carve-out proof -
    RFC1918, Tailscale and public all 403 EVEN with ``allow_remote_host_ops``
    on, and a loopback owner passes the gate.

    The positive half runs on an id no shelf holds, so the handler answers 404
    and nothing is spawned: what is under test is the gate in front of it, and
    a test that reached the spawn would open a file manager window on whichever
    machine ran the suite."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            for ip in (LAN_IPV4, PRIVATE_10_IPV4, "100.64.0.5", "8.8.8.8"):
                r = owner.post(_SHELF_OPEN, headers=_xff(ip))
                assert _is_loopback_403(r), (
                    f"{ip} must be 403'd on the shelf's open-location even with "
                    f"allow_remote_host_ops=true; got {r.status_code}: {r.text}"
                )
            r = owner.post(_SHELF_OPEN)
            assert not _is_loopback_403(r), (
                f"loopback owner must reach the shelf's open-location; "
                f"got {r.status_code}: {r.text}"
            )
            assert r.status_code == 404, (
                f"expected the handler's no-such-model 404 past the gate, "
                f"got {r.status_code}: {r.text}"
            )


def test_open_location_resolves_a_library_picture_against_the_library(
    monkeypatch,
):
    """A library picture stores its path relative to the library, so the
    folder opened is the library's own sub-folder, not one relative to the
    server's working directory.
    The spawn is stubbed so no file manager opens on the machine running this."""
    opened = []
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        image_root = server.vault.image_root
        os.makedirs(os.path.join(image_root, "show-in-folder"), exist_ok=True)

        def _add(session):
            pic = Picture(file_path=os.path.join("show-in-folder", "a.png"))
            session.add(pic)
            session.commit()
            return pic.id

        pic_id = server.vault.db.run_task(_add)
        # `subprocess` is one module, and the server's own background probes
        # run through it too: only the opener is caught.
        real_run = subprocess.run

        def _run(argv, **kw):
            if argv[0] in ("open", "xdg-open"):
                opened.append(argv[-1])
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, **kw)

        monkeypatch.setattr(subprocess, "run", _run)
        monkeypatch.setattr(os, "startfile", opened.append, raising=False)
        with _enforcing(server):
            r = owner.post(f"{API}/pictures/{pic_id}/open-location")
        monkeypatch.undo()
        assert r.status_code == 200, r.text
        assert opened == [os.path.join(image_root, "show-in-folder")]


# ---- server-config/open - the CSO Condition-1 sibling hole -----------------

_CONFIG_OPEN = f"{API}/server-config/open"


def test_server_config_open_loopback_owner_only_carve_out():
    """``POST /server-config/open`` spawns the host file browser via the
    byte-identical ``_open_in_os`` mechanism as the other red-line routes, so it
    is LOOPBACK_OWNER_ONLY. Same carve-out proof: loopback allowed; RFC1918 /
    Tailscale / public 403 EVEN with ``allow_remote_host_ops=True``.

    The loopback-allow path reaches the handler, which would spawn a real file
    browser - patch the config module's ``subprocess.run`` so the test never
    launches a GUI (the gate, which runs before the handler, is what we assert)."""
    from unittest import mock

    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            # NEGATIVE carve-out: none of these may pass even with the flag ON.
            for ip in (LAN_IPV4, "100.64.0.5", "8.8.8.8"):
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
            r = owner.get(_BROWSE, headers=_xff(LAN_IPV4))
            assert not _is_locality_403(r), (
                f"a LAN real client must be admitted; got {r.status_code}: {r.text}"
            )


# Import Server after the pure-unit tests are defined so predicate/policy tests
# do not depend on the heavier server import path.
from pixlstash.server import Server  # noqa: E402


# ---- The e2e test hook (LOOPBACK_OWNER_ONLY, conditionally mounted) --------
#
# Mounted only when ``enable_test_hooks`` is true, so it needs its own server
# env. Its declaration exists unconditionally; CONDITIONALLY_MOUNTED_ROUTES
# waives the "dead declaration" complaint for the normal (flag off) config.

_WS_HOOK = f"{API}/test-hooks/ws-event"
_WS_HOOK_BODY = {"event_type": "CHANGED_PICTURES", "picture_ids": [1]}
_RESCAN_HOOK = f"{API}/test-hooks/reference-folders/{{folder_id}}/rescan"
_FILE_HOOK = f"{API}/test-hooks/file-pictures"


@contextlib.contextmanager
def _test_hooks_owner_env():
    """Owner-authenticated server with ``enable_test_hooks`` ON."""

    tmp = tempfile.TemporaryDirectory()
    cfg = os.path.join(tmp.name, "server-config.json")
    with open(cfg, "w") as fh:
        json.dump(
            {
                "port": 8000,
                "trusted_proxies": ["testclient"],
                "enable_test_hooks": True,
                "disable_background_workers": True,
            },
            fh,
        )
    server = Server(cfg)
    server.__enter__()
    try:
        client = TestClient(server.api, raise_server_exceptions=True)
        r = client.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        )
        assert r.status_code == 200, r.text
        yield {"server": server, "owner": client, "tmp": tmp}
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


def test_test_hooks_route_is_absent_unless_the_flag_is_on():
    """Declaring the route must not cause it to EXIST.

    Asserted against the mounted route table, which is the precise claim; the
    HTTP status alone is ambiguous because the SPA catch-all answers unmatched
    paths (405 for a POST, not 404). Either way the handler is unreachable.
    """
    from pixlstash.route_inventory import api_endpoint_set

    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        mounted = api_endpoint_set(server.api)
        for hook in (_WS_HOOK, _RESCAN_HOOK, _FILE_HOOK):
            assert ("POST", hook) not in mounted, (
                f"{hook} must not be mounted without the flag"
            )
        with _enforcing(server):
            r = owner.post(_WS_HOOK, json=_WS_HOOK_BODY)
            assert r.status_code in (404, 405), (
                f"expected the route to be absent, got {r.status_code}: {r.text}"
            )


def test_test_hooks_declaration_does_not_boot_fail_when_unmounted():
    """The conditional waiver's whole job: a declaration for an absent route is
    NOT a dead declaration, so the normal configuration still boots enforcing."""
    from pixlstash.authz.registry import CONDITIONALLY_MOUNTED_ROUTES

    assert ("POST", _WS_HOOK) in CONDITIONALLY_MOUNTED_ROUTES
    assert ("POST", _RESCAN_HOOK) in CONDITIONALLY_MOUNTED_ROUTES
    assert ("POST", _FILE_HOOK) in CONDITIONALLY_MOUNTED_ROUTES
    with _owner_env() as env:
        server = env["server"]
        with _enforcing(server):
            # Would raise RuntimeError("...dead declaration(s)") without the waiver.
            server.authz.enforce_startup(server.api)


def test_test_hooks_loopback_owner_reaches_the_handler():
    """POSITIVE direction: with the flag on, a loopback owner gets through the
    gate to the handler (over-blocking would break the entire e2e suite)."""
    with _test_hooks_owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server):
            r = owner.post(_WS_HOOK, json=_WS_HOOK_BODY)
            assert not _is_loopback_403(r), (
                f"loopback owner must reach the hook; got {r.status_code}: {r.text}"
            )
            assert r.status_code == 200, (
                f"expected the handler's success past the gate, got "
                f"{r.status_code}: {r.text}"
            )
            assert r.json()["emitted"] == 1, r.text


def test_test_hooks_non_loopback_owner_is_403_even_with_flag_on():
    """NEGATIVE direction: an owner from LAN / Tailscale / public is 403'd even
    with ``allow_remote_host_ops=True`` - this tier is flag-immune, so switching
    ``enable_test_hooks`` on in a network-reachable deployment still does not
    expose the event-injection primitive remotely."""
    with _test_hooks_owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            for ip in (LAN_IPV4, PRIVATE_10_IPV4, "100.64.0.5", "8.8.8.8"):
                r = owner.post(_WS_HOOK, json=_WS_HOOK_BODY, headers=_xff(ip))
                assert _is_loopback_403(r), (
                    f"{ip} must be 403'd on the test hook even with "
                    f"allow_remote_host_ops=true; got {r.status_code}: {r.text}"
                )


def _insert_reference_folder(session, path):
    """A reference folder row, last scanned long ago; its id."""
    rf = ReferenceFolder(folder=path, label="r", last_scanned=1.0)
    session.add(rf)
    session.commit()
    session.refresh(rf)
    return rf.id


def test_rescan_hook_loopback_owner_marks_the_folder_due():
    """POSITIVE direction: a loopback owner reaches the rescan hook, which
    marks the folder due exactly as the watcher does, and 404s a folder that
    does not exist (so the 200 is the handler's, not a catch-all's)."""
    with _test_hooks_owner_env() as env:
        server, owner = env["server"], env["owner"]

        folder_id = server.vault.db.run_task(
            lambda session: _insert_reference_folder(session, env["tmp"].name)
        )
        with _enforcing(server):
            r = owner.post(_RESCAN_HOOK.format(folder_id=folder_id))
            assert r.status_code == 200, r.text
            assert r.json() == {"status": "success", "folder_id": folder_id}
            missing = owner.post(_RESCAN_HOOK.format(folder_id=folder_id + 1))
            assert missing.status_code == 404, missing.text
        last_scanned = server.vault.db.run_task(
            lambda session: session.get(ReferenceFolder, folder_id).last_scanned
        )
        assert last_scanned == 0.0


def test_rescan_hook_non_loopback_owner_is_403_even_with_flag_on():
    """NEGATIVE direction, as for the ws-event hook: no remote owner reaches
    it, whatever allow_remote_host_ops says."""
    with _test_hooks_owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            for ip in (LAN_IPV4, PRIVATE_10_IPV4, "100.64.0.5", "8.8.8.8"):
                r = owner.post(_RESCAN_HOOK.format(folder_id=1), headers=_xff(ip))
                assert _is_loopback_403(r), (
                    f"{ip} must be 403'd on the rescan hook even with "
                    f"allow_remote_host_ops=true; got {r.status_code}: {r.text}"
                )


def _insert_picture(session):
    """A picture row filed on no workflow; its id."""
    pic = Picture(file_path="file-hook.png")
    session.add(pic)
    session.commit()
    session.refresh(pic)
    return pic.id


def _filed_on(server, picture_id):
    return server.vault.db.run_task(
        lambda session: session.get(Picture, picture_id).run_workflow_id
    )


def test_file_hook_loopback_owner_files_and_unfiles_the_pictures():
    """POSITIVE direction: a loopback owner files a picture on a workflow over
    whatever it held, unfiles it with null, and gets a 404 for a picture that
    does not exist (so the 200 is the handler's, not a catch-all's)."""
    with _test_hooks_owner_env() as env:
        server, owner = env["server"], env["owner"]
        picture_id = server.vault.db.run_task(_insert_picture)
        with _enforcing(server):
            for workflow_id in ("manual:a", "manual:b"):
                r = owner.post(
                    _FILE_HOOK,
                    json={"picture_ids": [picture_id], "workflow_id": workflow_id},
                )
                assert r.status_code == 200, r.text
                assert r.json() == {"status": "success", "filed": 1}
                assert _filed_on(server, picture_id) == workflow_id
            r = owner.post(_FILE_HOOK, json={"picture_ids": [picture_id]})
            assert r.status_code == 200, r.text
            assert _filed_on(server, picture_id) is None
            missing = owner.post(
                _FILE_HOOK,
                json={"picture_ids": [picture_id, picture_id + 1], "workflow_id": "x"},
            )
            assert missing.status_code == 404, missing.text
        # A refused call writes none of them.
        assert _filed_on(server, picture_id) is None


def test_file_hook_non_loopback_owner_is_403_even_with_flag_on():
    """NEGATIVE direction, as for the other hooks: no remote owner reaches
    it, whatever allow_remote_host_ops says, and nothing is written."""
    with _test_hooks_owner_env() as env:
        server, owner = env["server"], env["owner"]
        picture_id = server.vault.db.run_task(_insert_picture)
        body = {"picture_ids": [picture_id], "workflow_id": "manual:a"}
        with _enforcing(server), _remote_host_ops(server, True):
            for ip in (LAN_IPV4, PRIVATE_10_IPV4, "100.64.0.5", "8.8.8.8"):
                r = owner.post(_FILE_HOOK, json=body, headers=_xff(ip))
                assert _is_loopback_403(r), (
                    f"{ip} must be 403'd on the file hook even with "
                    f"allow_remote_host_ops=true; got {r.status_code}: {r.text}"
                )
        assert _filed_on(server, picture_id) is None


def test_test_hooks_refuse_read_and_share_tokens():
    """NEGATIVE direction for tokens: neither an unscoped READ token nor a
    resource-scoped share token reaches either hook, from loopback. The token
    reads ``/pictures`` (it is live) and the owner reaches both hooks (they
    resolve), so each 403 is a refusal, not a dead token or a dead path."""

    with _test_hooks_owner_env() as env:
        server, owner = env["server"], env["owner"]
        folder_id = server.vault.db.run_task(
            lambda session: _insert_reference_folder(session, env["tmp"].name)
        )
        picture_id = server.vault.db.run_task(_insert_picture)
        hooks = (
            (_WS_HOOK, _WS_HOOK_BODY),
            (_RESCAN_HOOK.format(folder_id=folder_id), None),
            (_FILE_HOOK, {"picture_ids": [picture_id], "workflow_id": "manual:a"}),
        )
        minted = {
            "read": {"description": "read", "scope": "READ"},
            "share": {
                "description": "set share",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": 1,
            },
        }
        with _enforcing(server):
            for path, body in hooks:
                r = owner.post(path, json=body)
                assert r.status_code == 200, f"{path} must resolve: {r.text}"
            # Cookie-less: the middleware prefers the owner's session cookie.
            anon = TestClient(server.api, raise_server_exceptions=True)
            for kind, request in minted.items():
                token = owner.post(f"{API}/users/me/token", json=request)
                assert token.status_code == 200, token.text
                headers = {"Authorization": f"Bearer {token.json()['token']}"}
                assert (
                    anon.get(f"{API}/pictures", headers=headers).status_code == 200
                ), f"the {kind} token is dead; its refusals would prove nothing"
                for path, body in hooks:
                    r = anon.post(path, json=body, headers=headers)
                    assert r.status_code == 403, (
                        f"a {kind} token must not reach {path}; "
                        f"got {r.status_code}: {r.text}"
                    )


def test_conditionally_mounted_routes_are_all_declared():
    """The waiver is an ABSENCE waiver, not a coverage waiver: every conditional
    route must still carry a policy, or it could be used to smuggle an undeclared
    route past the matrix."""
    from pixlstash.authz.registry import CONDITIONALLY_MOUNTED_ROUTES, ROUTE_POLICIES

    assert CONDITIONALLY_MOUNTED_ROUTES, "the set must not silently empty out"
    missing = CONDITIONALLY_MOUNTED_ROUTES - set(ROUTE_POLICIES)
    assert not missing, f"conditional routes with no declaration: {sorted(missing)}"


# ---- ai-toolkit training: host-process routes and the trainer's settings -------


def test_training_host_routes_refuse_a_remote_owner_and_admit_lan():
    """POST /training/jobs, POST .../cancel and DELETE /training/jobs/{id} are
    LOCAL_OWNER_ONLY: a public owner is 403'd naming the flag, and the same
    route answers from the LAN, which is the in-scope control that proves the
    route resolves and the 403 is the locality gate, not a dead path."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, False):
            # POST with an empty body: the gate admits the LAN owner, then the
            # body fails validation (422), which proves the route handled it.
            remote = owner.post(
                f"{API}/training/jobs", json={}, headers=_xff("8.8.8.8")
            )
            assert remote.status_code == 403, remote.text
            assert "allow_remote_host_ops" in remote.text, remote.text
            lan = owner.post(f"{API}/training/jobs", json={}, headers=_xff(LAN_IPV4))
            assert not _is_locality_403(lan), lan.text
            assert lan.status_code == 422, lan.text

            for method in ("cancel", "delete"):
                path = f"{API}/training/jobs/999999" + (
                    "/cancel" if method == "cancel" else ""
                )
                call = owner.post if method == "cancel" else owner.delete
                remote = call(path, headers=_xff("8.8.8.8"))
                assert remote.status_code == 403, (path, remote.text)
                assert "allow_remote_host_ops" in remote.text, remote.text
                # The LAN owner reaches the handler: an unknown job is its 404.
                lan = call(path, headers=_xff(LAN_IPV4))
                assert lan.status_code == 404, (path, lan.text)
                assert "Training job not found" in lan.text, lan.text


def test_training_host_routes_admit_a_remote_owner_with_the_flag_on():
    """The dedicated flag is the one way a remote owner starts or deletes a
    trainer job; the same route answers the handler, not the gate."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            r = owner.delete(f"{API}/training/jobs/999999", headers=_xff("8.8.8.8"))
            assert r.status_code == 404, r.text
            assert "Training job not found" in r.text, r.text


def test_ai_toolkit_install_routes_refuse_a_remote_owner_and_admit_lan():
    """discover and status disclose host paths, so they are LOCAL_OWNER_ONLY: a
    remote owner is 403'd naming the flag, and the LAN owner reaches the handler.
    check and PUT config are on the loopback tier and are covered by
    test_choosing_the_trainers_python_is_loopback_only."""
    cases = (
        ("get", f"{API}/training/ai-toolkit/discover"),
        ("get", f"{API}/training/ai-toolkit/status"),
    )
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, False):
            for method, path in cases:
                call = getattr(owner, method)
                remote = call(path, headers=_xff("8.8.8.8"))
                assert remote.status_code == 403, (path, remote.text)
                assert "allow_remote_host_ops" in remote.text, (path, remote.text)
                lan = call(path, headers=_xff(LAN_IPV4))
                assert not _is_locality_403(lan), (path, lan.text)
                assert lan.status_code == 200, (path, lan.text)


def test_ai_toolkit_discover_admits_a_remote_owner_with_the_flag_on():
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, True):
            r = owner.get(
                f"{API}/training/ai-toolkit/discover", headers=_xff("8.8.8.8")
            )
            assert r.status_code == 200, r.text
            assert "searched" in r.json(), r.text


def test_ai_toolkit_config_is_saved_only_from_this_computer(tmp_path):
    """PUT /training/ai-toolkit/config stores the checkout and the Python that is
    argv[0] of the trainer, so it is LOOPBACK_OWNER_ONLY: a remote or LAN
    owner is refused, even to disconnect and even with allow_remote_host_ops,
    and the loopback owner saves the checkout's Python and reads it back. PATCH
    /users/me/config does not write these keys: 422 from any address."""
    repo = tmp_path / "ai-toolkit"
    (repo / "toolkit").mkdir(parents=True)
    (repo / "run.py").write_text("", encoding="utf-8")
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    body = {"repo_path": str(repo), "python": str(python)}
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        for flag in (False, True):
            with _enforcing(server), _remote_host_ops(server, flag):
                for address in ("8.8.8.8", LAN_IPV4):
                    for attempt in (body, {"repo_path": None, "python": None}):
                        refused = owner.put(
                            f"{API}/training/ai-toolkit/config",
                            json=attempt,
                            headers=_xff(address),
                        )
                        assert _is_loopback_403(refused), (address, refused.text)

        with _enforcing(server), _remote_host_ops(server, False):
            saved = owner.put(f"{API}/training/ai-toolkit/config", json=body)
            assert saved.status_code == 200, saved.text
            for address in (LAN_IPV4, "8.8.8.8"):
                patched = owner.patch(
                    f"{API}/users/me/config",
                    json={"ai_toolkit_python": None},
                    headers=_xff(address),
                )
                assert patched.status_code == 422, (address, patched.text)
                assert "PUT /api/v1/training/ai-toolkit/config" in patched.text
            stored = owner.get(f"{API}/users/me/config").json()
            assert stored["ai_toolkit_python"] == str(python)
            cleared = owner.put(
                f"{API}/training/ai-toolkit/config",
                json={"repo_path": None, "python": None},
            )
            assert cleared.status_code == 200, cleared.text
