from __future__ import annotations

import io
import logging
import os
import stat
from types import SimpleNamespace

import pytest

from pixlstash import app
from pixlstash.pixl_logging import hold_log_output
import pixlstash.startup_permissions as startup_permissions


pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")


class TerminalInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def loose_config(tmp_path):
    config_dir = tmp_path / "config"
    library = config_dir / "images"
    config_dir.mkdir(mode=0o700)
    library.mkdir(mode=0o700)
    os.chmod(config_dir, 0o775)
    os.chmod(library, 0o775)
    return config_dir / "server-config.json", library


@pytest.fixture(autouse=True)
def treat_test_config_as_app_owned(monkeypatch, tmp_path):
    monkeypatch.setattr(
        startup_permissions,
        "_app_owned_config_directories",
        lambda: {os.path.realpath(tmp_path / "config")},
    )


def test_terminal_default_yes_repairs_and_continues(tmp_path, monkeypatch, capsys):
    config_path, library = loose_config(tmp_path)
    monkeypatch.delenv("PIXLSTASH_INSTALL_TYPE", raising=False)
    monkeypatch.delenv("PIXLSTASH_REPAIR_PERMISSIONS", raising=False)
    monkeypatch.setattr(app.sys, "stdin", TerminalInput())
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    app._prepare_startup_permissions(str(config_path), {"image_root": str(library)})
    assert mode(config_path.parent) == 0o700
    assert mode(library) == 0o755
    assert "Permissions fixed" in capsys.readouterr().err


def test_terminal_no_leaves_permissions_unchanged(tmp_path, monkeypatch, capsys):
    config_path, library = loose_config(tmp_path)
    monkeypatch.delenv("PIXLSTASH_INSTALL_TYPE", raising=False)
    monkeypatch.delenv("PIXLSTASH_REPAIR_PERMISSIONS", raising=False)
    monkeypatch.setattr(app.sys, "stdin", TerminalInput())
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    app._prepare_startup_permissions(str(config_path), {"image_root": str(library)})
    assert mode(config_path.parent) == 0o775
    assert "Permissions were not changed" in capsys.readouterr().err


def test_noninteractive_launch_prints_copyable_commands(tmp_path, monkeypatch, capsys):
    config_path, library = loose_config(tmp_path)
    monkeypatch.delenv("PIXLSTASH_INSTALL_TYPE", raising=False)
    monkeypatch.delenv("PIXLSTASH_REPAIR_PERMISSIONS", raising=False)
    monkeypatch.setattr(app.sys, "stdin", io.StringIO())

    app._prepare_startup_permissions(str(config_path), {"image_root": str(library)})
    stderr = capsys.readouterr().err
    assert "PixlStash will start anyway" in stderr
    assert f"chmod 700 {config_path.parent}" in stderr
    assert f"chmod 755 {library}" in stderr
    assert mode(config_path.parent) == 0o775


class RecordingHandler(logging.Handler):
    """A console stand-in that records what actually reached the screen."""

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def console():
    """Give the root logger one recording handler, and put it back afterwards."""
    root = logging.getLogger()
    previous_handlers, previous_level = list(root.handlers), root.level
    handler = RecordingHandler()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)


class TestAQuestionOwnsTheScreen:
    """Start-up asks after the workers are running, so it has to hold the log.

    The first-run credentials prompt was written between two INFO lines and a
    snapshot task logged onto the same line while it waited for an answer.
    """

    def test_a_record_logged_during_a_question_waits_for_the_answer(self, console):
        logger = logging.getLogger("pixlstash.test.holding")

        with hold_log_output():
            logger.info("a worker logging while the question is on screen")
            assert console.lines == [], "the question would be buried under this"

        assert console.lines == ["a worker logging while the question is on screen"]

    def test_nothing_is_dropped_and_the_order_survives(self, console):
        logger = logging.getLogger("pixlstash.test.holding")

        logger.info("before")
        with hold_log_output():
            logger.info("held first")
            logger.warning("held second")
        logger.info("after")

        assert console.lines == ["before", "held first", "held second", "after"]

    def test_the_console_works_again_even_if_the_question_raises(self, console):
        logger = logging.getLogger("pixlstash.test.holding")

        with pytest.raises(KeyboardInterrupt):
            with hold_log_output():
                logger.info("held")
                raise KeyboardInterrupt
        logger.info("after")

        assert console.lines == ["held", "after"]


class TestTheFirstRunCredentialsPrompt:
    def _server(self):
        auth = SimpleNamespace(
            user=SimpleNamespace(username="", password_hash=""),
            ensure_user=lambda: SimpleNamespace(username="", password_hash=""),
            set_username=lambda value: recorded.setdefault("username", value),
            set_password_hash=lambda value: recorded.setdefault("hash", value),
        )
        recorded: dict = {}
        return SimpleNamespace(auth=auth), recorded

    def test_it_announces_itself_and_holds_the_log_until_it_is_answered(
        self, monkeypatch, capsys, console
    ):
        server, recorded = self._server()
        monkeypatch.setattr("sys.stdin", TerminalInput("y\nme\n"))

        # A background worker logging exactly where it used to land: while the
        # prompt sits waiting for a password.
        def answer_password(_prompt):
            logging.getLogger("pixlstash.test.worker").info("snapshot 1 created")
            return "a-long-enough-password"

        monkeypatch.setattr(app.getpass, "getpass", answer_password)

        app._prompt_bootstrap_credentials(server)

        out = capsys.readouterr().out
        assert "PixlStash first-run credentials" in out, "the question needs a heading"
        assert "Bootstrap credentials saved." in out
        assert recorded["username"] == "me"
        # Held, not lost: both records (password and confirmation) reach the
        # console once the answer is in.
        assert console.lines == ["snapshot 1 created", "snapshot 1 created"]

    def test_declining_still_releases_the_held_output(
        self, monkeypatch, capsys, console
    ):
        server, recorded = self._server()
        monkeypatch.setattr("sys.stdin", TerminalInput("n\n"))
        logging.getLogger("pixlstash.test.worker").info("before the question")

        app._prompt_bootstrap_credentials(server)

        assert recorded == {}, "declining sets nothing"
        assert console.lines == ["before the question"]
