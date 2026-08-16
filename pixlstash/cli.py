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
is the one that looks like an exception and is not — it writes only to a folder
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
from typing import Sequence

from pixlstash.hub.db import HubDatabase, HubPermissionError, default_hub_path
from pixlstash.hub.registry import (
    Library,
    LibraryError,
    LibraryRegistry,
    resolve_path,
)
from pixlstash.hub.schema import HubSchemaTooNewError
from pixlstash import plugin_install
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


def invoked_as() -> str:
    """Return the command the user typed to get here.

    Every usage line, error and "add one with:" hint names this, so on a desktop
    install they must not say ``pixlstash-cli``: that console script is sealed
    inside the app image and is on nobody's PATH. The launcher that ran us
    declares the working form in ``PIXLSTASH_CLI_COMMAND`` (see
    :mod:`pixlstash.hub.cli_hint`, which reads the same variable to fill the
    Settings panel). Everywhere else the console script is exactly right.
    """
    return os.environ.get("PIXLSTASH_CLI_COMMAND", "").strip() or "pixlstash-cli"


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
            "whose folder is missing — reconnect the drive, or point it "
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
            "the share links and API tokens issued from it — but only while "
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
            "library is refused — switch to another one first."
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
            "vault.db, hub.db and — unless --metadata-only was given — the "
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
            "its vault.db and into the hub. Needed once, before the first "
            "normal start, on an installation that predates the hub: startup "
            "deliberately will not guess which vault to trust. This records "
            "the approval and nothing else — the copy, the verification and "
            "the blanking of the old identity happen the next time PixlStash "
            "starts."
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
    """Add the `plugins` group: install, list, remove."""
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
    install_parser.set_defaults(handler=_cmd_plugins_install)

    test_parser = commands.add_parser(
        "test",
        help="Load a captioning plugin as the server does and check it.",
        # Wrapped by hand, like the top-level parser: the safety caveat is the
        # first thing to read and has to stay its own paragraph rather than
        # being reflowed into the middle of a block.
        description=(
            "A development aid for writing a plugin. NOT a security scanner:\n"
            "it does not tell you whether a plugin is safe, it RUNS it. The\n"
            "module body — and the model itself, with --image — executes in\n"
            "this process, with your permissions, exactly as it would in the\n"
            "server. Nothing is sandboxed, and nothing here inspects what the\n"
            "code does. Only test a plugin you would have installed anyway.\n"
            "\n"
            "What it does check: that the plugin imports the way the server\n"
            "imports it at start-up, that every plugin class it defines\n"
            "registers, and that its parameter schema is one the settings\n"
            "screen can render — the last of which the server does not check\n"
            "and which fails quietly when it is wrong. Prints what registered.\n"
            "\n"
            "A `problem:` means the plugin will not work and exits 1. A\n"
            "`warning:` means it works and could be tidier — a parameter with\n"
            "no label, no capability flag set — and exits 0.\n"
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
            "init() will still do so — nothing here can prevent that."
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
            "that only happens at import — a missing dependency, say — is not "
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
        # make is handing its author the owner account on this machine — which
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


def _confirm(question: str) -> bool:
    """Ask for a y/n on stdin. A closed stdin is a no, not a yes."""
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


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

        requirements: list[str] = []
        if args.with_deps and plan.requirements:
            requirements = plugin_install.read_requirements(plan.requirements)
            print("pip will install: " + (", ".join(requirements) or "(nothing)"))
        elif plan.requirements:
            print(
                f"note: this plugin ships {plan.requirements.name}. It is NOT "
                "installed; pass --with-deps if you want it."
            )

        if args.dry_run:
            print("Dry run: nothing was written.")
            return EXIT_OK
        if not args.yes and not _confirm("Install it?"):
            print("Cancelled. Nothing was written.")
            return EXIT_REFUSED

        plugin_install.install(plan, force=args.force)
        if requirements:
            plugin_install.install_requirements(plan.requirements)

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
    # already run — and if the plugin hangs at import, this line is the last
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
            f"({schema.get('display_name') or check.name})  — "
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
            "this again — no restart needed.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    print(
        "\nIt loads, registers and renders. That is a contract check, and it "
        "is neither a quality one nor a safety one: it says nothing about "
        "whether the captions are any good, and nothing about whether this "
        "plugin is safe to install — it just ran it. A plugin that hangs at "
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
        "happens at import — a missing dependency, say — is invisible above. "
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
