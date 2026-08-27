"""The demo image's build-time hub rewrite, run against a real hub.

``Dockerfile.demo`` reduces the baked ``hub.db`` to the one library the demo
serves and re-points it at the in-container path. That step is a single
``python3 -c`` line inside a ``RUN``, so nothing but a full ``docker build``
exercises it - and the build machine's own hub is the only input it ever sees.
This test lifts the payload straight out of the Dockerfile (so its shell
quoting is covered too) and runs it against a hub built from the real schema.

The case that matters is a hub with more than one registered library:
``ux_library_path`` is UNIQUE, so a rewrite that touched every row would fail
the build outright, and shipping those rows would publish the build machine's
folder names on a public demo.
"""

from __future__ import annotations

import shlex
import sqlite3
from pathlib import Path

import pytest

from pixlstash.hub.schema import apply_migrations

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile.demo"
IN_CONTAINER_HUB = "/home/pixlstash/.config/pixlstash/hub.db"
IN_CONTAINER_ROOT = "/home/pixlstash/images"


def _rewrite_payload() -> str:
    """Return the python source of the hub rewrite step in Dockerfile.demo."""

    found = [
        line
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith('RUN python3 -c "import sqlite3;')
    ]
    assert len(found) == 1, f"expected one hub rewrite RUN, found {len(found)}"
    # shlex parses the RUN's shell quoting, so a broken quote fails here.
    return shlex.split(found[0][len("RUN ") :])[2]


def _run_rewrite(hub_path: Path, image_root: str) -> None:
    """Execute the Dockerfile's payload against *hub_path*."""

    code = _rewrite_payload().replace(IN_CONTAINER_HUB, str(hub_path))
    code = code.replace(f"'{IN_CONTAINER_ROOT}'", repr(image_root))
    exec(compile(code, str(DOCKERFILE), "exec"), {})  # noqa: S102


def _build_hub(hub_path: Path, libraries: list[tuple[str, str, int]]) -> None:
    """Write a hub at the current schema holding *libraries* and their tokens.

    Each library gets one READ and one ALL token, plus the ledger and migration
    rows that carry a path, so the test can prove every path is rewritten.
    """

    conn = sqlite3.connect(hub_path)
    try:
        apply_migrations(conn)
        conn.execute(
            "INSERT INTO user (id, username, password_hash, is_admin) "
            "VALUES (1, 'owner', 'placeholder-hash', 1)"
        )
        for index, (uuid, path, is_active) in enumerate(libraries, start=1):
            conn.execute(
                "INSERT INTO library (id, uuid, name, path, created_at, "
                "attached_at, is_active) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    index,
                    uuid,
                    f"Library {index}",
                    path,
                    "2026-01-01",
                    "2026-01-01",
                    is_active,
                ),
            )
            conn.execute(
                "INSERT INTO library_uuid_issued (uuid, issued_at, first_path) "
                "VALUES (?, ?, ?)",
                (uuid, "2026-01-01", path),
            )
            conn.execute(
                "INSERT INTO identity_migration_operation (library_uuid, "
                "source_path, payload_digest, state) VALUES (?, ?, ?, ?)",
                (uuid, path, "placeholder-digest", "complete"),
            )
            for scope in ("READ", "ALL"):
                conn.execute(
                    "INSERT INTO usertoken (public_id, user_id, library_uuid, "
                    "token_hash, scope, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"{uuid}-{scope}",
                        1,
                        uuid,
                        "placeholder-hash",
                        scope,
                        "2026-01-01",
                    ),
                )
        conn.execute(
            "INSERT INTO model_folder (id, path, kind, movable) "
            "VALUES (1, '/home/me/loras', 'adapter', 'no')"
        )
        conn.commit()
    finally:
        conn.close()


def test_rewrite_keeps_only_the_active_library(tmp_path):
    """A hub with a second library survives, reduced to the demo's own row."""

    hub = tmp_path / "hub.db"
    root = str(tmp_path / "images")
    _build_hub(
        hub,
        [("uuid-demo", "/home/me/demo", 1), ("uuid-other", "/home/me/other", 0)],
    )

    _run_rewrite(hub, root)

    conn = sqlite3.connect(hub)
    try:
        assert conn.execute("SELECT uuid, path FROM library").fetchall() == [
            ("uuid-demo", root)
        ]
        # The demo library keeps its READ token; the ALL token and the other
        # library's tokens are gone.
        assert conn.execute("SELECT library_uuid, scope FROM usertoken").fetchall() == [
            ("uuid-demo", "READ")
        ]
        ledger = dict(conn.execute("SELECT uuid, first_path FROM library_uuid_issued"))
        assert ledger == {"uuid-demo": root, "uuid-other": None}
        assert conn.execute(
            "SELECT library_uuid, source_path FROM identity_migration_operation"
        ).fetchall() == [("uuid-demo", root)]
        assert not conn.execute("SELECT count(*) FROM model_folder").fetchone()[0]
    finally:
        conn.close()


def test_rewrite_fails_without_a_read_token_for_the_demo_library(tmp_path):
    """No usable share link is a build failure, not a silently broken demo."""

    hub = tmp_path / "hub.db"
    _build_hub(hub, [("uuid-demo", "/home/me/demo", 1)])
    conn = sqlite3.connect(hub)
    try:
        conn.execute("DELETE FROM usertoken WHERE scope = 'READ'")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(AssertionError, match="no READ token"):
        _run_rewrite(hub, str(tmp_path / "images"))


def test_rewrite_fails_without_an_active_library(tmp_path):
    """Nothing to serve is also a build failure."""

    hub = tmp_path / "hub.db"
    _build_hub(hub, [("uuid-demo", "/home/me/demo", 0)])

    with pytest.raises(AssertionError, match="no active library"):
        _run_rewrite(hub, str(tmp_path / "images"))
