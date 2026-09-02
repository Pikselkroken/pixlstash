"""§16.3 host-capability access design (principal ruling 2026-07-21).

Covers the three-lens (CSO/Principal/CEO) decided design landing before Step 5:

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
``enforcing`` explicitly rather than relying on the shipped default, which was
``False`` when this file was written and is ``True`` today - one test
deliberately turns it **off**, to prove the middleware's
``READ_BLOCKED_GET_PATHS`` still refuses a share token if the documented
one-line rollback is ever taken.
"""

import contextlib
import json
import os
import tempfile

import pytest

from pixlstash.auth import (
    READ_BLOCKED_GET_PATHS,
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
from pixlstash.route_inventory import api_endpoint_set
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
    ("POST", "/api/v1/server-config/open"),
    ("POST", "/api/v1/pictures/export/folder"),
    ("POST", "/api/v1/test-hooks/ws-event"),
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


def test_host_capability_tier_split_is_47_local_7_loopback():
    """The loopback tier is the 5 file-manager spawns, the process restart and
    the e2e test hook; the filesystem/folder routes stay LOCAL_OWNER_ONLY. 54
    routes carry a locality tier = 47 local + 7 loopback.

    History, so a future change to this number arrives with its reason: 16 = 13 +
    3 originally; 17 = 13 + 4 after CSO Condition 1 folded in
    ``server-config/open``; 18 = 13 + 5 when the e2e test hook was declared; 19 =
    14 + 5 from 2026-08-01, when ``POST /libraries/active`` joined the local tier.
    That one is **not** a filesystem route and does not take a host path: it
    is local-only because switching library is the pivot that would otherwise let
    one stolen owner token reach every registered library, and because it resets
    every connected client (plan §11 q4). 23 = 18 + 5 from 2026-08-09, when the
    model shelf's four ``model-folders`` mutators joined the local tier (shelf
    plan B5): three take a caller-supplied host path and the fourth walks one,
    which is the reference-folder class exactly. The shelf's *read* routes stay
    ``owner_only`` - they surface host paths but take none.

    26 = 21 + 5 from later the same day, when the shelf's **move** block (B7)
    joined it: ``POST``, ``GET`` and ``DELETE /model-moves``. The POST is
    settled by precedent - it writes files into one registered folder and
    unlinks them from another, which is strictly more than
    ``reference-folders/{id}/move-pictures``, already on this tier. The GET is
    the deliberate one: it is not a shelf read but the *control surface* of a
    host-filesystem operation - how a move is watched, beside the DELETE that
    stops one - so the tier that alone may start a move is the tier that may
    observe and steer it. **Not** a secrecy claim about the relpaths: a remote
    owner is 200 on ``GET /adapters``, which serves ``locations[].folder_path``
    and ``locations[].relpath`` for every copy (the earlier wording here said
    otherwise and was corrected in the B7 sign-off). The DELETE is the POST's
    authority from the other end.

    28 = 23 + 5 later still, when the shelf's **ai-toolkit import** block joined
    it: ``GET /model-folders/{folder_id}/runs`` and ``POST /model-imports``.
    The listing walks a registered output root, which is ``rescan``'s authority;
    the import writes into one registered folder and may unlink from the output
    root, which is ``POST /model-moves``' authority. Neither takes a host path -
    the import names a registered folder id and a run *name* - so they are on
    this tier for what they do, not for what they accept.

    29 = 24 + 5 with ``POST /model-folders/{folder_id}/relocate``, which moves
    every file the managed model store holds to a caller-supplied host path and
    unlinks the originals. It is the ``reference-folders/{folder_id}/relocate``
    class carrying ``POST /model-moves``' file movement, so it is the one route
    in the shelf that is on this tier for **both** reasons at once.

    30 = 25 + 5 with ``GET /model-folders/{folder_id}/runs/{run_name}/samples/
    {filename}``, which serves one preview image out of a registered output root
    so a step can be judged before it is imported: it reads inside a registered
    host root and writes nothing, which is ``rescan``'s authority class.

    31 = 26 + 5 with ``POST /model-files``, the shelf's ``Add file`` (plan F6).
    It is the second route on this tier for **both** reasons at once, and the
    first shelf route that takes a host path in its *body*: it copies one loose
    file from anywhere on the machine into a registered folder and registers it.
    The path cannot be avoided - the file is by definition somewhere nobody
    registered - so the containment is on the write and the read is bounded
    instead (one regular ``.safetensors``, refused outright if it already lies
    inside a registered folder). It never unlinks: the source is the owner's own
    file.

    32 = 27 + 5 with ``GET /taggers/plugin-diagnostics`` (#326). It is the
    first route on this tier for **disclosure alone**: it takes no path, walks
    nothing and writes nothing. It returns two things and both name paths on
    the host - the folder the tagger registry scans, which is under the owner's
    home directory, and the import failures of the plugins in it, whose message
    is ``str(exc)`` from third-party code and so carries whatever path that code
    was reaching for. Both were fields on ``GET /taggers``, which was ANY_TOKEN,
    so every share-link holder was reading them. Splitting them out costs a
    remote owner nothing real: acting on either means editing a file in that
    folder and restarting. (``GET /taggers`` itself went ``any_token`` ->
    ``owner_only`` in the same change, which is not a locality tier and so does
    not move this arithmetic.)

    33 = 28 + 5 with ``GET /adapters/{sha256}/file``, which streams one
    registered adapter's bytes so a generator on another machine can use what
    this one catalogues - the locations the detail route serves are *this*
    host's paths and mean nothing over there. It is the **first shelf read off
    the ``owner_only`` tier**, and the line that kept the others on it says why:
    they "surface host paths but take none". This one takes none either, but it
    does not surface a path - it returns the raw bytes of a file inside a
    registered model folder, which is the ``.../runs/{run_name}/samples/
    {filename}`` authority class exactly: reads inside a registered host root,
    writes nothing, and is a new capability rather than a narrower view of the
    metadata route beside it. Loopback/LAN/Tailscale is the deployment the route
    exists for, so the tier costs it nothing; a genuinely remote generator needs
    ``allow_remote_host_ops``, which is the safe direction to fail in.

    34 = 29 + 5 with ``POST /model-files/delete``, the shelf's delete verb
    (#933). It is the **unlink half of ``POST /model-moves`` standing alone**:
    it removes the owner's model files - to the OS trash by default, permanently
    on request - and then drops their hub rows. It takes no host path, and the
    same containment the mover uses for its source decides every path it
    touches; what puts it here is the destruction itself, which is the strongest
    thing the shelf does to a disk and is not made weaker by the ids being ours
    rather than the caller's.

    35 = 29 + 6 with ``POST /models/{model_id}/open-location``, the shelf's
    ``Open in file manager`` (#933) and the **first addition to the loopback
    tier since the e2e test hook**. It is the same host-GUI spawn as the three
    file-manager routes before it - ``server/restart``, the fourth member, drives
    the host shell without spawning a GUI - reached through the shared
    ``pixlstash/utils/host_open.py`` rather than inline, so it needs no new
    argument for the tier: the authority is the host's own shell. It is here for
    the *spawn* and not for an input - there is no body, the id is a hub
    ``model.id``, and the path is the scanner's own folder joined to its relpath
    and contained, exactly as ``GET /adapters/{sha256}/file`` contains the same
    join. The local count is unchanged.

    37 = 31 + 6 with the two ``GET /models/{model_id}/samples`` routes, which
    read a training run's previews back off the shelf after the import copied
    them into ``<stem>_samples/`` beside the checkpoint. The byte route is
    ``GET /adapters/{sha256}/file`` again - raw bytes out of a registered model
    folder - and the listing walks one directory inside that folder, reporting
    names of files PixlStash never registered, which is ``rescan``'s authority
    narrowed to a directory. **The plan for that change asked for
    ``owner_only``**, on the grounds that both are addressed by a ``model.id``
    with no host path crossing the wire; that is the argument the
    ``/adapters/{sha256}/file`` entry above records as *not* the argument, since
    the tier follows the authority exercised rather than what the route accepts.
    The listing is kept beside the byte route rather than one tier below it, so
    a caller who may not fetch a preview is not handed a list of them. The
    loopback count is unchanged: both are reads, and neither spawns anything.

    41 = 35 + 6 with the four v1.11 library-lifecycle routes, and they land on
    this tier for three different reasons rather than one. ``GET
    /libraries/inspect`` and ``POST /libraries`` are the plain path-authority
    case: both take a caller-supplied host path through the same
    ``validate_reference_folder_path`` chokepoint as the folder picker, the
    first walking it to say what the folder is and the second writing a vault
    into it and restricting it to the owner. Neither creates a directory -
    ``POST /filesystem/folders``, already on this tier, is what the picker's
    ``New folder`` uses - so the authority stays bounded to the one folder the
    owner named. ``DELETE /libraries/{library_uuid}`` is the ``POST
    /libraries/active`` argument again and **not** the path one: it takes a
    registry uuid, removes no file, and is here because every share link
    pointing at that library stops working, which is authority over other
    principals' state. ``PATCH /libraries/{library_uuid}`` is the weakest of the
    four and is here by consistency rather than capability - it writes one hub
    column and renames nothing on disk - because the Settings pane gates its
    whole management menu on one ``can_manage`` locality answer, and splitting
    the rename onto a looser tier would give that pane two rules to explain
    while buying a remote owner nothing they could act on. The loopback count is
    unchanged: none of the four spawns anything.

    43 = 37 + 6 with the two ``/server-config/views`` routes, PixlStash Views
    (v1.11 Phase 7): the library's sets, people and projects published as folders
    of **links** to the files the owner already keeps. The PATCH takes a
    caller-supplied host path and writes a folder tree into it, which is the
    ``POST /model-folders`` class for what it accepts and the ``POST
    /model-moves`` class for the filesystem it drives - so it is the third route
    here for both reasons at once. It is on this tier for the authority and not
    for the destruction: it creates only links, and the one thing it unlinks is
    a name that is not the last one - a symlink, or a regular file with
    ``st_nlink > 1``. ``shutil.rmtree`` is deliberately not used, because it is
    not link-aware and would delete a file the owner had dropped into a view
    folder; anything that is not a link is reported back as ``kept_by_owner``
    and left standing. A folder that already holds content and carries no
    ``.pixlstash-views`` marker is refused rather than adopted, so a views root
    aimed at a folder of real pictures never becomes one. The GET is the control surface argument that put ``GET
    /model-moves`` here rather than one tier down: it names the host folder the
    tree went to, and the tier that alone may publish it is the tier that may see
    where it landed. It is also on ``READ_BLOCKED_GET_PATHS``, so the documented
    ``AUTHZ_GATE_ENFORCING = False`` rollback does not hand that path back to
    every share token. The loopback count is unchanged: neither route spawns
    anything.

    46 = 40 + 6 with the folder-structure read's three routes (v1.11 Phase 2):
    ``POST``, ``GET .../status`` and ``DELETE /folder-structure/read``. The POST
    is ``GET /filesystem/browse``'s class and then some - it takes a
    caller-supplied host path, walks it *recursively* and decodes pictures out
    of it, where browse lists one directory - so it must not be a second,
    weaker way to ask what is on the disk. It is the ``GET /libraries/inspect``
    lesson above applied one route later, and then once more: the blocklist runs
    after ``realpath`` (validating the string the caller sent lets a symlink hand
    ``/etc`` to a route that walks it) **and again on every directory the walk
    descends into**, because a root-only check is a check on one string - ``/``
    names no restricted directory and contains all of them. Measured: 391 of 400
    folders came out of ``/etc``, ``/proc`` and ``/root`` before that second
    check, and 0 after. The GET is the deliberate
    one, for the reason ``GET /model-moves`` is on this tier: what it carries
    *is* the answer, a map of the owner's folder names, tree shape and picture
    counts, so polling cannot be a lower bar than starting. The DELETE is the
    POST's authority from the other end. None of the three writes anything -
    no row is created, no file is moved - which is why the tier follows the
    disclosure and the path, not a destructive verb. The loopback count is
    unchanged: nothing here spawns anything.

    48 = 46 + 2 with the folder-structure commit's two routes (v1.11 Phase 3):
    ``POST`` and ``GET .../commit/status``. The POST takes no fresh host path -
    it addresses a settled read by ``task_id`` - but it is the write that
    follows the read's own tier: it registers the read's root as a reference
    folder and creates the accepted projects/people/sets/tags from it, which is
    ``POST /reference-folders``' authority reached through a different door. The
    GET is the read status route's own argument again: what it carries is the
    commit's result, the same host-path-derived map the read already gates at
    this tier, so polling the write must not be a lower bar than polling the
    read was. Neither spawns anything, so the loopback count is unchanged.

    50 = 44 + 6 with the layout pair (v1.11 Phase 4b): ``GET`` and
    ``PATCH /server-config/layout``. Neither takes a host path at all - the root
    is the library's own - and the PATCH moves nothing, because the release's
    rule is that every path already in the library is true the moment it is
    written. What puts them here is the authority the PATCH *hands out*: from
    then on a background task renames the owner's files into the folder names it
    chose, so the tier that may decide that is the tier that holds it, and the
    GET is its control surface by the ``GET /model-moves`` argument. The GET is
    also on ``READ_BLOCKED_GET_PATHS``, so an ``AUTHZ_GATE_ENFORCING = False``
    rollback does not hand the shape of the owner's folder tree to share tokens.
    The move itself is NOT here: ``POST /pictures/layout/move-to-match`` is
    ``picture_scoped``, on the ``POST /pictures/rotate`` line, because the
    caller supplies pictures and never a path. The loopback count is unchanged:
    neither route spawns anything.

    51 = 44 + 7 with ``POST /pictures/export/folder`` (#291), export-to-folder:
    a local owner already has the destination mounted, so the ZIP-and-download
    round trip in ``POST /pictures/export`` is pure overhead, and this route
    writes the exported pictures straight into a caller-named destination
    instead. It takes a caller-supplied host path exactly like the filesystem
    picker's routes, which alone would be ``LOCAL_OWNER_ONLY`` - but once every
    picture is written it opens the destination in the host file manager
    through the shared ``pixlstash/utils/host_open.py``, the same spawn as
    ``pictures/{id}/open-location`` and the shelf's `Open in file manager`, and
    that is the stricter tier the write-then-open pair as a whole is on. The
    local count is unchanged: nothing else here moved.

    52 = 45 + 7 with ``DELETE /folder-structure/commit`` (``bd1ff8f7``), which
    is ``DELETE /folder-structure/read`` one route later: it stops the owner's
    in-flight commit - abort, or "organise later" - which is authority over
    another principal's operation and belongs on the tier that starts it. **It
    landed without this number being moved**, so the assertion below was red on
    ``develop`` until the entry after this one; recorded here rather than
    quietly folded in, because a counter that gets corrected without saying so
    is a counter nobody trusts next time.

    54 = 47 + 7 with the migration pair (v1.11 Phase 4c): ``GET`` and ``POST
    /server-config/layout/migration``, moving an existing library onto the
    layout the pair above chose. The POST is the strongest host-filesystem
    thing any route in this library does - it renames *every* picture in the
    library's own root - and it is above ``POST /pictures/layout/move-to-match``
    for the reason that route is ``picture_scoped``: there the caller names the
    pictures, so the scope check is the check that matters; here the caller
    names none and the scope is the whole library, so there is nothing for a
    per-object gate to bound and the tier has to carry it instead. It takes no
    host path - the root is the library's own, every destination is rendered
    from a layout only this tier could have set - and the Phase 4b planner's
    refusals still stand under it (a source outside the root, a symlink, a
    destination that would escape). The GET is the control-surface argument
    again, and this time for a second reason as well: what it returns is a
    count of the owner's files, sample paths and where a mount point sits
    inside their library, which is ``GET /folder-structure/read/status``'s
    disclosure class. It is on ``READ_BLOCKED_GET_PATHS`` beside ``GET
    /server-config/layout`` for the same rollback reason. The loopback count is
    unchanged: neither route spawns anything.

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
    assert len(loopback) == 7, sorted(loopback)
    assert len(local) == 47, sorted(local)


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
    """The in-scope 200 half of the Phase 4c declaration, over a real server.

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
    """Both directions on the #326 split, plus the reason it exists.

    The plugin folder and the load-failure messages are host-path disclosures,
    so a remote owner is refused them (flag off) and a share-scoped token is
    refused them from anywhere; a loopback owner still gets them, because
    over-blocking is its own regression.

    The negatives assert on the **path string**, not on a field name: an
    earlier version of this test checked that ``plugin_dirs`` was absent from
    ``GET /taggers``, which the ``load_error`` row beside it satisfied while
    still carrying the folder. Grepping the serialised body for the owner's
    home directory is the assertion that cannot be satisfied by moving the leak
    to another key - or to another directory.

    ``GET /taggers`` is now owner-only and gets the stronger check of the two:
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
            from starlette.testclient import TestClient

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


def test_the_plugin_routes_survive_the_documented_gate_rollback():
    """The second belt: ``READ_BLOCKED_GET_PATHS``, with the gate switched off.

    ``AUTHZ_GATE_ENFORCING = False`` is a documented one-line rollback, and
    ``GET /filesystem/browse`` is on both belts precisely so taking it does not
    re-open a filesystem disclosure. The two tagger routes are GETs on the same
    tier, so they need the same pair - a fact the #326 review reproduced by
    reading the owner's home directory out of the diagnostics route with the
    gate off. The owner (no token, session cookie) is unaffected either way.
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
        from starlette.testclient import TestClient

        anon = TestClient(server.api, raise_server_exceptions=True)
        share = {"Authorization": f"Bearer {minted.json()['token']}"}
        # The rollback state, set explicitly: the gate ships enforcing.
        previously_enforcing = server.authz._enforcing
        server.authz._enforcing = False
        try:
            assert anon.get(f"{API}/pictures", headers=share).status_code == 200, (
                "the share token is dead; the refusals below would prove nothing"
            )
            for route in (_TAGGER_DIAGNOSTICS, f"{API}/taggers"):
                refused = anon.get(route, headers=share)
                assert refused.status_code == 403, (
                    f"{route} must stay closed to a READ token with the gate "
                    f"off; got {refused.status_code}"
                )
            # The owner still reaches both, from the same middleware.
            assert owner.get(_TAGGER_DIAGNOSTICS).status_code == 200
            assert owner.get(f"{API}/taggers").status_code == 200
        finally:
            server.authz._enforcing = previously_enforcing


def test_every_untemplated_locality_get_is_on_the_read_blocked_belt():
    """Derive the belt's membership instead of writing it down.

    ``READ_BLOCKED_GET_PATHS`` matches literal paths, so a GET on the locality
    tier is only protected under the documented ``AUTHZ_GATE_ENFORCING = False``
    rollback if its own path is in that frozenset. The rollback test beside this
    one names two paths; this one asserts the *rule*, so the next such GET fails
    the build rather than waiting for a review to notice it.

    The templated ones cannot be expressed in an exact-match frozenset at all.
    They are pinned as a known set rather than ignored: adding a fourth fails
    here, and closing the gap needs prefix matching (the follow-up recorded in
    ``tests/test_model_shelf_api.py`` and ``docs/backend_architecture.md`` §16.3).

    **``GET /adapters/{sha256}/file`` joined that set on 2026-08-15, and it is
    the sharpest member of it.** The other two serve a run listing and a preview
    image; this one streams model weights. So under the documented
    ``AUTHZ_GATE_ENFORCING = False`` rollback a share token would not read a
    directory - it would download every adapter on the shelf. It is on the list
    rather than closed here because closing it means prefix matching in a belt
    every route passes through, which is its own change with its own review, and
    a bespoke ``startswith`` for one route is the kind of special case that rots.
    The gate refuses it today and
    ``tests/test_model_shelf_api.py::test_no_share_token_can_download_a_model_file``
    proves that by mutation; this note is about the rollback, not about today.
    """
    tier = (AccessPolicy.LOCAL_OWNER_ONLY, AccessPolicy.LOOPBACK_OWNER_ONLY)
    tier_gets = {
        path
        for (method, path), rp in ROUTE_POLICIES.items()
        if method == "GET" and rp.policy in tier
    }
    untemplated = {path for path in tier_gets if "{" not in path}
    assert untemplated, "the derivation found nothing - it has stopped working"
    missing = sorted(untemplated - READ_BLOCKED_GET_PATHS)
    assert missing == [], (
        f"locality-tier GETs off the second belt: {missing}. Add each to "
        f"READ_BLOCKED_GET_PATHS in pixlstash/auth.py."
    )

    templated_gap = sorted(path for path in tier_gets if "{" in path)
    assert templated_gap == [
        "/api/v1/adapters/{sha256}/file",
        "/api/v1/model-folders/{folder_id}/runs",
        "/api/v1/model-folders/{folder_id}/runs/{run_name}/samples/{filename}",
        "/api/v1/models/{model_id}/samples",
        "/api/v1/models/{model_id}/samples/{filename}",
    ], templated_gap


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


def test_loopback_owner_only_public_403():
    """A public remote owner is 403'd on the red-line route (flag off)."""
    with _owner_env() as env:
        server, owner = env["server"], env["owner"]
        with _enforcing(server), _remote_host_ops(server, False):
            r = owner.post(_OPEN_LOCATION, headers=_xff("8.8.8.8"))
            assert _is_loopback_403(r), (
                f"public owner must be 403'd on the red line; got {r.status_code}: {r.text}"
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


_SHELF_OPEN = f"{API}/models/999999/open-location"  # the #933 red-line route


def test_the_shelf_open_location_is_on_the_red_line_too():
    """#933: the model shelf's `Open in file manager` spawns the same host GUI
    process as its four predecessors, so it gets the same carve-out proof -
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


# ---- server-config/open - the CSO Condition-1 sibling hole -----------------

_CONFIG_OPEN = f"{API}/server-config/open"


def test_server_config_open_loopback_owner_only_carve_out():
    """CSO Condition 1: ``POST /server-config/open`` spawns the host file browser
    via the byte-identical ``_open_in_os`` mechanism as the other 3 red-line
    routes, but shipped ``owner_only`` with NO locality check. It is reclassified
    LOOPBACK_OWNER_ONLY. Same carve-out proof: loopback allowed; RFC1918 /
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


@contextlib.contextmanager
def _test_hooks_owner_env():
    """Owner-authenticated server with ``enable_test_hooks`` ON."""
    from starlette.testclient import TestClient

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
        assert ("POST", _WS_HOOK) not in api_endpoint_set(server.api), (
            "the test-hooks router must not be mounted without the flag"
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


def test_conditionally_mounted_routes_are_all_declared():
    """The waiver is an ABSENCE waiver, not a coverage waiver: every conditional
    route must still carry a policy, or it could be used to smuggle an undeclared
    route past the matrix."""
    from pixlstash.authz.registry import CONDITIONALLY_MOUNTED_ROUTES, ROUTE_POLICIES

    assert CONDITIONALLY_MOUNTED_ROUTES, "the set must not silently empty out"
    missing = CONDITIONALLY_MOUNTED_ROUTES - set(ROUTE_POLICIES)
    assert not missing, f"conditional routes with no declaration: {sorted(missing)}"
