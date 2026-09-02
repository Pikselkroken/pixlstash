"""The PixlStash CLI: ``pixlstash-cli`` / ``python -m pixlstash.cli``.

Creates, attaches, detaches and lists libraries, and installs plugins. In the
MVP this is the only way to change the library registry: the server exposes no
HTTP route that accepts a host path, so nothing reachable over the network can
point PixlStash at a new folder.

**Authentication is filesystem access.** Shell access as the OS user that owns
the hub file *is* the credential, the same model as ``psql`` or ``docker``.
There is no login here and no stored credential; Docker deployments reach it
with ``docker exec``.

The library verbs destroy nothing: ``detach`` deregisters a library and never
touches its files, and there is deliberately no ``--delete`` flag. ``restore``
is the one that looks like an exception and is not - it writes only to a folder
it has proved empty, and *moves* the configuration it replaces into a dated
folder beside itself, printing the command that reopens it. ``plugins remove``
does delete, because a CLI that installs plugins and cannot uninstall them is
not worth shipping; what it guarantees instead is that the path it deletes is
always inside one of the two plugin directories.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from pixlstash.hub.cli_hint import desktop_windows_command
from pixlstash.hub.db import HubDatabase, HubPermissionError, default_hub_path
from pixlstash.hub.registry import (
    Library,
    LibraryError,
    LibraryRegistry,
    resolve_path,
)
from pixlstash.hub.schema import HubSchemaTooNewError
from pixlstash import plugin_create, plugin_install
from pixlstash.plugin_install import PluginError

# Exit codes. 0 success, 1 a refusal the user can act on (not a vault, already
# registered, library is active), 2 argparse usage error, 3 the hub itself
# could not be opened.
EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_HUB_UNAVAILABLE = 3

# Every verb that names a library accepts the same three forms, so they are
# documented in one place rather than drifting apart across five parsers.
LIBRARY_ARG_HELP = (
    "Which library: its name, its id from `list`, or its uuid. Quote a name "
    "containing spaces; digits are matched against ids before names. A "
    "detached library is not in `list` and answers only to its id or uuid."
)


def _hub_from_argv(argv: Sequence[str] | None = None) -> str | None:
    """Return the ``--hub`` value on the command line, if there is one.

    Read straight from ``sys.argv`` rather than from parsed arguments because
    :func:`invoked_as` fills argparse's own ``prog``, which is needed to build
    the parser that would do the parsing.
    """
    args = list(sys.argv if argv is None else argv)
    for index, arg in enumerate(args):
        if arg == "--hub" and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("--hub="):
            return arg[len("--hub=") :]
    return None


def invoked_as() -> str:
    """Return the command the user typed to get here.

    Every usage line, error and "add one with:" hint names this, so on a desktop
    install they must not say ``pixlstash-cli``: that console script is sealed
    inside the app image and is on nobody's PATH. The launcher that ran us
    declares the working form in ``PIXLSTASH_CLI_COMMAND`` (see
    :mod:`pixlstash.hub.cli_hint`, which reads the same variable to fill the
    Settings panel). Everywhere else the console script is exactly right.

    The Windows desktop declares nothing, because the command that works there
    is *this very interpreter* and so can be derived rather than announced
    (issue #1058). Deriving it through the same helper the Settings panel uses
    keeps the two from drifting: what the panel prints is what the CLI then
    calls itself.
    """
    declared = os.environ.get("PIXLSTASH_CLI_COMMAND", "").strip()
    if declared:
        return declared
    return desktop_windows_command(_hub_from_argv()) or "pixlstash-cli"


def epilog() -> str:
    """Return the text shown under every top-level ``--help``.

    Exit codes belong in the help output rather than only in this file's
    docstring: a script calling the CLI has to tell "you asked for something I
    will not do" apart from "I could not open the hub", and nothing else tells
    it. Built per call rather than held as a constant so its worked example
    names the command the reader actually typed (see :func:`invoked_as`).
    """
    return f"""\
Every command has its own help, e.g.
  {invoked_as()} libraries backup --help

Exit codes:
  0  the command did what it says
  1  refused for a reason you can act on, or you answered no to a prompt
  2  the command line itself was wrong
  3  the hub database could not be opened
"""


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the library CLI."""
    parser = argparse.ArgumentParser(
        prog=invoked_as(),
        # Wrapped by hand: RawDescriptionHelpFormatter prints the description
        # as written, which is the price of an epilog that keeps its layout.
        description=(
            "PixlStash command line. Run this on the machine hosting\n"
            "PixlStash, signed in as the user that owns it."
        ),
        epilog=epilog(),
        # Keeps the epilog's exit-code list on separate lines instead of
        # reflowing it into a paragraph.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--hub",
        default=None,
        metavar="PATH",
        help=(
            f"Hub database to use (default: {default_hub_path()}). Used by the "
            "`libraries` commands; `plugins` never opens the hub. Global "
            "options go before the group name."
        ),
    )
    # Only the library verbs need the hub. Opening it for `plugins` would make
    # plugin installation fail on a machine that has never run the server.
    parser.set_defaults(needs_hub=False)

    # Verbs are grouped so the CLI has room for more than libraries later.
    groups = parser.add_subparsers(dest="group", required=True)
    libraries = groups.add_parser(
        "libraries",
        help="Manage libraries.",
        description=(
            "Manage PixlStash libraries. A library is a folder holding "
            "vault.db and its images."
        ),
    )
    libraries.set_defaults(needs_hub=True)
    subparsers = libraries.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list",
        help="Show the registered libraries and which one is active.",
        description=(
            "Print every registered library with its id, name and folder. "
            "`*` marks the active one; `(not found)` marks a registration "
            "whose folder is missing - reconnect the drive, or point it "
            "somewhere new with `relocate`."
        ),
    )
    list_parser.set_defaults(handler=_cmd_list)

    create_parser = subparsers.add_parser(
        "create",
        help="Create a folder, start an empty library in it, register it.",
        description=(
            "Create the folder, initialise an empty library in it, and "
            "register it. A folder that already holds vault.db is refused; "
            "use `attach` for that one. The first library on an installation "
            "becomes the active one; any later library has to be switched to "
            "in Settings › Libraries."
        ),
    )
    create_parser.add_argument("folder", help="Folder to create the library in.")
    create_parser.add_argument(
        "--name", default=None, help="Display name (default: the folder's name)."
    )
    create_parser.set_defaults(handler=_cmd_create)

    attach_parser = subparsers.add_parser(
        "attach",
        help="Register a library that already exists on disk.",
        description=(
            "Register a library folder that already exists. The folder must "
            "hold vault.db, and any login or tokens inside that vault are "
            "ignored rather than imported. Attaching a library that was "
            "detached earlier revives its original registration, and with it "
            "the share links and API tokens issued from it - but only while "
            "the folder still holds that same library; a different one at "
            "that path is registered as a new library and the old tokens stay "
            "inert. Overlapping an existing library warns rather than refuses."
        ),
    )
    attach_parser.add_argument("folder", help="Folder containing vault.db.")
    attach_parser.add_argument(
        "--name", default=None, help="Display name (default: the folder's name)."
    )
    attach_parser.set_defaults(handler=_cmd_attach)

    detach_parser = subparsers.add_parser(
        "detach",
        help="Forget a library. No files are removed and nothing in the folder changes.",
        description=(
            "Deregister a library. No files are removed and nothing inside "
            "the folder changes. The registration is kept rather than "
            "deleted, so attaching this library again brings back its share "
            "links and API tokens; until then they are inert. The active "
            "library is refused - switch to another one first."
        ),
    )
    detach_parser.add_argument("library", help=LIBRARY_ARG_HELP)
    detach_parser.set_defaults(handler=_cmd_detach)

    relocate_parser = subparsers.add_parser(
        "relocate",
        help="Point a library at a folder that has moved, keeping its share links.",
        description=(
            "Point an existing registration at a folder that has moved. The "
            "library keeps its identity, so its share links and API tokens "
            "keep working; detaching and attaching at the new path would mint "
            "a new identity and leave them inert. The new folder must hold "
            "vault.db."
        ),
    )
    relocate_parser.add_argument("library", help=LIBRARY_ARG_HELP)
    relocate_parser.add_argument("folder", help="Where the library now lives.")
    relocate_parser.set_defaults(handler=_cmd_relocate)

    backup_parser = subparsers.add_parser(
        "backup",
        help="Write a library and the hub to a single archive.",
        description=(
            "Writes a consistent copy even while the library is open. The "
            "archive contains your credentials, so it is written owner-readable "
            "only; pictures in reference folders are outside the library and are "
            "not included. An existing destination file is never overwritten. "
            "Read it back with `restore`, or by hand: it is a zstd-compressed "
            "tar (a plain tar with --no-compress) holding manifest.json, "
            "vault.db, hub.db and - unless --metadata-only was given - the "
            "library's own files under images/."
        ),
    )
    backup_parser.add_argument("library", help=LIBRARY_ARG_HELP)
    backup_parser.add_argument(
        "destination", help="Output file, or a folder to write a dated name into."
    )
    backup_parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Skip the image files. A catalogue is worth nothing without them.",
    )
    backup_parser.add_argument(
        "--no-compress",
        action="store_true",
        help="Write a plain .tar. Faster for large image sets, which barely compress.",
    )
    backup_parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Do not ask for confirmation. Only asked when the destination looks "
            "too small; a cron job wants this so it cannot hang on the question."
        ),
    )
    backup_parser.set_defaults(handler=_cmd_backup)

    restore_parser = subparsers.add_parser(
        "restore",
        help="Restore a backup into a new folder and make it the library that opens.",
        description=(
            "Unpacks a `backup` archive into a folder that must not already "
            "hold anything, then makes it the library PixlStash opens. The "
            "archive's hub replaces this installation's, which is what brings "
            "back the password and the API tokens that library was using. "
            "Nothing is overwritten and nothing is deleted: the current "
            "server-config.json and hub.db are MOVED into a dated "
            "pre-restore-* folder beside themselves, and the command prints "
            "the launch command for each. The library you are using now is "
            "not touched. PixlStash must not be running."
        ),
    )
    restore_parser.add_argument("archive", help="Backup archive written by `backup`.")
    restore_parser.add_argument(
        "folder",
        help=("Folder for the restored library. Must be empty, or not exist yet."),
    )
    restore_parser.add_argument(
        "--yes", action="store_true", help="Do not ask for confirmation."
    )
    # Needs the hub's *path*, never its contents: a hub too corrupt to open is
    # exactly when someone restores, so `main`'s open must not gate this.
    restore_parser.set_defaults(handler=_cmd_restore, needs_hub=False)

    migrate_parser = subparsers.add_parser(
        "prepare-legacy-identity",
        help="Explicitly approve one legacy owner/token migration before startup.",
        description=(
            "Approve moving one older library's owner and API tokens out of "
            "its vault.db and into the hub. Startup deliberately will not "
            "guess which vault to trust on its own; an interactive terminal "
            "launch offers this same approval as a `[y/N]` prompt instead, "
            "so this command is for a non-interactive upgrade (a service, a "
            "container, a script) or for approving it ahead of time. This "
            "records the approval and nothing else - the copy, the "
            "verification and the blanking of the old identity happen the "
            "next time PixlStash starts."
        ),
    )
    migrate_parser.add_argument(
        "folder", help="Legacy library folder containing vault.db."
    )
    migrate_parser.set_defaults(handler=_cmd_prepare_legacy_identity)

    rename_parser = subparsers.add_parser(
        "rename",
        help="Change a library's name.",
        description=(
            "Change a library's display name. Nothing on disk moves and no "
            "link changes. Names are unique, so a name another library "
            "already holds is refused."
        ),
    )
    rename_parser.add_argument("library", help=LIBRARY_ARG_HELP)
    rename_parser.add_argument("new_name", help="The new display name.")
    rename_parser.set_defaults(handler=_cmd_rename)

    _add_plugin_parsers(groups)
    return parser


def _add_plugin_parsers(groups: argparse._SubParsersAction) -> None:
    """Add the `plugins` group: create, install, test, available, list, remove."""
    plugins = groups.add_parser(
        "plugins",
        help="Install, list and remove plugins.",
        description=(
            "Install a captioning plugin or an image filter. The destination "
            "differs by kind and by shape, so it is worked out from the source "
            "rather than typed. Plugin code runs unsandboxed in the server "
            "process with your permissions."
        ),
    )
    commands = plugins.add_subparsers(dest="command", required=True)

    install_parser = commands.add_parser(
        "install",
        help="Install a plugin from the plugins repository, a zip, a folder or a .py.",
        description=(
            "Read the source without importing it, work out whether it is a "
            "captioning plugin or an image filter, and copy it into the "
            "matching user plugin directory (`plugins list` prints both "
            "paths). Prints the plan and asks before writing anything unless "
            "--yes is given. A captioning plugin is loaded when the server "
            "next starts; an image filter appears the next time the Filters "
            "menu is listed."
        ),
    )
    install_parser.add_argument(
        "source",
        help=(
            "A plugin name from the plugins repository (`plugins available` "
            "lists them), or a path to a .zip, a folder, or a single .py file."
        ),
    )
    install_parser.add_argument(
        "--ref",
        default=plugin_install.DEFAULT_REF,
        help=(
            "Branch, tag or commit in the plugins repository "
            f"(default: {plugin_install.DEFAULT_REF}). Ignored for local sources."
        ),
    )
    install_parser.add_argument(
        "--yes", action="store_true", help="Do not ask for confirmation."
    )
    install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be installed and stop.",
    )
    install_parser.add_argument(
        "--force", action="store_true", help="Replace an existing plugin of this name."
    )
    install_parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat every warning as a refusal. For scripted installs.",
    )
    install_parser.add_argument(
        "--with-deps",
        action="store_true",
        help="Also pip-install the plugin's requirements.txt, if it has one.",
    )
    install_parser.add_argument(
        "--force-deps",
        action="store_true",
        help=(
            "Install the dependencies even when they replace a package "
            "PixlStash is using. This can stop PixlStash starting."
        ),
    )
    install_parser.set_defaults(handler=_cmd_plugins_install)

    create_parser = commands.add_parser(
        "create",
        help="Start a new plugin as a branch in a checkout of the plugins repo.",
        # Wrapped by hand: the two-line summary of what lands on disk is what
        # someone needs before they run a command that clones a repository.
        description=(
            "Set up everything a plugin pull request needs, so the only work\n"
            "left is the plugin itself. Run it with no arguments and it asks:\n"
            "what kind of plugin, what it should do, what to call it. Give\n"
            "a name and --kind and it asks nothing.\n"
            "\n"
            "It first works out whether it can fork\n"
            f"{plugin_install.PLUGINS_REPO} for you, naming the repository it\n"
            "would create on your account, and says why not when it cannot.\n"
            "The first question asked is the way out: its last numbered\n"
            "option stops before anything is forked, cloned or written.\n"
            "Then it clones,\n"
            "branches, copies one of the repository's\n"
            "example plugins to `plugins/<kind>/<name>/`, renames its folder,\n"
            "module and class, puts your name and licence in the header,\n"
            "writes what you said it should do into its README, and prints\n"
            "the commands that open the pull request.\n"
            "\n"
            "Last, it offers a one-line `claude` or `codex` command pointing\n"
            "the agent at that README. The rest of the brief is the plugins\n"
            "repository's own AGENTS.md, which both agents read for\n"
            "themselves. It prints the command rather than running it:\n"
            "starting an agent on your checkout is your call.\n"
            "\n"
            "Nothing is committed, nothing is pushed and no pull request is\n"
            "opened: the plugin at that point is still the example, and the\n"
            "repository takes one finished plugin per pull request.\n"
            "\n"
            "The copy is the example CI keeps green rather than a template\n"
            "kept in PixlStash, so a scaffold cannot be out of date with the\n"
            "contract tests it has to pass. Nothing is imported or run."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    create_parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help=(
            "The new plugin's name: lower-case, starting with a letter, "
            "letters, digits and underscores only. It names the folder, the "
            "module, the branch and the plugin's `name` attribute. Asked for "
            "if you leave it out."
        ),
    )
    create_parser.add_argument(
        "--kind",
        choices=[plugin_install.CAPTIONING, plugin_install.IMAGE],
        default=None,
        help=(
            "What you are writing: `captioning` turns an image into tags or a "
            "description, `image` turns a picture into another picture (the "
            "Filters menu). It picks the directory, the example and the "
            "shape. Asked for if you leave it out."
        ),
    )
    create_parser.add_argument(
        "--purpose",
        default=None,
        metavar="TEXT",
        help=(
            "What the plugin should do, in a sentence or three. It becomes "
            "the README's opening paragraph, the class `description`, and "
            "what a coding agent is told to build. Asked for if you leave it "
            "out and there is a terminal to ask on."
        ),
    )
    create_parser.add_argument(
        "--agent",
        choices=[*sorted(plugin_create.AGENTS), "no"],
        default=None,
        help=(
            "Print a command handing the plugin to this coding agent, instead "
            "of asking which. `no` prints none."
        ),
    )
    create_parser.add_argument(
        "--dir",
        dest="directory",
        default=plugin_create.DEFAULT_CHECKOUT,
        metavar="PATH",
        help=(
            "Where the checkout lives (default: ./"
            f"{plugin_create.DEFAULT_CHECKOUT}). An existing checkout is "
            "reused and branched again; anything else there is refused."
        ),
    )
    create_parser.add_argument(
        "--from",
        dest="example",
        default=None,
        metavar="PLUGIN",
        help=(
            "Copy this published plugin instead of the default example "
            f"({plugin_create.DEFAULT_EXAMPLES[plugin_install.CAPTIONING]} or "
            f"{plugin_create.DEFAULT_EXAMPLES[plugin_install.IMAGE]}). Use it "
            "to start from something closer to what you are building."
        ),
    )
    create_parser.add_argument(
        "--branch",
        default=None,
        help="Branch to create (default: add-<name>).",
    )
    create_parser.add_argument(
        "--display-name",
        default=None,
        metavar="LABEL",
        help=(
            "The label PixlStash shows in its menus (default: the name with "
            "the underscores turned into spaces)."
        ),
    )
    create_parser.add_argument(
        "--description",
        default=None,
        help=(
            "One line saying what the plugin does, shown in the UI. Left as a "
            "TODO in the source when you do not give it."
        ),
    )
    create_parser.add_argument(
        "--author",
        default=None,
        metavar="'NAME <CONTACT>'",
        help=(
            "Who to credit, as `Your Name <you@example.com>` or a URL between "
            "the brackets. Defaults to your git identity; the repository's "
            "tests require this shape."
        ),
    )
    create_parser.add_argument(
        "--license",
        dest="plugin_license",
        default=None,
        metavar="SPDX",
        help=(
            "The license of your plugin's own code, as an SPDX identifier "
            "where there is one (default: MIT). This says nothing about the "
            "license of any model it downloads; that goes in `models`."
        ),
    )
    create_parser.add_argument(
        "--no-fork",
        dest="fork",
        action="store_false",
        help=(
            "Clone the repository directly instead of forking it first. You "
            "will not be able to push until you point `origin` somewhere you "
            "can, so this is for looking rather than contributing."
        ),
    )
    create_parser.set_defaults(handler=_cmd_plugins_create)

    submit_parser = commands.add_parser(
        "submit",
        help="Check, commit, push and open the pull request for a new plugin.",
        # Wrapped by hand: what it pushes, and where, is what someone needs to
        # read before running a command that publishes their work.
        description=(
            "Finish what `plugins create` started. It runs the repository's\n"
            "own checks, stages the one plugin folder, commits it, pushes the\n"
            "branch, and opens the pull request.\n"
            "\n"
            "Which plugin is read off the branch, so from a checkout `plugins\n"
            "create` made there is nothing to type. Only the plugin's own\n"
            "folder is staged: the checkout is yours and may hold other work,\n"
            "and the repository takes one plugin per pull request.\n"
            "\n"
            "It stops on a failing check, and asks before it pushes, because\n"
            "pushing and opening a pull request are the two steps that leave\n"
            "this machine. --yes skips the question; --dry-run stops after\n"
            "the checks and pushes nothing.\n"
            "\n"
            "You are asked what you tested the plugin against, which goes in\n"
            "the pull request. CI checks the shape of a model-backed plugin\n"
            "and never runs the model, so that sentence is what makes it\n"
            "reviewable at all."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    submit_parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help=(
            "Which plugin to submit. Taken from the branch name (`add-<name>`) "
            "when you leave it out."
        ),
    )
    submit_parser.add_argument(
        "--dir",
        dest="directory",
        default=plugin_create.DEFAULT_CHECKOUT,
        metavar="PATH",
        help=(
            "The checkout holding the plugin (default: ./"
            f"{plugin_create.DEFAULT_CHECKOUT})."
        ),
    )
    submit_parser.add_argument(
        "--tested",
        default=None,
        metavar="TEXT",
        help=(
            "What you ran the plugin against: which model, which PixlStash "
            "version, on what hardware. Goes in the pull request. Asked for "
            "if you leave it out."
        ),
    )
    submit_parser.add_argument(
        "--message",
        default=None,
        metavar="TEXT",
        help="Commit message and pull request title (default: `Add <name>`).",
    )
    submit_parser.add_argument(
        "--skip-checks",
        action="store_true",
        help=(
            "Do not run ruff and pytest. CI runs them anyway, so this only "
            "moves where you find out."
        ),
    )
    submit_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the checks and stop. Nothing is committed or pushed.",
    )
    submit_parser.add_argument(
        "--yes", action="store_true", help="Do not ask before pushing."
    )
    submit_parser.set_defaults(handler=_cmd_plugins_submit)

    test_parser = commands.add_parser(
        "test",
        help="Load a captioning plugin as the server does and check it.",
        # Wrapped by hand, like the top-level parser: the safety caveat is the
        # first thing to read and has to stay its own paragraph rather than
        # being reflowed into the middle of a block.
        description=(
            "A development aid for writing a plugin. NOT a security scanner:\n"
            "it does not tell you whether a plugin is safe, it RUNS it. The\n"
            "module body - and the model itself, with --image - executes in\n"
            "this process, with your permissions, exactly as it would in the\n"
            "server. Nothing is sandboxed, and nothing here inspects what the\n"
            "code does. Only test a plugin you would have installed anyway.\n"
            "\n"
            "What it does check: that the plugin imports the way the server\n"
            "imports it at start-up, that every plugin class it defines\n"
            "registers, and that its parameter schema is one the settings\n"
            "screen can render - the last of which the server does not check\n"
            "and which fails quietly when it is wrong. Prints what registered.\n"
            "\n"
            "A `problem:` means the plugin will not work and exits 1. A\n"
            "`warning:` means it works and could be tidier - a parameter with\n"
            "no label, no capability flag set - and exits 0.\n"
            "\n"
            "Passing still is not the same as working in PixlStash: a plugin\n"
            "that hangs at import hangs the server's boot and would hang this\n"
            "command too, and nothing here says the captions are any good.\n"
            "Image filters are not checked."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    test_parser.add_argument(
        "path", help="The plugin's .py file, or its folder holding __init__.py."
    )
    test_parser.add_argument(
        "--image",
        default=None,
        metavar="PATH",
        help=(
            "Also run the plugin over this one image with the schema's "
            "defaults and print what comes back. This loads the model, so it "
            "is the slow one. It stops rather than running when the plugin "
            "reports its model is missing, but a plugin that downloads inside "
            "init() will still do so - nothing here can prevent that."
        ),
    )
    test_parser.set_defaults(handler=_cmd_plugins_test)

    available_parser = commands.add_parser(
        "available",
        help="Show the plugins published in the plugins repository.",
        description=(
            "List what the plugins repository publishes, so you can find a "
            "plugin's name before installing it. Give a word to search: it "
            "matches the name, title, summary, author and licence, so anything "
            "you can see in the listing you can also search for. `*` marks a "
            "plugin you already have installed. This downloads the same "
            "archive `plugins install <name>` does; nothing is imported or run."
        ),
    )
    available_parser.add_argument(
        "query",
        nargs="?",
        default="",
        metavar="WORD",
        help="Only show plugins matching this word (case-insensitive).",
    )
    available_parser.add_argument(
        "--ref",
        default=plugin_install.DEFAULT_REF,
        help=(
            "Branch, tag or commit in the plugins repository "
            f"(default: {plugin_install.DEFAULT_REF})."
        ),
    )
    available_parser.set_defaults(handler=_cmd_plugins_available)

    list_parser = commands.add_parser(
        "list",
        help="Show the installed plugins, grouped by kind.",
        description=(
            "Print both plugin directories and what is installed in them. "
            "`!` marks a plugin that will not load as it stands and `*` one "
            "that replaces a built-in. Nothing is imported here, so a failure "
            "that only happens at import - a missing dependency, say - is not "
            "visible in this listing."
        ),
    )
    list_parser.set_defaults(handler=_cmd_plugins_list)

    remove_parser = commands.add_parser(
        "remove",
        help="Delete an installed plugin. This removes files.",
        description=(
            "Delete an installed plugin's file or folder, after printing the "
            "exact path and asking, unless --yes is given. The path deleted "
            "is always inside one of the two plugin directories. Removing a "
            "plugin that replaces a built-in filter brings the built-in back; "
            "removing a captioning plugin takes effect when the server next "
            "starts."
        ),
    )
    remove_parser.add_argument("name", help="Plugin name from `plugins list`.")
    remove_parser.add_argument(
        "--kind",
        choices=[plugin_install.CAPTIONING, plugin_install.IMAGE],
        default=None,
        help="Which directory to look in. Only needed when both hold the name.",
    )
    remove_parser.add_argument(
        "--yes", action="store_true", help="Do not ask for confirmation."
    )
    remove_parser.set_defaults(handler=_cmd_plugins_remove)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.needs_hub:
        # These handlers take no registry: they touch the plugin directories
        # and nothing else, and must work before a hub exists.
        try:
            return args.handler(args)
        except PluginError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_REFUSED
        except KeyboardInterrupt:
            # Ctrl-C is the other way out of the wizard, and a traceback is a
            # poor answer to someone who just said stop. What has already
            # happened has happened; the message says only that this stopped.
            print("\nStopped.", file=sys.stderr)
            return EXIT_REFUSED

    try:
        # repair_permissions: the CLI tightens a loose hub file in place and
        # says so, because the person running it is the owner and can act on
        # it now. The server refuses to start instead (see hub.db).
        hub = HubDatabase(args.hub, repair_permissions=True)
    except (HubPermissionError, HubSchemaTooNewError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_HUB_UNAVAILABLE
    except OSError as exc:
        print(
            f"error: could not open the hub database at "
            f"{args.hub or default_hub_path()}: {exc}",
            file=sys.stderr,
        )
        return EXIT_HUB_UNAVAILABLE

    try:
        registry = LibraryRegistry(hub)
        return args.handler(registry, args)
    except LibraryError as exc:
        # Every LibraryError carries a message written for the person at the
        # terminal, so print it as-is rather than wrapping it in a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        hub.close()


def _cmd_list(registry: LibraryRegistry, _args: argparse.Namespace) -> int:
    """Print the registry, marking the active library and any unreachable one."""
    libraries = registry.list_libraries()
    if not libraries:
        print("No libraries are registered yet.")
        print(f"Add one with:  {invoked_as()} libraries attach /path/to/library")
        return EXIT_OK

    name_width = max(len(library.name) for library in libraries)
    name_width = max(name_width, len("NAME"))
    print(f"{'':2}{'ID':>3}  {'NAME':<{name_width}}  PATH")
    for library in libraries:
        marker = "* " if library.is_active else "  "
        suffix = "" if library.is_reachable else "   (not found)"
        print(
            f"{marker}{library.id:>3}  {library.name:<{name_width}}  "
            f"{library.path}{suffix}"
        )
    print("\n* = active library")
    return EXIT_OK


def _cmd_create(registry: LibraryRegistry, args: argparse.Namespace) -> int:
    """Create and register a new, empty library."""
    library = registry.create(args.folder, args.name)
    print(f'Created library "{library.name}" at {library.path}')
    _print_activation_note(library)
    return EXIT_OK


def _cmd_attach(registry: LibraryRegistry, args: argparse.Namespace) -> int:
    """Register an existing library, warning about any overlap."""
    resolved = resolve_path(args.folder)
    overlaps = registry.overlapping(resolved)

    library = registry.attach(args.folder, args.name)
    print(f'Attached library "{library.name}" at {library.path}')

    for other in overlaps:
        # A warning, not a refusal: nested libraries are legal, but two
        # libraries over the same files will eventually disagree about sidecars
        # and deletions.
        print(
            f'warning: this folder overlaps library "{other.name}" at '
            f"{other.path}. Two libraries sharing files can conflict over "
            "sidecars and deletions.",
            file=sys.stderr,
        )

    print(
        "If this library references external folders, they will need "
        "re-pointing or removing in Settings after you switch to it."
    )
    _print_activation_note(library)
    return EXIT_OK


def _cmd_detach(registry: LibraryRegistry, args: argparse.Namespace) -> int:
    """Deregister a library, restating that its files are untouched."""
    library = registry.detach(args.library)
    print(f'Detached library "{library.name}".')
    print(f"No files were removed. {library.path} is unchanged.")
    print(
        f"Add it back at any time with:  {invoked_as()} "
        f'libraries attach "{library.path}"'
    )
    return EXIT_OK


def _cmd_relocate(registry: LibraryRegistry, args: argparse.Namespace) -> int:
    """Move a registration to a new folder, keeping its identity."""
    library = registry.relocate(args.library, args.folder)
    print(f'Library "{library.name}" now lives at {library.path}')
    print("Its share links and API tokens keep working.")
    return EXIT_OK


def _cmd_backup(registry: LibraryRegistry, args: argparse.Namespace) -> int:
    """Archive a library together with the hub."""
    # Local import: pulls in tar/zstd and the backup service, which `list`,
    # `attach` and `detach` have no use for.
    from pixlstash.services.library_backup_service import BackupError, create_backup

    library = registry.get(args.library)
    try:
        result = create_backup(
            library,
            args.destination,
            registry.hub_path,
            metadata_only=args.metadata_only,
            compress=not args.no_compress,
            tool_version=_tool_version(),
            # Only ever called when the destination looks too small. --yes is
            # the scripted answer; without it a cron job would hang on a
            # question nobody is there to read.
            confirm=(lambda message: True) if args.yes else _confirm,
        )
    except BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    megabytes = result.byte_size / (1024 * 1024)
    print(f'Backed up "{library.name}" to {result.path} ({megabytes:.1f} MB)')
    print(f"{result.picture_count} picture(s) catalogued.")
    if result.metadata_only:
        print(
            "This is a metadata-only archive: it holds the database, not your "
            "images. It can only restore a library whose picture files still exist."
        )
    if result.has_external_folders:
        # Said at the top of the output, with the count, because users assume
        # otherwise: reference folders live outside the library by definition.
        print(
            f"note: this library references {len(result.reference_folders)} "
            "external folder(s), which are NOT in the archive:",
            file=sys.stderr,
        )
        for folder in result.reference_folders:
            print(f"  {folder}", file=sys.stderr)
    print(
        "The archive contains your login and tokens, so it is readable only by "
        "you. Keep it somewhere private."
    )
    return EXIT_OK


def _cmd_restore(args: argparse.Namespace) -> int:
    """Stage a backup, describe exactly what it will do, then publish it."""
    # Local import for the same reason as _cmd_backup: tar, zstd and the hub
    # schema are dead weight for `list` and `attach`.
    from pixlstash.services import library_restore_service as restore

    hub_path = args.hub or default_hub_path()
    scratch = None
    try:
        # Planned before anything is unpacked: the plan is read from the front
        # of the archive, so the question below is asked before the copy rather
        # than after it.
        plan = restore.plan_restore(args.archive, args.folder, hub_path)

        print(f"Archive:  {plan.archive}")
        print(f'Library:  "{plan.library_name}" ({plan.picture_count} picture(s))')
        print(f"Taken:    {plan.created_at}, from {plan.source_path}")
        print(f"Restore to: {plan.library_folder}")
        print()
        print("This will:")
        print(f"  - write the restored library to {plan.library_folder}")
        print(
            f"  - move {restore.SERVER_CONFIG_FILENAME} and hub.db from "
            f"{plan.config_dir} "
            f"into {plan.preserved_dir}"
        )
        print(
            "  - make the restored library the one PixlStash opens, and replace "
            "your current password and API tokens with the archive's"
        )
        if plan.other_libraries:
            print(
                f"  - bring back {plan.other_libraries} other library "
                "registration(s) from the archive; any whose folder is not on "
                "this machine will show as (not found)"
            )
        print()
        print("Your current library folder is NOT touched, and nothing is deleted.")
        # The credentials come out of the archive, so restoring one you did not
        # make is handing its author the owner account on this machine - which
        # reaches the host-capability routes, not just the restored pictures.
        # Worth saying plainly: the rest of this output reads reassuring.
        print(
            "Restore only an archive you made yourself. Its password and tokens "
            "become this installation's, so restoring someone else's archive "
            "gives whoever made it owner access to this machine."
        )
        if plan.metadata_only:
            print(
                "warning: this is a metadata-only archive. It restores the "
                "catalogue, not the pictures.",
                file=sys.stderr,
            )
        if plan.reference_folders:
            print(
                f"warning: this library referenced {len(plan.reference_folders)} "
                "external folder(s), which were never in the archive. Re-point "
                "or remove them in Settings after restoring.",
                file=sys.stderr,
            )

        if plan.space_warning:
            print(f"warning: {plan.space_warning}", file=sys.stderr)

        if not args.yes and not _confirm("Restore it?"):
            print("Cancelled. Nothing was written.")
            return EXIT_REFUSED

        # Only now is anything written: staging is created after the answer.
        scratch = restore.restore_scratch(args.folder)
        result = restore.perform_restore(plan, scratch)
    except restore.RestoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        restore.remove_scratch(scratch)

    _print_restore_report(result)
    return EXIT_OK


def _quote_path(path: str) -> str:
    """Make *path* safe to paste into a shell prompt.

    Double quotes rather than ``shlex.quote``: these commands are printed on
    Windows too, where the single quotes shlex emits are literal characters to
    cmd.exe. Double quotes work in both, and a library folder named "Holiday
    photos" is the ordinary case, not the exotic one.
    """
    if path and not any(char.isspace() or char in "\"'\\$`" for char in path):
        return path
    return '"' + path.replace('"', '\\"') + '"'


def _print_restore_report(result) -> None:
    """Say what landed where, and how to launch either installation."""
    from pixlstash.services import library_restore_service as restore

    plan = result.plan
    print()
    print(
        f'Restored "{plan.library_name}" to {plan.library_folder} '
        f"({result.file_count} file(s))."
    )
    print(
        "Sign in with the password that library used when the backup was "
        "taken; its API tokens work again too."
    )
    restored_config = os.path.join(plan.config_dir, restore.SERVER_CONFIG_FILENAME)
    print()
    print("Launch the RESTORED library (this is what starts by default now):")
    print(f"  pixlstash-server --server-config {_quote_path(restored_config)}")
    if not result.had_previous_config:
        print("\nThere was no previous configuration to preserve.")
        return
    print()
    print("Launch your PREVIOUS library, exactly as it was before this restore:")
    print(f"  pixlstash-server --server-config {_quote_path(plan.preserved_config)}")
    print()
    print(
        f"The previous {restore.SERVER_CONFIG_FILENAME} and hub.db are in {plan.preserved_dir}."
    )
    print(f"Move them back into {plan.config_dir} to undo this restore completely.")


def _cmd_prepare_legacy_identity(
    registry: LibraryRegistry, args: argparse.Namespace
) -> int:
    from pixlstash.hub.bootstrap import HubBootstrapError, prepare_legacy_identity

    try:
        library = prepare_legacy_identity(registry._hub, args.folder)
    except HubBootstrapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"Prepared legacy identity migration for {library.uuid} at {library.path}")
    print("Start PixlStash to copy, verify, and blank the approved legacy identity.")
    return EXIT_OK


def _tool_version() -> str:
    """Return the installed PixlStash version, or 'unknown'."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("pixlstash")
    except PackageNotFoundError:
        return "unknown"


def _cmd_rename(registry: LibraryRegistry, args: argparse.Namespace) -> int:
    """Change a library's display name."""
    library = registry.rename(args.library, args.new_name)
    print(f'Library {library.id} is now named "{library.name}".')
    return EXIT_OK


def _confirm(question: str, default: bool = False) -> bool:
    """Ask for a y/n on stdin, Enter taking *default*.

    *default* is True only where the question follows a full itemised list of
    what is about to happen, which is the one case where Enter is an informed
    answer rather than a shrug.

    A closed stdin is a no whatever the default: Enter means a person read the
    list and pressed a key, and no stdin at all means nobody did.
    """
    try:
        answer = input(f"{question} {'(Y/n)' if default else '[y/N]'} ").strip().lower()
    except EOFError:
        return False
    if not answer:
        return default
    return answer in ("y", "yes")


def _ask(question: str, default: str = "") -> str:
    """Ask one question on stdin, returning *default* on a bare Enter.

    A closed stdin raises rather than silently taking the default: the wizard's
    questions have no answer that is safe to invent, and a scaffold built from
    guesses is worse than a refusal that says which question went unanswered.
    """
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{question}{suffix}: ").strip()
    except EOFError as exc:
        raise PluginError(
            f"no answer to {question!r}, and stdin is closed. Pass the answers "
            "as options instead - `plugins create --help` lists them."
        ) from exc
    return answer or default


#: The answer that stops the wizard, wherever one is offered.
ABORT = "abort"


def _ask_choice(question: str, options: dict[str, str]) -> str:
    """Ask for one of *options* by number, or by name, until an answer fits.

    Numbered because these are line prompts: every answer costs an Enter, so
    the shortest one worth offering is a single digit.  The names still work,
    since a reader who has just read `captioning` off the screen should not be
    told off for typing it.
    """
    keys = list(options)
    width = max(len(key) for key in keys)
    print(f"\n{question}")
    for number, key in enumerate(keys, start=1):
        print(f"  {number}  {key:<{width}}  {options[key]}")
    while True:
        answer = _ask("Choose", "1").lower()
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        if answer in options:
            return answer
        print(f"  Choose 1 to {len(keys)}, or type the name.")


def _ask_paragraph(question: str) -> str:
    """Ask for as many lines as the contributor wants, ending on a blank one.

    What a plugin should do is the one answer that does not fit on a line, and
    it is the answer everything downstream is built from - the README, the
    class description, the prompt handed to a coding agent. Truncating it to
    what fits before the Enter key would be the wizard's own fault.
    """
    print(f"\n{question}")
    print("(Several lines are fine. Finish with an empty line.)")
    lines: list[str] = []
    while True:
        try:
            line = input("  ")
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line.strip())
    return " ".join(lines)


def _ask_name(kind: str, checkout: Path) -> str:
    """Ask for a plugin name until it is one, explaining each refusal.

    Checked against the checkout, which is why the clone happens before this
    question rather than after all of them: a name the repository already
    publishes is the commonest thing to get wrong, and finding out after
    answering everything means answering everything again.
    """
    while True:
        name = _ask("Name (snake_case, e.g. edge_glow)")
        try:
            plugin_create.check_name_free(checkout, name, kind)
            return name
        except PluginError as exc:
            print(f"  {exc}")


def _report_readiness(readiness: plugin_create.ForkReadiness) -> bool:
    """Print what this machine can do about a pull request, and ask if it cannot.

    Asked before anything is cloned, because the answer decides whether the
    last two steps this command prints are true - and being told at the end
    that you cannot push is being told too late to do anything about it.
    """
    print(f"\n{readiness.explanation}")
    if readiness.mode != plugin_create.MANUAL:
        return True
    print(f"{readiness.remedy}")
    return _confirm("Carry on and set the plugin up anyway?")


def _note_readiness(readiness: plugin_create.ForkReadiness) -> None:
    """Say how this checkout will reach GitHub, without asking anything.

    The non-interactive half of `_report_readiness`. "origin is the upstream
    repository" with nothing to explain it reads as something having gone
    wrong, when for a maintainer it is the expected answer.
    """
    print(readiness.explanation)


def _plugin_answers(
    args: argparse.Namespace, checkout: Path, kind: str
) -> dict[str, object]:
    """Ask for whatever was not given on the command line."""
    purpose = args.purpose or _ask_paragraph("What should it do?")
    name = args.name or _ask_name(kind, checkout)
    plugin_license = args.plugin_license or _ask(
        "\nLicence for your own code (SPDX)", "MIT"
    )
    return {"purpose": purpose, "name": name, "license": plugin_license}


def _ask_kind() -> str | None:
    """Ask what kind of plugin this is, or None if the answer is to stop.

    The last question asked before anything is created, and so the one that
    carries the way out: by the time a name is being chosen there is a fork on
    the contributor's account and a clone on their disk.
    """
    answer = _ask_choice(
        "What kind of plugin is it?",
        {
            plugin_install.IMAGE: (
                "turns a picture into another picture (the Filters menu)"
            ),
            plugin_install.CAPTIONING: ("turns an image into tags or a description"),
            ABORT: "stop here, creating nothing",
        },
    )
    return None if answer == ABORT else answer


def _cmd_plugins_create(args: argparse.Namespace) -> int:
    """Set a plugin up as a branch, asking for whatever was not given."""
    interactive = not (args.name and args.kind)
    if interactive and not sys.stdin.isatty():
        raise PluginError(
            "a name and --kind are needed, and there is no terminal to ask on. "
            "Pass them as arguments: `plugins create <name> --kind image`."
        )

    readiness = (
        plugin_create.fork_readiness()
        if args.fork
        else plugin_create.ForkReadiness(
            plugin_create.MANUAL,
            "--no-fork was given, so nothing will be forked.",
            f"Fork https://github.com/{plugin_install.PLUGINS_REPO} on the web "
            "and point `origin` at it when you are ready to push.",
        )
    )
    if interactive:
        if not _report_readiness(readiness):
            print("Nothing was created.")
            return EXIT_REFUSED
    else:
        _note_readiness(readiness)

    directory = Path(args.directory).expanduser()
    checkout_state = None
    kind = args.kind
    if interactive:
        # The kind is asked before anything is created, because it is the last
        # moment at which stopping costs nothing: the step after it forks the
        # repository onto the contributor's account and clones it.
        if kind is None:
            kind = _ask_kind()
            if kind is None:
                print("\nStopped. Nothing was forked, cloned or created.")
                return EXIT_REFUSED
        # Cloned before the remaining questions, so `_ask_name` can check the
        # name against what the repository actually publishes and ask again.
        print("\nFetching the plugins repository...")
        checkout_state = plugin_create.obtain_checkout(
            directory, fork=readiness.can_fork
        )
    answers = (
        _plugin_answers(args, directory, kind)
        if interactive
        else {
            "purpose": args.purpose,
            "name": args.name,
            "license": args.plugin_license or "MIT",
        }
    )

    result = plugin_create.create(
        answers["name"],
        kind,
        directory=directory,
        example=args.example,
        branch=args.branch,
        display_name=args.display_name,
        description=args.description,
        purpose=answers["purpose"],
        author=args.author,
        plugin_license=answers["license"],
        readiness=readiness,
        checkout_state=checkout_state,
    )
    _report_created(result)

    agent = args.agent
    if interactive and agent is None:
        agent = _ask_choice(
            "Hand it to a coding agent?",
            {
                **{
                    name: f"print a `{name}` command that writes the plugin"
                    for name in sorted(plugin_create.AGENTS)
                },
                "no": "just show me the steps",
            },
        )
    if agent and agent != "no":
        _report_agent_command(agent, result)
    return EXIT_OK


def _report_created(result: plugin_create.CreateResult) -> None:
    """Say what landed on disk, and what has to happen to it."""
    origin = "your fork" if result.forked else plugin_install.PLUGINS_REPO
    print()
    print(
        f"{'Reused' if result.reused else 'Cloned into'} {result.checkout} "
        f"(origin: {origin})"
    )
    print(f"Branch:  {result.branch}")
    print(f"Plugin:  {result.folder}  (copied from {result.example})")
    print()

    for warning in result.warnings:
        print(f"warning: {warning}")
    if result.warnings:
        print()

    # Paths are printed relative to the checkout because every command below
    # runs from inside it, and an absolute path in step 4 would not paste into
    # the `git add` in step 5.
    folder = result.folder.relative_to(result.checkout)
    # Two steps, because only the first is yours: `plugins submit` runs the
    # checks, commits, pushes and opens the pull request. A list of the six
    # commands it runs would be a list of things to get wrong by hand.
    print("Next:")
    print(f"  1. cd {result.checkout}")
    print(f"     Write the plugin:  {result.module.relative_to(result.checkout)}")
    print(f"     Expand the README: {result.readme.relative_to(result.checkout)}")
    if result.kind == plugin_install.CAPTIONING:
        print(f"     Try it:            {invoked_as()} plugins test {folder}")
    else:
        # `plugins test` checks captioning plugins only, so pointing an image
        # plugin at it would send someone to a command that refuses them.
        print(
            f"     Try it:            {invoked_as()} plugins install {folder} --force"
        )
        print("                        then use it from the Filters menu")
    print(f"  2. {invoked_as()} plugins submit")
    print("     Checks it, commits it, pushes it, opens the pull request.")


def _report_agent_command(agent: str, result: plugin_create.CreateResult) -> None:
    """Print the command that hands the plugin to a coding agent.

    Printed rather than run. Starting an agent that edits a checkout is the
    contributor's decision to make in their own terminal, where they can see
    what it does, and it belongs to step 2 rather than to this command.
    """
    command = plugin_create.agent_command(agent, result)
    print()
    print(f"To have {agent} write it, paste this anywhere:")
    print()
    print(f"  {command}")
    print()
    print(
        "Read what it writes before you commit it: a plugin runs unsandboxed "
        "in the server process, and a reviewer will ask what you ran it against."
    )


def _cmd_plugins_submit(args: argparse.Namespace) -> int:
    """Run the checks, commit the plugin, push it and open the pull request."""
    submission = plugin_create.find_submission(Path(args.directory), args.name)
    print(f"Plugin:  {submission.folder}")
    print(f"Branch:  {submission.branch}")

    if args.skip_checks:
        print("\nChecks skipped.")
    else:
        missing = plugin_create.missing_tools(submission)
        if missing:
            raise PluginError(plugin_create.dev_setup_hint(submission, missing))
        print(f"\nRunning the repository's checks with {submission.python}")
        failed = plugin_create.run_checks(submission)
        if failed:
            # Stopping here is the point of running them: the same failure in
            # CI costs a red pull request and a force-push to fix.
            raise PluginError(
                f"{', '.join(failed)} failed. Fix that and run this again, or "
                "pass --skip-checks to submit anyway."
            )
        print("\nChecks passed.")

    if args.dry_run:
        print("Stopping here: --dry-run. Nothing was committed or pushed.")
        return EXIT_OK

    message = args.message or f"Add {submission.name}"
    tested = args.tested
    if tested is None:
        if not sys.stdin.isatty():
            raise PluginError(
                "the pull request has to say what you tested the plugin "
                "against, and there is no terminal to ask on. Pass --tested."
            )
        tested = _ask_paragraph(
            "What did you test it against? (which model, which PixlStash "
            "version, what hardware)"
        )
    if not tested.strip():
        raise PluginError(
            "the pull request has to say what you tested the plugin against. "
            "CI checks the shape of a model-backed plugin and never runs the "
            "model, so this is what makes it reviewable."
        )

    print(
        f"\nAbout to commit {submission.folder.name}, push {submission.branch} "
        f"to origin, and open a pull request on {plugin_install.PLUGINS_REPO}."
    )
    if not args.yes and not _confirm("Go ahead?"):
        print("Nothing was pushed.")
        return EXIT_REFUSED

    plugin_create.commit(submission, message)
    plugin_create.push(submission)
    url = plugin_create.open_pull_request(
        submission, message, plugin_create.pull_request_body(submission, tested)
    )
    print(f"\n{url}")
    return EXIT_OK


def _report_dependencies(
    changes: list[plugin_install.DependencyChange], *, force: bool
) -> bool:
    """List what pip would install, and say whether it may go ahead.

    Every package is named, transitive ones included: a plugin's
    requirements.txt asking for one package routinely pulls in dozens, and
    "pip will install moondream2" is not what is about to happen to the
    environment PixlStash runs in.
    """
    if not changes:
        print("Everything it needs is already installed.")
        return True

    print("\nThis operation will install the following Python packages:")
    width = max(len(change.name) for change in changes)
    for change in changes:
        note = f"replaces {change.installed}" if change.moves else "new"
        print(f"  {change.name:<{width}}  {change.version:<12}  {note}")

    moved = [change for change in changes if change.moves]
    if not moved:
        return True

    # The refusal this whole mechanism exists for. PixlStash pins every
    # dependency it has, so a plugin that replaces one is a plugin that can
    # stop the application starting, and the damage would not show until the
    # next boot, long after anyone would connect it to installing a plugin.
    print()
    for change in moved:
        print(
            f"warning: this replaces {change.name} {change.installed} with "
            f"{change.version}, which PixlStash itself is using."
        )
    if not force:
        print(
            "\nRefused: a plugin may add packages, but not change one that is "
            "already in use. Install it without --with-deps and put its "
            "dependencies somewhere of your own, or pass --force-deps if you "
            "accept that PixlStash may stop working.",
            file=sys.stderr,
        )
        return False
    print("Proceeding anyway: --force-deps.")
    return True


def _cmd_plugins_install(args: argparse.Namespace) -> int:
    """Validate a plugin source, say where it lands, and copy it there."""
    with plugin_install.materialise(args.source, args.ref) as root:
        plan = plugin_install.plan_install(root, strict=args.strict)

        print(f"Source:      {args.source}")
        print(f"Kind:        {plugin_install.KIND_LABELS[plan.kind]}")
        print(f"Plugin:      {plan.name}  ({plan.display_name})")
        print(f"Destination: {plan.destination}")
        for warning in plan.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        print(
            "Plugin code runs unsandboxed, in the server process, with your "
            "permissions."
        )

        changes: list[plugin_install.DependencyChange] = []
        if args.with_deps and plan.requirements:
            # Resolved before anything is copied. pip is asked what it would
            # do, and it is asked first, so a plugin whose dependencies cannot
            # be had leaves nothing behind to clean up.
            print(f"\nResolving {plan.requirements.name}...")
            changes = plugin_install.resolve_requirements(plan.requirements)
            if not _report_dependencies(changes, force=args.force_deps):
                return EXIT_REFUSED
        elif plan.requirements:
            print(
                f"note: this plugin ships {plan.requirements.name}. It is NOT "
                "installed; pass --with-deps if you want it."
            )

        if args.dry_run:
            print("Dry run: nothing was written.")
            return EXIT_OK
        # Default yes only when the packages have just been listed one by one.
        question, default = ("Is this OK", True) if changes else ("Install it?", False)
        if not args.yes and not _confirm(question, default):
            print("Cancelled. Nothing was written.")
            return EXIT_REFUSED

        plugin_install.install(plan, force=args.force)
        if changes:
            # The resolution that was shown and agreed to, not the file it came
            # from: re-reading the file would resolve it a second time and
            # could install something nobody was asked about.
            plugin_install.install_requirements(changes)

    print(f"Installed {plan.name} to {plan.destination}")
    if plan.kind == plugin_install.CAPTIONING:
        print("Restart PixlStash Server to load it.")
    else:
        print("It appears the next time the Filters menu is listed.")
    return EXIT_OK


def _cmd_plugins_test(args: argparse.Namespace) -> int:
    """Load a plugin as the server does and report what would register."""
    # Local import: this is the one verb that imports the plugin system (and,
    # with --image, whatever the plugin itself pulls in).
    from pixlstash import plugin_check

    # Printed *before* the load, because after it the plugin's code has
    # already run - and if the plugin hangs at import, this line is the last
    # thing on screen and the one that explains what is hanging.
    print(
        f"About to run {args.path} in this process, unsandboxed, with your "
        "permissions.\nThis is a development check, not a security check."
    )
    report = plugin_check.check_plugin(args.path, image=args.image)
    for failure in report.failures:
        # Not "Loaded <path>" first: a plugin that raised on import did not
        # load, and saying so above the error is a contradiction the reader
        # has to resolve. Whether it loaded is what the rest of this says.
        print(f"error: {failure}", file=sys.stderr)

    for check in report.checked:
        schema = check.schema
        capabilities = ", ".join(
            label
            for label, supported in (
                ("captions", schema.get("supports_descriptions")),
                ("tags", schema.get("supports_tags")),
            )
            if supported
        )
        print(
            f'\nRegistered "{check.name}"  '
            f"({schema.get('display_name') or check.name})  - "
            f"{capabilities or 'no capability flags'}"
        )
        _print_parameters(schema.get("parameters"))
        if check.output is not None:
            print(f"  Ran over {args.image} and got:")
            if isinstance(check.output, dict):
                for key, value in check.output.items():
                    print(f"    {key} -> {value!r}")
            else:
                print(f"    {check.output!r}")
        for problem in check.problems:
            print(f"  problem: {problem}", file=sys.stderr)
        for warning in check.warnings:
            # Said, never failed on: the plugin works with these. Failing the
            # command on something cosmetic would make it less useful than the
            # restart it is meant to replace.
            print(f"  warning: {warning}", file=sys.stderr)

    if not report.ok:
        print(
            "\nThis plugin would not work as it stands. Fix the above and run "
            "this again - no restart needed.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    print(
        "\nIt loads, registers and renders. That is a contract check, and it "
        "is neither a quality one nor a safety one: it says nothing about "
        "whether the captions are any good, and nothing about whether this "
        "plugin is safe to install - it just ran it. A plugin that hangs at "
        "import would hang the server's boot the same way it would hang this "
        "command."
    )
    if not args.image:
        print("Pass --image to run it over a picture as well.")
    return EXIT_OK


def _print_parameters(parameters: object) -> None:
    """Print the schema's parameters, one per line, as the UI would see them."""
    if not isinstance(parameters, list) or not parameters:
        print("  No parameters.")
        return
    for definition in parameters:
        if not isinstance(definition, dict):
            print(f"  {definition!r}")
            continue
        print(
            f"  {definition.get('name')}: {definition.get('type')} "
            f"= {definition.get('default')!r}"
        )


def _cmd_plugins_available(args: argparse.Namespace) -> int:
    """Print the published catalogue, optionally filtered by a search word."""
    entries = plugin_install.catalogue(args.ref)
    matched = [entry for entry in entries if plugin_install.matches(entry, args.query)]

    if not matched:
        # The two empty results mean different things, and telling them apart is
        # the difference between "try another word" and "something is wrong".
        if args.query and entries:
            print(f"No published plugin matches {args.query!r}.")
            print(f"Drop the word to see all {len(entries)}.")
        else:
            print(f"{plugin_install.PLUGINS_REPO} publishes no plugins at this ref.")
        return EXIT_OK

    seen_installed = False
    for kind in (plugin_install.CAPTIONING, plugin_install.IMAGE):
        of_kind = [entry for entry in matched if entry.kind == kind]
        if not of_kind:
            continue
        print(f"\n{plugin_install.KIND_LABELS[kind]}")
        width = max(len(entry.name) for entry in of_kind)
        for entry in of_kind:
            marker = "  "
            if entry.installed:
                marker, seen_installed = "* ", True
            print(f"  {marker}{entry.name:<{width}}  {entry.display_name}")
            detail = entry.problem or entry.summary
            if detail:
                print(f"      {' ' * width}{detail}")
            # Only shown once declared: every published plugin predates the
            # header, so printing "author: -" on all of them would be noise.
            credit = "  ".join(part for part in (entry.author, entry.license) if part)
            if credit:
                print(f"      {' ' * width}{credit}")

    print()
    if seen_installed:
        print("* already installed")
    print(f"Install one with:  {invoked_as()} plugins install <name>")
    return EXIT_OK


def _cmd_plugins_list(_args: argparse.Namespace) -> int:
    """Print both plugin directories, grouped by kind, with a marker legend."""
    listing = plugin_install.list_installed()
    if not any(listing.values()):
        print("No plugins are installed.")
        for kind in (plugin_install.CAPTIONING, plugin_install.IMAGE):
            print(
                f"  {plugin_install.KIND_LABELS[kind]}: {plugin_install.user_dir(kind)}"
            )
        print(f"See what is published:  {invoked_as()} plugins available")
        print(f"Add one with:           {invoked_as()} plugins install <name>")
        return EXIT_OK

    notes = {
        plugin_install.CAPTIONING: "loaded at server start",
        plugin_install.IMAGE: "re-scanned on every use",
    }
    seen_problem = False
    seen_shadow = False
    for kind in (plugin_install.CAPTIONING, plugin_install.IMAGE):
        entries = listing[kind]
        print(
            f"\n{plugin_install.KIND_LABELS[kind]}  "
            f"({plugin_install.user_dir(kind)}, {notes[kind]})"
        )
        if not entries:
            print("    (none)")
            continue
        name_width = max(max(len(entry.name) for entry in entries), len("NAME"))
        label_width = max(
            max(len(entry.display_name) for entry in entries), len("DISPLAY NAME")
        )
        for entry in entries:
            marker = "  "
            if entry.problem:
                marker, seen_problem = "! ", True
            elif entry.shadows_builtin:
                marker, seen_shadow = "* ", True
            suffix = ""
            if entry.shadows_builtin:
                suffix = "  (replaces the built-in)"
            print(
                f"{marker}{entry.name:<{name_width}}  "
                f"{entry.display_name:<{label_width}}  {entry.entry}{suffix}"
            )
            if entry.problem:
                print(f"{'':4}{entry.problem}")

    if seen_problem or seen_shadow:
        legend = []
        if seen_problem:
            legend.append("! = will not load as it stands")
        if seen_shadow:
            legend.append("* = replaces a built-in")
        print("\n" + "    ".join(legend))
    print(
        "\nRead statically: no plugin is imported here, so a failure that only "
        "happens at import - a missing dependency, say - is invisible above. "
        "For a captioning plugin the server reports it under Settings › "
        "Auto-tagging; for an image filter it is reported nowhere, so check the "
        "server log."
    )
    return EXIT_OK


def _cmd_plugins_remove(args: argparse.Namespace) -> int:
    """Delete an installed plugin, after saying exactly what will be deleted."""
    kind, path = plugin_install.resolve_removal(args.name, args.kind)
    restores_builtin = (
        kind == plugin_install.IMAGE and args.name in plugin_install.builtin_names(kind)
    )

    print(f"This deletes {path}")
    if restores_builtin:
        print(f"The built-in {args.name} filter it replaces comes back.")
    if not args.yes and not _confirm("Delete it?"):
        print("Cancelled. Nothing was deleted.")
        return EXIT_REFUSED

    plugin_install.remove(path)
    print(f"Removed {args.name}.")
    if restores_builtin:
        print(f"The built-in {args.name} is in use again.")
    elif kind == plugin_install.CAPTIONING:
        print("Restart PixlStash Server to stop loading it.")
    return EXIT_OK


def _print_activation_note(library: Library) -> None:
    """Say what happens next, which differs for the very first library."""
    if library.is_active:
        print("It is the active library.")
    else:
        print("Switch to it in Settings › Libraries.")


if __name__ == "__main__":
    sys.exit(main())
