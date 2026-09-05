import os
import argparse
import logging
import sys
import json
import getpass
import shlex
import time

from platformdirs import user_config_dir
from passlib.hash import bcrypt


from pixlstash.pixl_logging import setup_logging, get_logger, hold_log_output
from pixlstash.server import Server
from pixlstash.startup_checks import StartupCheckError
from pixlstash.hub.bootstrap import (
    HubBootstrapError,
    UnusableVaultError,
    VAULT_RECREATE_ENV,
)
from pixlstash.hub.db import HubPermissionError
from pixlstash.startup_permissions import (
    PERMISSION_REPAIR_ENV,
    find_startup_permission_issues,
    format_permission_problem,
    repair_permission_issues,
)
from pixlstash.trusted_sqlite import TrustedSQLiteLocationError

logger = get_logger(__name__)

APP_NAME = "pixlstash"
SERVER_CONFIG_PATH = os.path.join(user_config_dir(APP_NAME), "server-config.json")


def _resolve_log_level(value):
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.debug(
            "Could not parse log level %r as integer; trying string lookup.", value
        )

    if isinstance(value, str):
        level_name = value.strip().upper()
        level_map = {
            "CRITICAL": logging.CRITICAL,
            "ERROR": logging.ERROR,
            "WARNING": logging.WARNING,
            "INFO": logging.INFO,
            "DEBUG": logging.DEBUG,
            "NOTSET": logging.NOTSET,
        }
        if level_name in level_map:
            return level_map[level_name]
        # Provide a gentle fallback for unexpected values.
        print(f"Unknown log level '{value}', defaulting to INFO.")
    return logging.INFO


def _parse_yes_no(value, default: bool) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    if raw in {"y", "yes", "true", "1", "on"}:
        return True
    if raw in {"n", "no", "false", "0", "off"}:
        return False
    return default


def _permission_fix_commands(issues) -> list[str]:
    """Return copy/pasteable commands for a non-interactive POSIX launch."""

    return [
        f"chmod {issue.repaired_mode:03o} {shlex.quote(issue.path)}" for issue in issues
    ]


def _prepare_startup_permissions(
    server_config_path: str,
    server_config: dict,
) -> None:
    """Warn about loose permissions and offer to tighten them; never block.

    A terminal is asked inline. Anything else (a service, Docker, Electron)
    is told what to run and started anyway, unless
    ``PIXLSTASH_REPAIR_PERMISSIONS=1`` asks for the repair up front.
    """

    repair_requested = os.environ.get(PERMISSION_REPAIR_ENV) == "1"

    # Usually one pass is enough. A second pass can discover an active library
    # stored in the hub only after the hub directory itself has been repaired.
    for _ in range(3):
        issues = find_startup_permission_issues(
            server_config_path,
            str(server_config.get("image_root") or "") or None,
        )
        if not issues:
            return

        print(format_permission_problem(issues), file=sys.stderr)
        if not repair_requested and getattr(sys.stdin, "isatty", lambda: False)():
            try:
                answer = input("\nFix permissions now? [Y/n] ").strip().lower()
            except EOFError:
                answer = "n"
            repair_requested = answer in {"", "y", "yes"}
            if not repair_requested:
                print("Permissions were not changed.", file=sys.stderr)

        if not repair_requested:
            print("\nFix them with:", file=sys.stderr)
            for command in _permission_fix_commands(issues):
                print(f"  {command}", file=sys.stderr)
            return

        try:
            repair_permission_issues(issues)
        except OSError as exc:
            print(f"\nPixlStash could not fix the permissions: {exc}", file=sys.stderr)
            return
        print("Permissions fixed.", file=sys.stderr)

    print(
        "PixlStash still found unsafe permissions after attempting the repair.",
        file=sys.stderr,
    )


VAULT_UNUSABLE_PREFIX = "PIXLSTASH_VAULT_UNUSABLE="


def _vault_unusable_signal(exc: UnusableVaultError) -> str:
    """The single-line record the desktop shell parses out of our output."""

    return VAULT_UNUSABLE_PREFIX + json.dumps(
        {
            "version": 1,
            "folder": exc.folder,
            "vault_path": exc.vault_path,
            "reason": exc.reason,
        },
        separators=(",", ":"),
    )


def _format_unusable_vault(exc: UnusableVaultError) -> str:
    """Say what is wrong, what the offer is, and what it costs - in that order."""

    return (
        f"PixlStash could not open the library database in {exc.folder}:\n"
        f"- {exc.reason}\n\n"
        "PixlStash can start over with a new, empty library database in that "
        "folder. The pictures are untouched and are offered for import again, "
        f"but the tags, scores, characters and history recorded in "
        f"{os.path.basename(exc.vault_path)} are not carried over.\n"
        "The old file is renamed, never deleted, so it can still be recovered."
    )


def _offer_vault_recreation(exc: UnusableVaultError) -> bool:
    """Ask whether the unopenable vault may be set aside, as this launch can ask.

    Returns True only when a human said yes to this process; the desktop shell
    answers by relaunching with ``PIXLSTASH_RECREATE_VAULT=1`` instead, exactly
    as it does for a permission repair, because Electron has no usable stdin.
    """

    with hold_log_output():
        print(_format_unusable_vault(exc), file=sys.stderr)

        if os.environ.get("PIXLSTASH_INSTALL_TYPE", "").lower() == "electron":
            # Human text stays in the log; the JSON line is the stable protocol.
            print(_vault_unusable_signal(exc), file=sys.stderr)
            return False

        if getattr(sys.stdin, "isatty", lambda: False)():
            try:
                answer = input("\nStart over with an empty library database? [y/N] ")
            except EOFError:
                answer = "n"
            if answer.strip().lower() in {"y", "yes"}:
                return True
            print("The library database was left alone.", file=sys.stderr)
            return False

    print(
        "\nStart PixlStash again with "
        f"{VAULT_RECREATE_ENV}=1 to accept that, or point image_root at "
        "another folder.",
        file=sys.stderr,
    )
    return False


def _prompt_legacy_identity_migration(library) -> bool:
    """Offer, at startup, what `pixlstash-cli libraries prepare-legacy-identity`
    otherwise requires as a separate manual step.

    Skipped for Electron, whose own setup wizard already offers this choice
    before the backend is ever launched. A non-interactive launch is told
    about it in the log instead of being asked, and either way leaves the
    vault exactly as untouched as declining would, with the CLI command still
    available. Defaults to **not** migrating on a bare Enter: this is
    irreversible (the vault stops being readable by pre-hub PixlStash
    afterwards), and the desktop wizard's equivalent checkbox
    (`importLegacyIdentity` in electron/src/renderer/setup.js) starts
    unchecked for the same reason.
    """
    is_electron = (
        os.environ.get("PIXLSTASH_INSTALL_TYPE", "").strip().lower() == "electron"
    )
    if is_electron:
        return False
    if not getattr(sys.stdin, "isatty", lambda: False)():
        logger.info(
            "%s still holds a pre-hub owner account and API tokens. Run "
            "`pixlstash-cli libraries prepare-legacy-identity %s` to migrate "
            "them into the hub, or start PixlStash in an interactive terminal "
            "to be asked.",
            library.path,
            shlex.quote(library.path),
        )
        return False

    # Asked from inside `Server.__init__`, between two boot log lines.
    with hold_log_output():
        print(
            f"\n{library.path} still holds an owner account and API tokens from "
            "before PixlStash introduced its hub. PixlStash can move them into "
            "the hub now, after which this library will no longer be readable "
            "as an owner/token store by versions of PixlStash older than the hub.",
            file=sys.stderr,
        )
        try:
            answer = (
                input("Migrate this library's identity into the hub now? [y/N] ")
                .strip()
                .lower()
            )
        except EOFError:
            answer = "n"
    return answer in {"y", "yes"}


def _prompt_library_switch(library, reason: str, alternatives: list):
    """Offer the attached libraries that open when the active one does not.

    Nothing else can offer this. The Settings pane that changes the active
    library needs the server that is failing to start, and the CLI has no verb
    for it, so without this prompt a vault deleted or replaced outside
    PixlStash is a start-up that can never succeed again. Electron and a
    non-interactive launch get the same list in the error text instead; the
    choice is never made for them, because silently opening a different library
    is how an import lands in the wrong one.
    """
    is_electron = (
        os.environ.get("PIXLSTASH_INSTALL_TYPE", "").strip().lower() == "electron"
    )
    if is_electron or not getattr(sys.stdin, "isatty", lambda: False)():
        return None

    with hold_log_output():
        print(
            f"\nPixlStash cannot open its library {library.name} ({library.path}):"
            f"\n  {reason}\n\nThese attached libraries do open:",
            file=sys.stderr,
        )
        for index, candidate in enumerate(alternatives, start=1):
            print(f"  {index}. {candidate.name} ({candidate.path})", file=sys.stderr)
        try:
            answer = input(
                "Open which one instead? [number, or Enter to stop] "
            ).strip()
        except EOFError:
            return None
    if not answer.isdigit() or not 1 <= int(answer) <= len(alternatives):
        return None
    return alternatives[int(answer) - 1]


def _should_prompt_bootstrap(server_config_path: str, force: bool) -> bool:
    if force:
        return True
    if not os.path.exists(server_config_path):
        return True
    try:
        with open(server_config_path, "r") as handle:
            data = json.load(handle)
        return not isinstance(data, dict)
    except Exception as exc:
        logger.warning(
            "Could not read server config %s (%s); prompting first-run bootstrap.",
            server_config_path,
            exc,
        )
        return True


def _bootstrap_server_config(server_config_path: str, force: bool = False) -> bool:
    if not _should_prompt_bootstrap(server_config_path, force):
        return False
    if not sys.stdin.isatty():
        return False

    config = Server.init_server_config(server_config_path)

    print("\nPixlStash first-run setup")
    print("Press Enter to keep defaults.\n")

    image_root_default = str(config.get("image_root") or "")
    image_root_input = input(f"Image storage path [{image_root_default}]: ").strip()
    image_root = (
        os.path.abspath(os.path.expanduser(image_root_input))
        if image_root_input
        else image_root_default
    )

    port_default = int(config.get("port", 9537))
    port = port_default
    while True:
        port_input = input(f"Server port [{port_default}]: ").strip()
        if not port_input:
            break
        try:
            parsed = int(port_input)
            if 1 <= parsed <= 65535:
                port = parsed
                break
        except Exception:
            logger.debug(
                "Port input %r is not a valid integer; prompting again.", port_input
            )
        print("Please enter a valid port between 1 and 65535.")

    ssl_default = bool(config.get("require_ssl", False))
    ssl_hint = "Y/n" if ssl_default else "y/N"
    ssl_input = input(f"Use HTTPS? [{ssl_hint}]: ").strip()
    require_ssl = _parse_yes_no(ssl_input, ssl_default)

    config["image_root"] = image_root
    config["port"] = port
    config["require_ssl"] = require_ssl
    config["cookie_secure"] = require_ssl
    with open(server_config_path, "w") as handle:
        json.dump(config, handle, indent=2)

    print(f"\nSaved setup to: {server_config_path}")
    print("You can rerun this wizard later with --bootstrap.\n")
    return True


def _prompt_bootstrap_credentials(server) -> None:
    """Ask for the owner's credentials, on a screen the boot log is not writing to.

    This runs after ``Server.__init__``, so the whole boot log and the first
    background tasks are already going past. Held output and a heading of its
    own are what make it a question rather than another line of start-up.
    """
    if not sys.stdin.isatty():
        return

    user = server.auth.user or server.auth.ensure_user()
    has_existing_credentials = bool(user and user.username and user.password_hash)

    with hold_log_output():
        print("\nPixlStash first-run credentials")
        if has_existing_credentials:
            keep_input = input("Keep existing username/password? [Y/n]: ").strip()
            keep_existing = _parse_yes_no(keep_input, True)
            if keep_existing:
                return
        else:
            setup_input = input(
                "Set username/password now before launch? [Y/n]: "
            ).strip()
            should_setup = _parse_yes_no(setup_input, True)
            if not should_setup:
                return

        existing_username = str(user.username).strip() if user and user.username else ""
        username = existing_username
        while True:
            prompt_suffix = f" [{existing_username}]" if existing_username else ""
            username_input = input(f"Username{prompt_suffix}: ").strip()
            if username_input:
                username = username_input
            if username:
                break
            print("Username cannot be empty.")

        while True:
            password = getpass.getpass("Password (min 8 chars): ")
            if len(password) < 8:
                print("Password must be at least 8 characters.")
                continue
            try:
                password_bytes = len(password.encode("utf-8"))
            except Exception:
                password_bytes = len(password)
            if password_bytes > 72:
                print("Password cannot exceed 72 bytes.")
                continue
            password_confirm = getpass.getpass("Confirm password: ")
            if password != password_confirm:
                print("Passwords do not match.")
                continue
            break

        server.auth.set_username(username)
        server.auth.set_password_hash(bcrypt.hash(password))
        print("Bootstrap credentials saved.\n")


def _force_utf8_streams():
    """Force UTF-8 on stdout/stderr so non-ASCII output never crashes startup.

    On Windows the standard streams default to the legacy ANSI codepage
    (typically ``cp1252``) rather than UTF-8. Any ``print`` of non-Latin-1
    characters - e.g. the box-drawing glyphs in the startup banner or the
    arrows in log messages - then raises ``UnicodeEncodeError`` and takes the
    whole backend down before the server can serve a request. Reconfiguring the
    streams to UTF-8 (with ``backslashreplace`` as a never-crash safety net for
    any stream that still can't encode a glyph) removes that failure class.

    Best-effort: in frozen/packaged builds ``sys.stdout`` may be ``None`` or a
    stream without ``reconfigure`` (Python < 3.7 semantics); any such case is
    logged and skipped rather than allowed to abort startup.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception as exc:
            logger.warning(
                "Could not reconfigure sys.%s to UTF-8 (%s); non-ASCII output "
                "may be mangled on this platform.",
                name,
                exc,
            )


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the server entry point."""
    parser = argparse.ArgumentParser(
        prog=f"{APP_NAME}-server",
        description=(
            f"Run the {APP_NAME} server. In a source checkout, where the "
            "entry point is not on PATH, the same options are accepted by "
            f"`python -m {APP_NAME}.app`."
        ),
        epilog=(
            "Every option acts during startup and the server then runs "
            "normally, except --clear-embeddings, which does its work and "
            "exits. Libraries and plugins are managed with a separate "
            f"command, `{APP_NAME}-cli`."
        ),
    )
    parser.add_argument(
        "--server-config",
        type=str,
        default=SERVER_CONFIG_PATH,
        metavar="PATH",
        help=(
            "Path to the server config file, which is created on first run "
            f"if it is missing (default: {SERVER_CONFIG_PATH})."
        ),
    )
    parser.add_argument(
        "--remove-password",
        action="store_true",
        help=(
            "Clear the stored username and password hash and log out every "
            "signed-in session, so the next sign-in sets them again. The "
            "server starts as usual afterwards."
        ),
    )
    parser.add_argument(
        "--clear-embeddings",
        action="store_true",
        help=(
            "Clear every picture's text and image embeddings, then exit "
            "without starting the server. Tags are not touched, and the "
            "embeddings are recomputed in the background the next time the "
            "server runs."
        ),
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "Run the interactive first-run setup - storage path, port, HTTPS, "
            "then the username and password - even if a config file already "
            "exists, and start the server afterwards. It needs a terminal: "
            "with stdin redirected the setup is skipped."
        ),
    )
    parser.add_argument(
        "--cleanup-missing-pictures",
        action="store_true",
        help=(
            "On startup, remove picture records whose source files are missing "
            "before thumbnail generation."
        ),
    )
    parser.add_argument(
        "--path-map",
        action="append",
        metavar="HOST_PATH:CONTAINER_PATH",
        default=[],
        help=(
            "Map a host-side path prefix to its mounted container path. "
            "May be repeated for multiple mappings. Docker use only. "
            "Example: --path-map /mnt/photos:/data/photos"
        ),
    )
    return parser


def main():
    _boot_t0 = time.perf_counter()
    _force_utf8_streams()
    args = build_parser().parse_args()

    ran_bootstrap = _bootstrap_server_config(args.server_config, force=args.bootstrap)
    Server.DEFAULT_CLEANUP_MISSING_PICTURES = bool(args.cleanup_missing_pictures)

    path_map: dict[str, str] = {}
    for entry in args.path_map or []:
        parts = entry.split(":", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            print(f"Invalid --path-map entry (expected HOST:CONTAINER): {entry!r}")
            return 1
        path_map[parts[0]] = parts[1]

    server_config = Server.init_server_config(args.server_config)
    _prepare_startup_permissions(args.server_config, server_config)

    log_level = _resolve_log_level(server_config.get("log_level"))
    log_file = server_config.get("log_file")
    if log_file and log_level != logging.INFO:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        setup_logging(log_file=log_file, log_level=log_level)
    else:
        setup_logging(log_level=log_level)

    def build_server() -> Server:
        return Server(
            server_config_path=args.server_config,
            path_map=path_map,
            legacy_identity_prompt=_prompt_legacy_identity_migration,
            library_switch_prompt=_prompt_library_switch,
        )

    try:
        try:
            server = build_server()
        except UnusableVaultError as exc:
            # Exactly one authorised retry, as the permission repair does. A
            # second UnusableVaultError falls through to the HubBootstrapError
            # clause below and is reported rather than looping.
            if not _offer_vault_recreation(exc):
                return 1
            os.environ[VAULT_RECREATE_ENV] = "1"
            server = build_server()
    except StartupCheckError as exc:
        print("Startup checks failed. Please resolve the following issues:")
        for failure in exc.failures:
            print(f"- {failure}")
        return 1
    except (HubPermissionError, TrustedSQLiteLocationError) as exc:
        # Suspicious cases (foreign owner, symlink/junction, replaced file) are
        # deliberately not offered to chmod, but they still deserve a concise
        # startup error rather than an implementation traceback.
        print("PixlStash could not safely open its database:", file=sys.stderr)
        print(f"- {exc}", file=sys.stderr)
        return 1
    except HubBootstrapError as exc:
        print("PixlStash could not prepare its library:", file=sys.stderr)
        print(f"- {exc}", file=sys.stderr)
        return 1

    if ran_bootstrap:
        _prompt_bootstrap_credentials(server)

    if args.remove_password:
        server.auth.remove_password_hash()
        # Continue running the server after removing the password hash

    if args.clear_embeddings:
        # Clear all text embeddings for all images
        from pixlstash.db_models.picture import Picture
        from sqlmodel import select

        vault = server.vault
        logger.info("Clearing all text embeddings for all images...")

        def clear_embeddings(session):
            pictures = session.exec(select(Picture)).all()
            logger.info(f"Found {len(pictures)} pictures to clear embeddings.")
            for pic in pictures:
                pic.text_embedding = None
                pic.image_embedding = None
                session.add(pic)
            session.commit()
            logger.info("All text and image embeddings cleared.")

        vault.db.run_task(clear_embeddings, priority=1)
        return None

    _t_engine = time.perf_counter()
    server.vault.ensure_ready()
    logger.info(
        "[boot] inference engine ready (models loaded): %.3fs",
        time.perf_counter() - _t_engine,
    )
    logger.info(
        "[boot] total before serving (config + Server() + engine warm-up): %.3fs",
        time.perf_counter() - _boot_t0,
    )
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
