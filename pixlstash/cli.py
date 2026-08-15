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
touches its files, and there is deliberately no ``--delete`` flag. ``plugins
remove`` does delete, because a CLI that installs plugins and cannot uninstall
them is not worth shipping; what it guarantees instead is that the path it
deletes is always inside one of the two plugin directories.
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
from pixlstash import plugin_install
from pixlstash.plugin_install import PluginError

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
    )
    install_parser.add_argument(
        "source",
        help=(
            "A plugin name from the plugins repository, or a path to a .zip, "
            "a folder, or a single .py file."
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

    list_parser = commands.add_parser(
        "list", help="Show the installed plugins, grouped by kind."
    )
    list_parser.set_defaults(handler=_cmd_plugins_list)

    remove_parser = commands.add_parser(
        "remove", help="Delete an installed plugin. This removes files."
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


def _cmd_plugins_list(_args: argparse.Namespace) -> int:
    """Print both plugin directories, grouped by kind, with a marker legend."""
    listing = plugin_install.list_installed()
    if not any(listing.values()):
        print("No plugins are installed.")
        for kind in (plugin_install.CAPTIONING, plugin_install.IMAGE):
            print(
                f"  {plugin_install.KIND_LABELS[kind]}: {plugin_install.user_dir(kind)}"
            )
        print("Add one with:  pixlstash-cli plugins install hello_world_stamp")
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
