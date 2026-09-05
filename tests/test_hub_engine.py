"""Tests for the hub engine and for AuthService running against the hub.

The point of :class:`pixlstash.hub.engine.HubEngine` is that identity can move
out of the vault by re-pointing one constructor argument, so these tests assert
the real :class:`~pixlstash.auth.AuthService` works when handed one - that is
the claim the hub schema's shape exists to support.
"""

import os
import sqlite3
import stat
import threading
import types

import pytest
from sqlmodel import select

from pixlstash.auth import AuthService
from pixlstash.db_models import User
from pixlstash.db_models.user_token import UserToken
from pixlstash.hub.db import HubDatabase
from pixlstash.hub.db import (
    HubPermissionError,
    canonical_hub_path,
    check_file_mode,
)
from pixlstash.hub.engine import HubEngine
from pixlstash import trusted_sqlite
from pixlstash.trusted_sqlite import (
    TrustedSQLiteLocation,
    TrustedSQLiteLocationError,
    _is_private_group,
    _reject_symlinked_path,
    _validate_file,
)


def _warned(caplog, text: str) -> bool:
    """A WARNING record mentioning *text* was logged; mode bits warn, not refuse."""
    return any(
        text in record.message and record.levelname == "WARNING"
        for record in caplog.records
    )


@pytest.fixture
def hub_path(tmp_path):
    """A hub file that has been created and migrated to the current schema."""
    path = str(tmp_path / "hub.db")
    HubDatabase(path).close()
    return path


@pytest.fixture
def library_uuid(hub_path):
    """A registered library, so tokens have something to be stamped with."""
    import sqlite3

    value = "33333333-3333-4333-8333-333333333333"
    conn = sqlite3.connect(hub_path)
    try:
        conn.execute(
            "INSERT INTO library (uuid, name, path, created_at, attached_at, "
            "is_active) VALUES (?, 'Test', '/tmp/test-library', ?, ?, 1)",
            (value, "2026-08-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    return value


@pytest.fixture
def engine(hub_path):
    """A HubEngine over the temporary hub."""
    hub_engine = HubEngine(hub_path)
    yield hub_engine
    hub_engine.close()


@pytest.fixture
def auth(engine, tmp_path):
    """A real AuthService backed by the hub rather than by a vault."""
    return AuthService(
        engine,
        {"cookie_samesite": "Lax", "cookie_secure": False},
        str(tmp_path / "server-config.json"),
        __import__("logging").getLogger("test-auth"),
    )


class TestHubEngine:
    def test_run_task_returns_the_callables_result(self, engine):
        assert engine.run_task(lambda session: 42) == 42

    def test_run_task_propagates_the_callables_exception(self, engine):
        with pytest.raises(ValueError):

            def boom(_session):
                raise ValueError("no")

            engine.run_task(boom)

    def test_submit_task_returns_a_completed_future(self, engine):
        future = engine.submit_task(lambda session: "done")
        assert future.done()
        assert future.result() == "done"

    def test_submit_task_captures_the_exception_in_the_future(self, engine):
        def boom(_session):
            raise RuntimeError("nope")

        future = engine.submit_task(boom)
        assert future.done()
        with pytest.raises(RuntimeError):
            future.result()

    def test_done_callbacks_still_fire(self, engine):
        # auth.py refreshes ``last_used_at`` through submit_task().
        # add_done_callback(), so an inline future must still honour it.
        seen = []
        engine.submit_task(lambda session: None).add_done_callback(seen.append)
        assert len(seen) == 1

    def test_priority_argument_is_accepted_and_ignored(self, engine):
        from pixlstash.database import DBPriority

        assert engine.run_task(lambda s: 1, priority=DBPriority.IMMEDIATE) == 1

    def test_writes_are_visible_to_a_later_session(self, engine):
        engine.run_task(lambda session: _add_user(session, "alice"))
        found = engine.run_immediate_read_task(
            lambda session: session.exec(select(User)).first()
        )
        assert found.username == "alice"

    def test_a_failed_write_is_rolled_back(self, engine):
        def add_then_fail(session):
            _add_user(session, "bob", commit=False)
            raise RuntimeError("changed my mind")

        with pytest.raises(RuntimeError):
            engine.run_task(add_then_fail)

        remaining = engine.run_immediate_read_task(
            lambda session: session.exec(select(User)).all()
        )
        assert remaining == []

    def test_wal_is_enabled_on_pooled_connections(self, engine):
        from sqlalchemy import text

        with engine.engine.connect() as conn:
            mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        assert mode.lower() == "wal"


class TestHubFileSecurity:
    def test_first_sqlite_open_sees_an_already_private_file(
        self, tmp_path, monkeypatch
    ):
        path = str(tmp_path / "hub.db")
        real_connect = sqlite3.connect
        seen_modes = []

        def observing_connect(database, *args, **kwargs):
            if str(database) == path:
                seen_modes.append(stat.S_IMODE(os.lstat(path).st_mode))
            return real_connect(database, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", observing_connect)
        HubDatabase(path).close()

        assert seen_modes
        assert seen_modes[0] == 0o600

    def test_the_hub_opens_on_a_platform_without_fchmod(self, tmp_path, monkeypatch):
        """Windows has no ``os.fchmod``, and every backend test opens a hub.

        The unguarded call made `AttributeError: module 'os' has no attribute
        'fchmod'` the failure of every test in both Windows CI shards. Linux is
        the only place this can be caught before Windows CI runs, so the
        platform difference is simulated rather than waited for.
        """
        monkeypatch.delattr(os, "fchmod", raising=False)
        path = str(tmp_path / "hub.db")

        HubDatabase(path).close()

        # Where the bits mean something they must still be exactly 0600: the
        # mode handed to os.open() carries them when fchmod cannot.
        assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600

    def test_a_symlink_hub_is_refused_without_touching_its_target(self, tmp_path):
        target = tmp_path / "target.db"
        target.write_bytes(b"do not open")
        link = tmp_path / "hub.db"
        link.symlink_to(target)

        with pytest.raises(HubPermissionError, match="symlink"):
            HubDatabase(str(link))

        assert target.read_bytes() == b"do not open"

    def test_a_non_regular_hub_path_is_refused(self, tmp_path):
        path = tmp_path / "hub.db"
        path.mkdir()

        with pytest.raises(HubPermissionError, match="non-regular"):
            HubDatabase(str(path))

    def test_simultaneous_first_open_never_observes_a_creation_race(self, tmp_path):
        """Every concurrent opener must succeed, on every attempt.

        Four openers AND repeated trials. Both matter, and the second was the
        one that had been missing: the original two-thread single-trial version
        passed 20/20 while the defect was live, and so did a four-thread single
        trial. The window is only hit ~22% of the time, so a test that runs one
        trial is a coin flip, not a regression test. Twenty trials makes a live
        defect essentially certain to surface.

        The defect: verify_after_open compared the parent directory's
        mtime/ctime to a snapshot, and SQLite creating our own -wal/-shm moves
        both, so a second opener was refused as though the namespace had been
        tampered with.
        """
        openers = 4
        trials = 20
        failures = []

        for trial in range(trials):
            path = str(tmp_path / f"hub-{trial}.db")
            barrier = threading.Barrier(openers)

            def open_hub():
                try:
                    barrier.wait(timeout=10)
                    HubDatabase(path).close()
                except Exception as exc:  # pragma: no cover - asserted below
                    failures.append(f"trial {trial}: {type(exc).__name__}: {exc}")

            threads = [threading.Thread(target=open_hub) for _ in range(openers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)

            assert all(not thread.is_alive() for thread in threads)
            assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600

        assert failures == [], (
            f"{len(failures)}/{trials} trials refused a concurrent opener: "
            f"{failures[:3]}"
        )

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_a_lost_creation_race_is_logged_and_the_winner_still_opens(
        self, tmp_path, monkeypatch, caplog
    ):
        """Losing the create race to a legitimate file is loud, not silent.

        The file appears between the ``lexists`` probe and the ``O_EXCL``
        create. A self-owned 0600 winner must still open (the positive
        direction), and the lost race must leave a warning in the log rather
        than an ``except: pass``.
        """
        path = tmp_path / "hub.db"
        os.close(os.open(path, os.O_RDWR | os.O_CREAT, 0o600))
        monkeypatch.setattr(os.path, "lexists", lambda _p: False)

        with caplog.at_level("WARNING", logger="pixlstash.trusted_sqlite"):
            guard = TrustedSQLiteLocation.open(
                str(path), private=True, create=True, trusted_root=str(tmp_path)
            )
        guard.close()

        assert any(
            "Lost the creation race" in record.message for record in caplog.records
        )

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_a_hostile_file_that_wins_the_creation_race_is_still_reported(
        self, tmp_path, monkeypatch, caplog
    ):
        """The logged race branch must fall through to validation, not accept.

        A loose-mode file that won the race is exactly what the old silent
        ``pass`` could have hidden; ``_validate_file`` must still see it.
        """
        path = tmp_path / "hub.db"
        os.close(os.open(path, os.O_RDWR | os.O_CREAT, 0o644))
        monkeypatch.setattr(os.path, "lexists", lambda _p: False)

        TrustedSQLiteLocation.open(
            str(path), private=True, create=True, trusted_root=str(tmp_path)
        ).close()
        assert _warned(caplog, "mode 600")

    def test_path_replacement_during_sqlite_open_is_refused_before_schema_writes(
        self, tmp_path, monkeypatch
    ):
        path = str(tmp_path / "hub.db")
        real_connect = sqlite3.connect
        replaced = False

        def replacing_connect(database, *args, **kwargs):
            nonlocal replaced
            if not replaced and (str(database) == path or "/fd/" in str(database)):
                replaced = True
                os.unlink(path)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.write(fd, b"replacement")
                os.close(fd)
            return real_connect(database, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", replacing_connect)
        with pytest.raises(HubPermissionError, match="changed while"):
            HubDatabase(path)

        assert open(path, "rb").read() == b"replacement"

    def test_a_same_uid_swap_during_open_is_a_documented_non_goal(
        self, tmp_path, monkeypatch
    ):
        """Swap-away-and-back by the SAME uid is out of scope, on purpose.

        This replaces a test that asserted the swap WAS caught. It was caught,
        by comparing the parent directory's mtime/ctime, and that comparison
        also refused ~22% of concurrent opens because SQLite creating our own
        -wal/-shm is indistinguishable from tampering when you watch a
        directory's timestamps. The trade was withdrawn (2026-08-07, human
        decision after principal review) because the attacker it stopped is one
        the threat model excludes: these files are mode 600 owned by this uid,
        so a same-uid process can already read and rewrite them directly, with
        no race to win. See the module docstring of pixlstash/trusted_sqlite.py.

        The test is kept, inverted, so the boundary is asserted rather than
        merely written down: if someone reintroduces the timestamp comparison,
        this fails and points at the reasoning.
        """
        path = str(tmp_path / "hub.db")
        decoy = str(tmp_path / "decoy.db")
        held_original = str(tmp_path / "held-original.db")
        held_decoy = str(tmp_path / "held-decoy.db")
        for database_path, value in ((path, "original"), (decoy, "decoy")):
            hub = HubDatabase(database_path)
            with hub.transaction() as conn:
                conn.execute("CREATE TABLE connection_probe (value TEXT)")
                conn.execute("INSERT INTO connection_probe VALUES (?)", (value,))
            hub.close()

        real_connect = sqlite3.connect
        swapped = False

        def swapping_connect(database, *args, **kwargs):
            nonlocal swapped
            if not swapped and (str(database) == path or "/fd/" in str(database)):
                swapped = True
                os.rename(path, held_original)
                os.rename(decoy, path)
                connection = real_connect(database, *args, **kwargs)
                os.rename(path, held_decoy)
                os.rename(held_original, path)
                return connection
            return real_connect(database, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", swapping_connect)
        # Not raising is the documented outcome, not an oversight.
        HubDatabase(path).close()

        # Assert the swap actually ran. Without this the test passes vacuously
        # the moment the interception stops matching (a changed path, or
        # HubDatabase no longer routing through sqlite3.connect), and would then
        # assert nothing at all while still looking green.
        assert swapped, "the swap never fired; this test proved nothing"

    def test_a_directory_that_turns_writable_after_open_is_reported(
        self, tmp_path, caplog
    ):
        """The property that replaced the timestamp snapshot, asserted directly.

        verify_after_open now re-runs the ownership/permission check instead of
        comparing timestamps. That is strictly more than the old comparison
        managed: a chmod between open and verify used to be caught only
        incidentally, through the ctime it happened to bump. Here it is the
        thing being tested.
        """
        directory = tmp_path / "hub"
        directory.mkdir(mode=0o700)
        path = str(directory / "hub.db")
        guard = TrustedSQLiteLocation.open(
            path, private=True, create=True, trusted_root=str(directory)
        )
        try:
            guard.verify_after_open()  # clean while the directory is private
            assert not _warned(caplog, "world-writable")

            os.chmod(directory, 0o777)
            guard.verify_after_open()
            assert _warned(caplog, "world-writable")
        finally:
            os.chmod(directory, 0o700)
            guard.close()


class TestTrustedPathSymlinkCheck:
    """What "the caller's path goes through a symlink" is allowed to mean.

    The check used to be ``os.path.abspath(p) != os.path.realpath(p)``. On
    POSIX those differ only where a symlink was resolved, so it read correctly
    here - and wrongly on Windows, where ``realpath`` also expands 8.3 short
    names. ``C:\\Users\\RUNNER~1\\AppData\\Local\\Temp\\...`` was rejected as
    "contains a symlink", taking down every Windows test that opens a hub.

    Both directions, because the refusal is a security control: a genuinely
    symlinked path must still be refused, and a path that merely canonicalises
    to a different string must not be.
    """

    def test_a_symlinked_ancestor_is_still_refused(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir(mode=0o700)
        link = tmp_path / "via-link"
        link.symlink_to(real, target_is_directory=True)

        with pytest.raises(TrustedSQLiteLocationError, match="symlink"):
            TrustedSQLiteLocation.open(
                str(link / "hub.db"), private=True, create=True, trusted_root=str(link)
            )

    def test_a_symlinked_file_is_still_refused(self, tmp_path):
        directory = tmp_path / "hub"
        directory.mkdir(mode=0o700)
        target = directory / "real.db"
        target.touch(mode=0o600)
        link = directory / "hub.db"
        link.symlink_to(target)

        with pytest.raises(TrustedSQLiteLocationError, match="symlink"):
            TrustedSQLiteLocation.open(
                str(link), private=True, trusted_root=str(directory)
            )

    def test_a_windows_vault_outside_the_config_directory_is_allowed(
        self, tmp_path, monkeypatch
    ):
        """The vault's own call shape: not a credential store, so not gated.

        ``vault.py`` opens with ``private=False`` and no ``trusted_root``.
        Under the old blanket refusal that raised for *every* path on Windows,
        so no Windows install could open its library (W4). Pin the fix: a
        non-private open works anywhere the namespace checks allow.
        """
        directory = tmp_path / "library"
        directory.mkdir(mode=0o700)
        monkeypatch.setattr(os, "name", "nt")

        guard = TrustedSQLiteLocation.open(str(directory / "vault.db"), create=True)
        guard.close()

    def test_a_windows_hub_at_an_electron_style_path_opens_with_its_own_root(
        self, tmp_path, monkeypatch
    ):
        """The W6/W7/W18 outage regression.

        The desktop shell derives the hub path from ``--server-config`` under
        ``%APPDATA%\\pixlstash-desktop`` - never inside
        ``user_config_dir("pixlstash")`` - so the old DACL predicate refused
        every desktop install and the server could not start on Windows. A
        private open with ``trusted_root`` set to the hub's own parent must
        succeed regardless of where that parent is.
        """
        appdata = tmp_path / "AppData" / "Roaming" / "pixlstash-desktop"
        appdata.mkdir(parents=True, mode=0o700)
        monkeypatch.setattr(os, "name", "nt")

        guard = TrustedSQLiteLocation.open(
            str(appdata / "hub.db"),
            private=True,
            create=True,
            trusted_root=str(appdata),
        )
        guard.close()

    def test_a_private_open_without_a_trusted_root_cannot_be_forgotten(self, tmp_path):
        """W15: forgetting the argument fails the first test run, loudly.

        Every one of W4-W7 was a caller silently opting out of a keyword. The
        mandatory ``trusted_root`` turns that omission into an immediate
        ``TypeError`` instead of a production refusal months later.
        """
        with pytest.raises(TypeError, match="trusted_root"):
            TrustedSQLiteLocation.open(
                str(tmp_path / "hub.db"), private=True, create=True
            )

    def test_a_private_file_outside_its_trusted_root_is_refused(self, tmp_path):
        """The containment tautology holds in both directions.

        ``trusted_root`` is derived from the hub path's own parent, so in
        correct code the check always passes; a caller wiring it to the wrong
        directory is the only way to get here, and that is a bug worth failing
        closed on.
        """
        directory = tmp_path / "hub"
        directory.mkdir(mode=0o700)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir(mode=0o700)

        with pytest.raises(TrustedSQLiteLocationError, match="trusted root"):
            TrustedSQLiteLocation.open(
                str(directory / "hub.db"),
                private=True,
                create=True,
                trusted_root=str(elsewhere),
            )

    def test_a_windows_junction_is_refused_like_a_symlink(self, tmp_path, monkeypatch):
        """The regression an independent review caught in this very check.

        ``os.path.islink`` is False for a directory junction, so swapping the
        old ``abspath != realpath`` comparison for an islink walk quietly
        stopped refusing them. That is the wrong way round: a symlink on
        Windows needs ``SeCreateSymbolicLinkPrivilege``, a junction needs only
        write access to the directory, so the check was refusing the redirect
        an attacker cannot make and accepting the one they can.

        No junction can exist on Linux, so the reparse tag is simulated. A real
        ``mklink /J`` test belongs on the Windows lane; this at least fails on
        the Linux lane if the branch is deleted.
        """
        directory = tmp_path / "hub"
        directory.mkdir(mode=0o700)
        target = str(directory / "hub.db")
        mount_point_tag = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT

        real_lstat = os.lstat

        class _JunctionStat:
            def __init__(self, info):
                self._info = info
                self.st_reparse_tag = mount_point_tag

            def __getattr__(self, name):
                return getattr(self._info, name)

        monkeypatch.setattr(os, "name", "nt")
        # raising=False: the constant is Windows-only, so it does not exist to
        # be replaced on the machine running this test.
        monkeypatch.setattr(
            stat, "IO_REPARSE_TAG_MOUNT_POINT", mount_point_tag, raising=False
        )
        monkeypatch.setattr(
            os,
            "lstat",
            lambda p, *a, **k: (
                _JunctionStat(real_lstat(p, *a, **k))
                if str(p) == str(directory)
                else real_lstat(p, *a, **k)
            ),
        )

        with pytest.raises(TrustedSQLiteLocationError, match="junction"):
            _reject_symlinked_path(target)

    def test_the_verdict_never_consults_realpath(self, tmp_path, monkeypatch):
        """No 8.3 name exists on Linux, so assert the cause, not the symptom.

        What made the old check wrong on Windows is that it asked ``realpath``
        a question realpath does not answer: "did this path cross a symlink?"
        Nothing on POSIX can produce a short name to reproduce that with, so
        this pins the property instead - the decision does not depend on the
        canonical spelling at all. ``realpath`` is made to explode; a path with
        no symlink in it must still be accepted.
        """
        directory = tmp_path / "hub"
        directory.mkdir(mode=0o700)

        def explode(*_args, **_kwargs):
            raise AssertionError("the symlink verdict must not consult realpath")

        monkeypatch.setattr(os.path, "realpath", explode)

        _reject_symlinked_path(str(directory / "hub.db"))


class TestGroupWritableDirectories:
    """Group-write is an exposure only when the group has another member.

    The blanket ``mode & 0o022`` test exited the server 1 at startup on a stock
    Linux box: Debian/Ubuntu give every account a same-named group of its own
    and default to umask 002, so every directory PixlStash created before it
    started passing 0700 is 0775 - group-writable by a group of exactly one.

    ``grp``/``pwd`` are patched rather than read, in both directions, because
    the real answer depends on the machine - a developer box's group is named
    after the login, a CI runner's need not be - so an unpatched test asserting
    a *value* would assert whatever the box happened to be configured with. Two
    tests at the end do call the real lookups, for what is machine-independent:
    that the production path resolves real ``pwd``/``grp`` records without an
    attribute error, and that an unresolvable id fails closed.
    """

    @staticmethod
    def _patch_groups(monkeypatch, *, group_name, members):
        """Report this user as ``me`` in a group of the given shape.

        uid 0 keeps its real name. A fake that answered ``me`` for *every* uid
        would make the root-owned case fail on a name mismatch rather than on the
        precondition it exists to pin, which is a test that cannot detect its own
        subject going missing.
        """
        monkeypatch.setattr(
            trusted_sqlite,
            "pwd",
            types.SimpleNamespace(
                getpwuid=lambda uid: types.SimpleNamespace(
                    pw_name="root" if uid == 0 else "me"
                )
            ),
        )
        monkeypatch.setattr(
            trusted_sqlite,
            "grp",
            types.SimpleNamespace(
                getgrgid=lambda _gid: types.SimpleNamespace(
                    gr_name=group_name, gr_mem=members
                )
            ),
        )

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_a_private_group_makes_group_write_harmless(
        self, tmp_path, monkeypatch, caplog
    ):
        directory = tmp_path / "images"
        directory.mkdir()
        os.chmod(tmp_path, 0o755)
        os.chmod(directory, 0o775)
        self._patch_groups(monkeypatch, group_name="me", members=["me"])

        TrustedSQLiteLocation.open(str(directory / "vault.db"), create=True).close()
        assert not _warned(caplog, "group/world-writable")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_a_shared_group_still_warns_about_group_write(
        self, tmp_path, monkeypatch, caplog
    ):
        directory = tmp_path / "images"
        directory.mkdir()
        os.chmod(directory, 0o775)
        self._patch_groups(monkeypatch, group_name="staff", members=["me", "someone"])

        TrustedSQLiteLocation.open(str(directory / "vault.db"), create=True).close()
        assert _warned(caplog, "group/world-writable")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_a_same_named_group_with_another_member_is_not_private(
        self, tmp_path, monkeypatch, caplog
    ):
        """The name alone is not the property; a group of one is."""
        directory = tmp_path / "images"
        directory.mkdir()
        os.chmod(directory, 0o775)
        self._patch_groups(monkeypatch, group_name="me", members=["me", "someone"])

        TrustedSQLiteLocation.open(str(directory / "vault.db"), create=True).close()
        assert _warned(caplog, "group/world-writable")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_world_write_warns_whatever_the_group_is(
        self, tmp_path, monkeypatch, caplog
    ):
        directory = tmp_path / "images"
        directory.mkdir()
        os.chmod(directory, 0o777)
        self._patch_groups(monkeypatch, group_name="me", members=["me"])

        TrustedSQLiteLocation.open(str(directory / "vault.db"), create=True).close()
        assert _warned(caplog, "group/world-writable")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_a_differently_named_group_of_one_is_not_private(
        self, tmp_path, monkeypatch, caplog
    ):
        """The member count alone is not the property; the name carries half of it.

        ``gr_mem`` is empty for *every* private group, so "no other member" is
        the default state of a group rather than evidence about it - a shared
        group with only primary members looks identical. The name is what makes
        this the owner's own group, and without this case that half of the
        predicate could be deleted with the suite still green.
        """
        directory = tmp_path / "images"
        directory.mkdir()
        os.chmod(directory, 0o775)
        self._patch_groups(monkeypatch, group_name="staff", members=[])

        TrustedSQLiteLocation.open(str(directory / "vault.db"), create=True).close()
        assert _warned(caplog, "group/world-writable")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="running as root makes root-owned the owner's own directory",
    )
    def test_a_root_owned_group_writable_ancestor_still_warns(
        self, tmp_path, monkeypatch, caplog
    ):
        """gid 0 is the administrators' group, not one owner's own.

        The ownership check admits an ancestor owned by root, and ``root:root
        0775`` directories exist on a stock Ubuntu box
        (``/var/lib/AccountsService``). Those are already refused by the name
        comparison, since the helper is asked about *this* user's name and not
        the directory's.

        So the case pinned here is the one only the precondition refuses:
        ``chown root:<this user's group>`` plus g+w, where the group name does
        match and both halves of ``_is_private_group`` say "private". This
        process stays its ordinary self; only the directory is reported as
        root-owned, which leaves the precondition as the sole reason to refuse.
        """
        directory = tmp_path / "images"
        directory.mkdir()
        os.chmod(directory, 0o775)
        real_lstat = os.lstat
        # Identity, not a path comparison: `realpath` itself calls `os.lstat`,
        # so matching on the resolved string recurses into the patch.
        target = (real_lstat(directory).st_dev, real_lstat(directory).st_ino)

        def as_root(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if (info.st_dev, info.st_ino) != target:
                return info
            fields = list(info)
            fields[4] = 0  # st_uid: root's, while the group stays this user's
            return os.stat_result(fields)

        monkeypatch.setattr(os, "lstat", as_root)
        self._patch_groups(monkeypatch, group_name="me", members=["me"])

        TrustedSQLiteLocation.open(str(directory / "vault.db"), create=True).close()
        assert _warned(caplog, "group/world-writable")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="running as root makes gid 0 this user's own group",
    )
    def test_the_real_lookups_resolve_a_real_pwd_and_grp_record(self):
        """One call with nothing patched, to pin the field names.

        Every other test here replaces ``grp`` and ``pwd`` with stand-ins written
        to match the code, so ``pw_name``/``gr_name``/``gr_mem`` would go on
        agreeing with a typo. Asking about gid 0 as a non-root user has a
        machine-independent answer - this login is not called ``root`` - while
        still running the production lookups end to end.
        """
        assert _is_private_group(os.geteuid(), 0) is False

    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_the_real_lookups_fail_closed_on_an_unresolvable_id(self):
        """An id no name service knows must read as shared, not as private."""
        assert _is_private_group(os.geteuid(), 0x7FFFFFFE) is False

    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_the_real_lookups_fail_closed_on_an_out_of_range_id(self):
        """Out of range is a different exception, and the two modules disagree.

        ``grp.getgrgid`` raises ``OverflowError`` past the end of ``gid_t``,
        where the unresolvable id above raises ``KeyError`` and where
        ``pwd.getpwuid`` raises ``KeyError`` for the same value. Unreachable from
        ``_require_owned_directory``, whose gid comes from the kernel - but the
        helper promises to fail closed on *any* lookup failure, and an escaping
        ``OverflowError`` would make that a startup crash instead.
        """
        assert _is_private_group(os.geteuid(), 2**32) is False
        assert _is_private_group(os.geteuid(), -2) is False

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
    def test_an_unresolvable_group_fails_closed(self, tmp_path, monkeypatch, caplog):
        """A lookup that did not work must not be read as "nobody else"."""
        directory = tmp_path / "images"
        directory.mkdir()
        os.chmod(directory, 0o775)

        def explode(_gid):
            raise KeyError("no such gid")

        monkeypatch.setattr(
            trusted_sqlite,
            "pwd",
            types.SimpleNamespace(
                getpwuid=lambda _uid: types.SimpleNamespace(pw_name="me")
            ),
        )
        monkeypatch.setattr(
            trusted_sqlite, "grp", types.SimpleNamespace(getgrgid=explode)
        )

        TrustedSQLiteLocation.open(str(directory / "vault.db"), create=True).close()
        assert _warned(caplog, "group/world-writable")


class TestWindowsHasNoModeBits:
    """POSIX permission assertions must not run where they cannot hold.

    Windows synthesises ``st_mode`` from the read-only attribute alone, so an
    ordinary file reads 0o666 and no chmod can make it 0o600. Asserting the
    mode there refuses *every* hub, including one this process created moments
    earlier - which is how both Windows shards failed with "SQLite credential
    file ... must be mode 600" on a freshly created file.

    Both directions, because these are credential-file checks: they must go on
    holding on POSIX, where the bits are real.
    """

    def test_validate_file_accepts_the_synthetic_windows_mode(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "hub.db"
        path.touch()
        os.chmod(path, 0o666)
        monkeypatch.setattr(os, "name", "nt")

        _validate_file(str(path), private=True)

    def test_validate_file_still_reports_that_mode_on_posix(self, tmp_path, caplog):
        path = tmp_path / "hub.db"
        path.touch()
        os.chmod(path, 0o666)

        _validate_file(str(path), private=True)
        assert _warned(caplog, "mode 600")

    def test_check_file_mode_is_a_no_op_on_windows(self, tmp_path, monkeypatch):
        path = tmp_path / "hub.db"
        path.touch()
        os.chmod(path, 0o666)
        monkeypatch.setattr(os, "name", "nt")

        check_file_mode(str(path), repair=False)

    def test_check_file_mode_still_reports_a_loose_hub_on_posix(self, tmp_path, caplog):
        path = tmp_path / "hub.db"
        path.touch()
        os.chmod(path, 0o666)

        check_file_mode(str(path), repair=False)
        assert _warned(caplog, "should be 600")
        assert stat.S_IMODE(os.lstat(path).st_mode) == 0o666


class TestHostileSidecarRefusal:
    """W8: a pre-positioned ``-wal``/``-shm``/``-journal`` must be refused.

    One tolerance, pinned by ``test_a_sidecar_that_vanishes_mid_validation…``:
    a sidecar that disappears between the existence probe and its ``lstat`` is
    a concurrent opener's transient journal, not an attack, and must not
    refuse the open (it failed 1-in-20 four-opener races in CI before the
    tolerance existed). A hostile sidecar that still exists is refused
    exactly as the rest of this class asserts.

    An attacker with directory write never touches ``hub.db``: they write
    ``hub.db-wal``. SQLite replays that WAL on open and it overrides arbitrary
    pages of the main database. The main file's ``(st_dev, st_ino)`` is
    unchanged, no symlink is involved, and ``S_ISREG`` is true on both files,
    so the identity and redirection checks all pass. The only refusal is the
    sidecar loop in :meth:`TrustedSQLiteLocation.open`, which runs
    ``_validate_file`` on every existing sidecar.

    Two traps these tests are built to avoid, per the signed plan:

    * The refusal must come from ``TrustedSQLiteLocation.open()``, BEFORE any
      ``sqlite3.connect``: ``HubDatabase`` calls ``verify_after_open()`` only
      *after* connecting, when SQLite has already replayed the hostile WAL.
    * The directory must be self-owned and 0700, and the hostility must live
      in the sidecar's own attributes. A loose *directory* is refused by
      ``_validate_namespace`` first, so the naive test goes green off the
      wrong control and proves nothing about the sidecar check. For the same
      reason the foreign-uid case fakes the sidecar's ``st_uid`` rather than
      monkeypatching ``os.geteuid``: a faked geteuid flips the directory- and
      main-file ownership checks first, and the test would keep passing with
      the sidecar loop deleted.

    RESIDUE: Windows, where this attack matters most, is NOT covered here
    and cannot be: ``_validate_file`` returns unconditionally on ``nt``
    because ``st_mode``/``st_uid`` are synthesised there (W2), so under the
    suite's ``nt`` simulation there is no sidecar check to exercise or to
    break. The last test below asserts that acceptance so the gap stays
    visible. Closing it requires the native DACL verifier (plan item 3c,
    ctypes ``GetNamedSecurityInfoW``); tracked in the W17 accepted-risk
    record, owner lindkvis, revisit 2026-11-08.
    """

    @staticmethod
    def _hub_with_sidecar(tmp_path, suffix, mode):
        """A migrated hub in a self-owned 0700 directory, plus one sidecar."""
        directory = tmp_path / "hub"
        directory.mkdir(mode=0o700)
        path = str(directory / "hub.db")
        HubDatabase(path).close()
        # Drop whatever sidecars SQLite left behind so the one under test is
        # the only one present.
        for leftover in trusted_sqlite._SIDECAR_SUFFIXES:
            if os.path.lexists(path + leftover):
                os.remove(path + leftover)
        sidecar = path + suffix
        with open(sidecar, "wb") as handle:
            handle.write(b"hostile frames")
        os.chmod(sidecar, mode)
        return path, sidecar

    @pytest.mark.parametrize("suffix", trusted_sqlite._SIDECAR_SUFFIXES)
    def test_a_loose_mode_sidecar_is_reported_by_the_guard(
        self, tmp_path, suffix, caplog
    ):
        """Mode 0o666 on the sidecar alone is warned about, naming the sidecar.

        The warning must name the sidecar: one naming the directory or the
        main file would mean a different check fired and this one is silent.
        """
        path, sidecar = self._hub_with_sidecar(tmp_path, suffix, 0o666)

        TrustedSQLiteLocation.open(
            path, private=True, trusted_root=os.path.dirname(path)
        ).close()
        assert _warned(caplog, sidecar)

    @pytest.mark.parametrize("suffix", trusted_sqlite._SIDECAR_SUFFIXES)
    def test_a_foreign_owned_sidecar_is_refused(self, tmp_path, monkeypatch, suffix):
        """A 0600 sidecar owned by someone else must still be refused.

        The foreign uid is faked on the sidecar's ``lstat`` result (the
        attacker's artifact), not via ``os.geteuid``, for the reason in the
        class docstring.
        """
        path, sidecar = self._hub_with_sidecar(tmp_path, suffix, 0o600)
        real_lstat = os.lstat

        class _ForeignOwnerStat:
            def __init__(self, info):
                self._info = info
                self.st_uid = info.st_uid + 1

            def __getattr__(self, name):
                return getattr(self._info, name)

        monkeypatch.setattr(
            os,
            "lstat",
            lambda p, *a, **k: (
                _ForeignOwnerStat(real_lstat(p, *a, **k))
                if str(p) == sidecar
                else real_lstat(p, *a, **k)
            ),
        )

        with pytest.raises(TrustedSQLiteLocationError, match="not this user"):
            TrustedSQLiteLocation.open(
                path, private=True, trusted_root=os.path.dirname(path)
            )

    @pytest.mark.parametrize("suffix", trusted_sqlite._SIDECAR_SUFFIXES)
    def test_a_self_owned_0600_sidecar_still_opens(self, tmp_path, suffix):
        """The other direction: refusing our own sidecars breaks every WAL DB."""
        path, _sidecar = self._hub_with_sidecar(tmp_path, suffix, 0o600)

        TrustedSQLiteLocation.open(
            path, private=True, trusted_root=os.path.dirname(path)
        ).close()

    def test_sqlites_own_live_sidecars_still_open_end_to_end(self, tmp_path):
        """With a live WAL connection holding real ``-wal``/``-shm`` files, a
        guard open and a full second HubDatabase open must both succeed."""
        directory = tmp_path / "hub"
        directory.mkdir(mode=0o700)
        path = str(directory / "hub.db")
        first = HubDatabase(path)
        try:
            assert os.path.exists(path + "-wal")

            TrustedSQLiteLocation.open(
                path, private=True, trusted_root=os.path.dirname(path)
            ).close()
            HubDatabase(path).close()
        finally:
            first.close()

    def test_windows_accepts_the_sidecar_it_cannot_judge(self, tmp_path, monkeypatch):
        """On ``nt`` a hostile-looking sidecar is ACCEPTED, asserted rather than hidden.

        ``st_mode`` there is synthesised (0o666 for any writable file) and
        ``st_uid`` is meaningless, so ``_validate_file`` returns before either
        check (W2). This pins the residue described in the class docstring: if
        someone adds a real Windows sidecar check (plan item 3c), this test
        fails and gets replaced by real coverage.
        """
        sidecar = tmp_path / "hub.db-wal"
        sidecar.write_bytes(b"hostile frames")
        os.chmod(sidecar, 0o666)
        monkeypatch.setattr(os, "name", "nt")

        _validate_file(str(sidecar), private=True)

    def test_a_sidecar_that_vanishes_mid_validation_does_not_refuse_the_open(
        self, tmp_path, monkeypatch
    ):
        """The 1-in-20 CI race, made deterministic.

        A concurrent opener's SQLite creates and deletes a transient
        ``-journal`` during first migration. When it vanished between the
        ``lexists`` probe and ``_validate_file``'s ``lstat``, the resulting
        FileNotFoundError was wrapped as "Could not inspect" and the healthy
        opener was refused - concurrency mistaken for tampering, the same
        class the creation-race test pins. Simulated here by making the probe
        claim the journal exists when it does not.
        """
        directory = tmp_path / "hub"
        directory.mkdir(mode=0o700)
        path = str(directory / "hub.db")
        journal = path + "-journal"
        real_lexists = os.path.lexists

        monkeypatch.setattr(
            os.path,
            "lexists",
            lambda p: True if str(p) == journal else real_lexists(p),
        )

        guard = TrustedSQLiteLocation.open(
            path, private=True, create=True, trusted_root=str(directory)
        )
        guard.close()


class TestAuthServiceOnTheHub:
    def test_ensure_user_creates_the_owner_in_the_hub(self, auth, engine):
        user = auth.ensure_user()

        assert user is not None
        stored = engine.run_immediate_read_task(
            lambda session: session.exec(select(User)).all()
        )
        assert len(stored) == 1
        assert stored[0].id == user.id

    def test_ensure_user_is_idempotent(self, auth):
        first = auth.ensure_user()
        second = auth.ensure_user()
        assert first.id == second.id

    def test_the_owner_row_lands_in_the_hub_file_not_a_vault(self, auth, hub_path):
        auth.ensure_user()

        import sqlite3

        conn = sqlite3.connect(hub_path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM user").fetchone()[0]
        finally:
            conn.close()
        assert count == 1

    def test_credentials_persist_in_the_hub(self, auth, hub_path):
        from passlib.hash import bcrypt

        auth.ensure_user()
        auth.set_username("owner")
        auth.set_password_hash(bcrypt.hash("correct horse battery staple"))

        import sqlite3

        conn = sqlite3.connect(hub_path)
        try:
            username, password_hash = conn.execute(
                "SELECT username, password_hash FROM user"
            ).fetchone()
        finally:
            conn.close()

        assert username == "owner"
        assert bcrypt.verify("correct horse battery staple", password_hash)

    def test_a_token_stored_in_the_hub_resolves_back_through_auth(
        self, auth, engine, library_uuid
    ):
        """The token lookup path (prefix fetch + bcrypt verify) on hub storage."""
        from passlib.hash import bcrypt

        user = auth.ensure_user()
        token_value = "abcdef0123456789abcdef0123456789"

        def add_token(session):
            session.add(
                UserToken(
                    user_id=user.id,
                    library_uuid=library_uuid,
                    token_hash=bcrypt.hash(token_value),
                    token_prefix=token_value[:8],
                    created_at=__import__("datetime").datetime.utcnow(),
                    description="test token",
                    scope="ALL",
                )
            )
            session.commit()

        engine.run_task(add_token)

        resolved = auth.token_from_value(token_value)
        assert resolved is not None
        assert resolved.description == "test token"
        assert auth.token_from_value("not-the-right-token") is None

    def test_a_token_without_a_library_is_rejected_by_the_database(self, auth, engine):
        """There is no such thing as an unpinned token.

        The hub column is NOT NULL, so a code path that forgets to stamp a
        token fails loudly at write time instead of quietly minting one that
        would change what it grants whenever the owner switched library.
        """
        import sqlalchemy.exc
        from passlib.hash import bcrypt

        user = auth.ensure_user()

        def add_unstamped_token(session):
            session.add(
                UserToken(
                    user_id=user.id,
                    token_hash=bcrypt.hash("x" * 32),
                    token_prefix="xxxxxxxx",
                    created_at=__import__("datetime").datetime.utcnow(),
                    scope="ALL",
                )
            )
            session.commit()

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            engine.run_task(add_unstamped_token)

    def test_a_token_cannot_name_a_library_that_does_not_exist(self, auth, engine):
        """The foreign key, so a stamp always resolves to a real library."""
        import sqlalchemy.exc
        from passlib.hash import bcrypt

        user = auth.ensure_user()

        def add_token(session):
            session.add(
                UserToken(
                    user_id=user.id,
                    library_uuid="00000000-0000-4000-8000-000000000000",
                    token_hash=bcrypt.hash("x" * 32),
                    token_prefix="xxxxxxxx",
                    created_at=__import__("datetime").datetime.utcnow(),
                    scope="ALL",
                )
            )
            session.commit()

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            engine.run_task(add_token)


def _add_user(session, username, *, commit=True):
    """Insert a user row through the shared SQLModel model."""
    user = User(username=username)
    session.add(user)
    if commit:
        session.commit()
        session.refresh(user)
    return user


def test_hub_schema_carries_every_column_the_user_model_declares(hub_path):
    """The shared-model contract, asserted rather than assumed.

    If a column is added to ``User`` without being added to the hub schema,
    every ``SELECT user.*`` against the hub breaks at runtime. This fails at
    the moment the model changes instead.
    """
    import sqlite3

    conn = sqlite3.connect(hub_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info('user')")}
    finally:
        conn.close()

    declared = set(User.model_fields) - {"tokens"}
    assert declared <= columns, f"hub `user` is missing: {sorted(declared - columns)}"


def test_hub_schema_carries_every_column_the_token_model_declares(hub_path):
    """Same contract for ``UserToken``, plus the hub-only ``library_id``."""
    import sqlite3

    conn = sqlite3.connect(hub_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info('usertoken')")}
    finally:
        conn.close()

    declared = set(UserToken.model_fields) - {"user"}
    assert declared <= columns, (
        f"hub `usertoken` is missing: {sorted(declared - columns)}"
    )
    assert "library_uuid" in columns


def test_hub_file_is_the_only_database_touched(hub_path, tmp_path):
    """Opening the hub must not create a vault next to it."""
    HubEngine(hub_path).close()
    assert not os.path.exists(os.path.join(str(tmp_path), "vault.db"))


def test_transaction_is_already_open_before_its_first_write(hub_path):
    """A gate read inside ``transaction()`` must already be in the transaction.

    ``with self._conn`` commits on exit but never begins, and the connection is
    opened ``isolation_level=""``, which defers ``BEGIN`` to the first *DML*
    statement. So a block that read before it wrote ran its read in autocommit
    and only became a transaction at the write. Callers that gate a write on a
    preceding ``SELECT`` -- ``forget_models``, ``apply_stack`` -- documented a
    critical section they did not have.

    **This proves the transaction is OPEN, and deliberately not that it is
    ``IMMEDIATE``.** ``in_transaction`` is equally true of a plain deferred
    ``BEGIN``, which acquires no write lock at all, so the name must not claim
    one. Measured, not assumed: mutating ``BEGIN IMMEDIATE`` to ``BEGIN`` leaves
    this test green and turns only
    :func:`test_another_process_cannot_write_between_a_gate_read_and_its_write`
    red. That test owns the lock half; this one owns the "not in autocommit"
    half, and both are needed because either mutant alone survives the other.
    """
    hub = HubDatabase(hub_path)
    try:
        with hub.transaction() as conn:
            conn.execute("SELECT COUNT(*) FROM model").fetchone()
            assert conn.in_transaction, (
                "the gate read ran in autocommit -- another process can commit "
                "between it and the write it is gating"
            )
    finally:
        hub.close()


def test_another_process_cannot_write_between_a_gate_read_and_its_write(hub_path):
    """The window this closes is cross-process, so prove it with a real one.

    ``self._lock`` is a ``threading`` lock, and the module docstring is explicit
    that the server and the ``pixlstash.libraries`` CLI open this file at the
    same moment. In WAL an autocommit read takes no lock whatsoever, so the
    second connection here could previously acquire the write lock *while the
    first was between its gate read and its DELETE* -- which is the race in
    exactly the shape a separate process runs it.

    The second connection asks for the write lock directly rather than writing a
    row, so the assertion does not depend on any table's columns.
    """
    hub = HubDatabase(hub_path)
    other = sqlite3.connect(hub_path, isolation_level="")
    other.execute("PRAGMA busy_timeout=100")  # do not wait out the hub's 5 s
    try:
        with hub.transaction() as conn:
            conn.execute("SELECT COUNT(*) FROM model").fetchone()
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
    finally:
        other.close()
        hub.close()


def test_transaction_survives_an_already_open_transaction(hub_path):
    """``BEGIN IMMEDIATE`` must not fire when one is already open.

    SQLite has no nested transactions, so an unguarded second ``BEGIN`` raises
    "cannot start a transaction within a transaction". This is reachable without
    any nesting in ``pixlstash``'s own callers: :attr:`HubDatabase.connection`
    hands the raw connection out, and a caller that runs DML on it leaves a
    transaction open behind ``transaction()``'s back. Several tests in
    ``test_hub_registry.py`` do exactly that.

    The guard skips only the ``BEGIN``. ``with self._conn`` still owns the
    commit, so the write below lands just as it did before the fix.
    """
    hub = HubDatabase(hub_path)
    try:
        # Leave a transaction open the way `hub.connection` lets a caller do.
        hub.connection.execute(
            "INSERT INTO model_folder (path, kind, movable) VALUES (?, ?, ?)",
            ("/left/open", "lora", "no"),
        )
        assert hub.connection.in_transaction, "precondition: DML opened one"

        with hub.transaction() as conn:  # must not raise
            conn.execute(
                "INSERT INTO model_folder (path, kind, movable) VALUES (?, ?, ?)",
                ("/written/inside", "lora", "no"),
            )
    finally:
        hub.close()

    survivor = sqlite3.connect(hub_path)
    try:
        paths = {row[0] for row in survivor.execute("SELECT path FROM model_folder")}
    finally:
        survivor.close()
    assert "/written/inside" in paths, "the block's own write must still commit"


class TestHubUnderASymlinkedAncestor:
    """A symlinked config directory must not stop the server starting.

    v1.10.0 introduced both the hub and ``trusted_sqlite``'s guard. The guard
    refuses a *caller-supplied* path that crosses a symlink, and the hub's path
    - unlike a library's, which ``registry.resolve_path`` has always canonicalised
    at attach - was handed over exactly as derived from the config directory.
    A stow/chezmoi-managed ``~/.config``, a ``$HOME`` symlinked onto another
    disk, or a macOS path crossing ``/var`` -> ``/private/var`` therefore
    bricked startup with no route back, since ``startup_permissions`` mirrors
    the guard over ``realpath`` and cannot see a redirect that exists only
    before resolution.

    Both directions, because the ancestor relaxation must not cost the leaf
    refusal: an ancestor link is followed, a link standing at ``hub.db``
    itself is still refused.
    """

    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_a_hub_under_a_symlinked_ancestor_opens(self, tmp_path):
        real = tmp_path / "real-config"
        real.mkdir(mode=0o700)
        link = tmp_path / "config"
        link.symlink_to(real, target_is_directory=True)

        HubDatabase(str(link / "hub.db")).close()

        # The bytes must land at the canonical location, opened once, rather
        # than at a second file reached through the link.
        assert (real / "hub.db").is_file()
        assert not (real / "hub.db").is_symlink()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_the_opened_path_is_canonical(self, tmp_path):
        real = tmp_path / "real-config"
        real.mkdir(mode=0o700)
        link = tmp_path / "config"
        link.symlink_to(real, target_is_directory=True)

        # The pool, the sidecars and `os.replace` all reopen by this string
        # over the process lifetime; if it still held the link they would
        # re-resolve it on every use.
        with HubDatabase(str(link / "hub.db")) as hub:
            assert hub.path == str(real / "hub.db")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_a_symlinked_leaf_under_a_symlinked_ancestor_is_still_refused(
        self, tmp_path
    ):
        real = tmp_path / "real-config"
        real.mkdir(mode=0o700)
        link = tmp_path / "config"
        link.symlink_to(real, target_is_directory=True)
        target = real / "elsewhere.db"
        target.write_bytes(b"do not open")
        (real / "hub.db").symlink_to(target)

        with pytest.raises(HubPermissionError, match="symlink"):
            HubDatabase(str(link / "hub.db"))

        assert target.read_bytes() == b"do not open"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_a_symlink_to_an_exposed_directory_is_still_reported(
        self, tmp_path, caplog
    ):
        """Resolving the ancestor must not smuggle past the namespace walk.

        The link is resolved, and then the directory it lands on is judged on
        its own ownership and mode.
        """
        exposed = tmp_path / "exposed"
        exposed.mkdir()
        # chmod, not mkdir(mode=...): the process umask is commonly 0o022, which
        # would silently make this 0o755 and leave nothing for the guard to
        # report - the assertion would then pass for the wrong reason.
        os.chmod(exposed, 0o777)
        link = tmp_path / "config"
        link.symlink_to(exposed, target_is_directory=True)

        HubDatabase(str(link / "hub.db")).close()
        assert _warned(caplog, str(exposed))


class TestCanonicalHubPath:
    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_ancestors_are_resolved_and_the_leaf_is_not(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir(mode=0o700)
        link = tmp_path / "via-link"
        link.symlink_to(real, target_is_directory=True)
        leaf = real / "hub.db"
        leaf.symlink_to(real / "target.db")

        resolved = canonical_hub_path(str(link / "hub.db"))

        assert resolved == str(real / "hub.db")
        # Preserved as a link so `_reject_symlinked_path` still gets to refuse
        # it; `os.path.realpath` over the whole path would have followed it.
        assert os.path.islink(resolved)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX only")
    def test_a_path_without_links_is_unchanged(self, tmp_path):
        plain = str(tmp_path / "hub.db")

        assert canonical_hub_path(plain) == os.path.realpath(plain)

    @pytest.mark.skipif(os.name == "nt", reason="creates a symlink to simulate nt")
    def test_on_windows_a_symlinked_ancestor_is_not_resolved(
        self, tmp_path, monkeypatch
    ):
        """The Windows carve-out that keeps ``_reject_symlinked_path`` intact.

        On ``nt`` resolving the ancestors would defeat the redirect refusal, one
        of the only controls left there (W19, ``docs/backend_architecture.md``
        section 13). A real symlink is made on this POSIX gate, ``os.name`` is
        forced to ``nt``, and the ancestor must come back *unresolved* - the
        symlink still present - so the leaf-and-ancestor walk in
        ``_reject_symlinked_path`` still gets to see and refuse it. Without the
        guard this resolves the link and the assertion fails.
        """
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "via-link"
        link.symlink_to(real, target_is_directory=True)

        monkeypatch.setattr("pixlstash.hub.db.os.name", "nt")

        resolved = canonical_hub_path(str(link / "hub.db"))

        assert resolved == str(link / "hub.db")
        assert os.path.islink(os.path.dirname(resolved))
