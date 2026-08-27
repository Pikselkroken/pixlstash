"""The command line documents itself: every command, every parameter.

``--help`` is the only documentation someone at a terminal has, so a flag added
without a ``help=``, or a verb added without a ``description=``, is a hole that
nothing else in this repository notices. These tests walk both entry points'
parsers and fail on the hole rather than waiting for a user to find it.

They also fail on the opposite mistake, which #960 turned up: ``--retag-and-embed``
was parsed and documented for its whole life and never read by anything, so the
help promised work the server does not do.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

import pytest

from pixlstash import app, cli

REPO_ROOT = Path(__file__).resolve().parent.parent

# Keyed by the module that owns each parser, so the test below can look the
# expected prog up in pyproject.toml rather than restate it here.
PARSERS = {cli: cli.build_parser(), app: app.build_parser()}


def _walk(parser: argparse.ArgumentParser, path: str):
    """Yield ``(command path, parser)`` for *parser* and every parser under it."""
    yield path, parser
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, subparser in action.choices.items():
            yield from _walk(subparser, f"{path} {name}")


def _all_parsers():
    for module, parser in PARSERS.items():
        yield from _walk(parser, parser.prog)


def test_the_walk_reaches_every_command():
    """Guards the guardrails: these walk argparse internals.

    ``_actions`` and ``_choices_actions`` are private, so a change in argparse
    would not raise here - the loops below would simply find nothing and pass.
    A floor on what the walk reaches turns that silence into a failure.
    """
    walked = {path for path, _parser in _all_parsers()}
    assert "pixlstash-cli libraries backup" in walked
    assert "pixlstash-server" in walked
    assert "pixlstash-cli plugins available" in walked
    # 2 roots + 2 groups + 8 library verbs + 7 plugin verbs, at least.
    assert len(walked) >= 19, walked


def test_entry_points_are_named_as_they_are_installed():
    """The usage line has to name a command the reader can actually type.

    ``pixlstash-server`` used to report itself as ``app.py``, which is neither
    the console script nor the ``python -m`` form. Read from pyproject.toml
    rather than restated, so renaming a console script and not its parser is
    the failure it should be.
    """
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    scripts = pyproject["project"]["scripts"]
    for module, parser in PARSERS.items():
        entry_points = [
            name
            for name, target in scripts.items()
            if target.split(":")[0] == module.__name__
        ]
        assert entry_points == [parser.prog], (
            f"{module.__name__} calls itself {parser.prog!r}; "
            f"pyproject.toml installs it as {entry_points}"
        )


def test_every_command_has_a_description():
    """`<command> --help` must say what the command does, not only list flags."""
    missing = [path for path, parser in _all_parsers() if not parser.description]
    assert not missing, f"no description under --help for: {missing}"


def test_every_subcommand_is_listed_with_a_summary():
    """The one-line summary in the parent's command list."""
    missing = []
    for path, parser in _all_parsers():
        for action in parser._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for choice in action._choices_actions:
                if not choice.help:
                    missing.append(f"{path} {choice.dest}")
    assert not missing, f"no summary in the command list for: {missing}"


def test_every_parameter_has_help():
    """Positionals and options alike, on every parser."""
    missing = []
    for path, parser in _all_parsers():
        for action in parser._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            # A subparsers action is documented by its choices, checked above.
            if isinstance(action, argparse._SubParsersAction):
                continue
            if not action.help:
                missing.append(f"{path}: {action.dest}")
    assert not missing, f"no help text for: {missing}"


def test_the_help_is_the_documentation_it_claims_to_be():
    """A shape check on the two claims the tests above cannot see.

    Both are load-bearing for anyone scripting the CLI: the exit codes appear
    nowhere else, and ``--hub`` silently does nothing for ``plugins``.
    """
    parser = PARSERS[cli]
    text = parser.format_help()
    assert "Exit codes:" in text
    for code in (cli.EXIT_OK, cli.EXIT_REFUSED, cli.EXIT_HUB_UNAVAILABLE):
        assert f"\n  {code}  " in text, f"exit code {code} is not documented"

    # The caveat, not the sentence carrying it: `--hub` has to name the group
    # it does nothing for, and rewording that is not a regression.
    hub = next(action for action in parser._actions if action.dest == "hub")
    assert "plugins" in hub.help


@pytest.mark.parametrize("module", [cli, app], ids=["cli", "app"])
def test_no_parameter_is_documented_without_being_read(module):
    """Every declared parameter is consumed somewhere in its own module.

    An option nobody reads is worse than an undocumented one: the help states
    it does something, and it does nothing at all.

    A substring search, deliberately: it is a lint, not a proof. A parameter
    read through ``getattr`` or handed to another module would need an
    exemption here, and both entry points read theirs as ``args.<dest>``.
    """
    source = Path(module.__file__).read_text(encoding="utf-8")
    unread = []
    for _path, parser in _walk(module.build_parser(), module.__name__):
        for action in parser._actions:
            if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
                continue
            if f"args.{action.dest}" not in source:
                unread.append(action.dest)
    assert not unread, f"{module.__name__} declares parameters nothing reads: {unread}"
