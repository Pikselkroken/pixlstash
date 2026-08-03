"""The PixlStash CLI: ``pixlstash-cli`` / ``python -m pixlstash.cli``.

Creates, attaches, detaches and lists libraries. In the MVP this is the only
way to change the registry: the server exposes no HTTP route that accepts a
host path, so nothing reachable over the network can point PixlStash at a new
folder.

**Authentication is filesystem access.** Shell access as the OS user that owns
the hub file *is* the credential, the same model as ``psql`` or ``docker``.
There is no login here and no stored credential; Docker deployments reach it
with ``docker exec``.

Nothing in the command set destroys data. ``detach`` deregisters a library and
never touches its files, and there is deliberately no ``--delete`` flag.
"""

from __future__ import annotations

import argparse
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

# Exit codes. 0 success, 1 a refusal the user can act on (not a vault, already
# registered, library is active), 2 argparse usage error, 3 the hub itself
# could not be opened.
EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_HUB_UNAVAILABLE = 3


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the library CLI."""
    parser = argparse.ArgumentParser(
        prog="pixlstash-cli",
        description=(
            "PixlStash command line. Run this on the machine hosting "
            "PixlStash, signed in as the user that owns it."
        ),
    )
    parser.add_argument(
        "--hub",
        default=None,
        metavar="PATH",
        help=f"Hub database to use (default: {default_hub_path()}).",
    )

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
    subparsers = libraries.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list", help="Show the registered libraries and which one is active."
    )
    list_parser.set_defaults(handler=_cmd_list)

    create_parser = subparsers.add_parser(
        "create", help="Create a folder, start an empty library in it, register it."
    )
    create_parser.add_argument("folder", help="Folder to create the library in.")
    create_parser.add_argument(
        "--name", default=None, help="Display name (default: the folder's name)."
    )
    create_parser.set_defaults(handler=_cmd_create)

    attach_parser = subparsers.add_parser(
        "attach", help="Register a library that already exists on disk."
    )
    attach_parser.add_argument("folder", help="Folder containing vault.db.")
    attach_parser.add_argument(
        "--name", default=None, help="Display name (default: the folder's name)."
    )
    attach_parser.set_defaults(handler=_cmd_attach)

    detach_parser = subparsers.add_parser(
        "detach",
        help="Forget a library. No files are removed and nothing in the folder changes.",
    )
    detach_parser.add_argument("library", help="Library name or id from `list`.")
    detach_parser.set_defaults(handler=_cmd_detach)

    relocate_parser = subparsers.add_parser(
        "relocate",
        help="Point a library at a folder that has moved, keeping its share links.",
    )
    relocate_parser.add_argument("library", help="Library name or id from `list`.")
    relocate_parser.add_argument("folder", help="Where the library now lives.")
    relocate_parser.set_defaults(handler=_cmd_relocate)

    backup_parser = subparsers.add_parser(
        "backup",
        help="Write a library and the hub to a single archive.",
        description=(
            "Writes a consistent copy even while the library is open. The "
            "archive contains your credentials, so it is written owner-readable "
            "only; pictures in reference folders are outside the library and are "
            "not included."
        ),
    )
    backup_parser.add_argument("library", help="Library name or id from `list`.")
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
    backup_parser.set_defaults(handler=_cmd_backup)

    migrate_parser = subparsers.add_parser(
        "prepare-legacy-identity",
        help="Explicitly approve one legacy owner/token migration before startup.",
    )
    migrate_parser.add_argument(
        "folder", help="Legacy library folder containing vault.db."
    )
    migrate_parser.set_defaults(handler=_cmd_prepare_legacy_identity)

    rename_parser = subparsers.add_parser("rename", help="Change a library's name.")
    rename_parser.add_argument("library", help="Library name or id from `list`.")
    rename_parser.add_argument("new_name", help="The new display name.")
    rename_parser.set_defaults(handler=_cmd_rename)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

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
        print("Add one with:  pixlstash-cli libraries attach /path/to/library")
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
        f'Add it back at any time with:  pixlstash-cli libraries attach "{library.path}"'
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


def _print_activation_note(library: Library) -> None:
    """Say what happens next, which differs for the very first library."""
    if library.is_active:
        print("It is the active library.")
    else:
        print("Switch to it in Settings › Libraries.")


if __name__ == "__main__":
    sys.exit(main())
