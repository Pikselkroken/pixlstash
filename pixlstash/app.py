import os
import argparse
import logging
import sys
import json
import getpass
import shlex

from platformdirs import user_config_dir
from passlib.hash import bcrypt


from pixlstash.pixl_logging import setup_logging, get_logger
from pixlstash.server import Server
from pixlstash.startup_checks import StartupCheckError
from pixlstash.hub.db import HubPermissionError
from pixlstash.startup_permissions import (
    PERMISSION_REPAIR_ENV,
    find_startup_permission_issues,
    format_permission_problem,
    permission_repair_signal,
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
        f"chmod {issue.repaired_mode:03o} {shlex.quote(issue.path)}"
        for issue in issues
    ]


def _prepare_startup_permissions(
    server_config_path: str,
    server_config: dict,
) -> bool:
    """Offer or perform the bounded permission repair needed before startup.

    Electron has no usable stdin, so the first backend launch emits a structured
    line for the shell and exits. After the user accepts the native dialog, the
    shell retries once with ``PIXLSTASH_REPAIR_PERMISSIONS=1``. A terminal gets
    the same decision inline; services get actionable commands and a clean exit.
    """

    repair_requested = os.environ.get(PERMISSION_REPAIR_ENV) == "1"
    is_electron = os.environ.get("PIXLSTASH_INSTALL_TYPE", "").lower() == "electron"

    # Usually one pass is enough. A second pass can discover an active library
    # stored in the hub only after the hub directory itself has been repaired.
    for _ in range(3):
        issues = find_startup_permission_issues(
            server_config_path,
            str(server_config.get("image_root") or "") or None,
        )
        if not issues:
            return True

        message = format_permission_problem(issues)
        if repair_requested:
            print(message, file=sys.stderr)
            try:
                repair_permission_issues(issues)
            except OSError as exc:
                print(f"\nPixlStash could not fix the permissions: {exc}", file=sys.stderr)
                return False
            continue

        if is_electron:
            # Human text remains in the log; the single-line JSON record is the
            # stable protocol consumed by the desktop shell.
            print(message, file=sys.stderr)
            print(permission_repair_signal(issues), file=sys.stderr)
            return False

        print(message, file=sys.stderr)
        if getattr(sys.stdin, "isatty", lambda: False)():
            try:
                answer = input("\nFix permissions now? [Y/n] ").strip().lower()
            except EOFError:
                answer = "n"
            if answer not in {"", "y", "yes"}:
                print("Permissions were not changed.", file=sys.stderr)
                return False
            try:
                repair_permission_issues(issues)
            except OSError as exc:
                print(f"\nPixlStash could not fix the permissions: {exc}", file=sys.stderr)
                return False
            print("Permissions fixed. Starting PixlStash…", file=sys.stderr)
            continue

        print("\nFix them and start PixlStash again:", file=sys.stderr)
        for command in _permission_fix_commands(issues):
            print(f"  {command}", file=sys.stderr)
        return False

    print(
        "PixlStash still found unsafe permissions after attempting the repair.",
        file=sys.stderr,
    )
    return False


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
    if not sys.stdin.isatty():
        return

    user = server.auth.user or server.auth.ensure_user()
    has_existing_credentials = bool(user and user.username and user.password_hash)

    if has_existing_credentials:
        keep_input = input("Keep existing username/password? [Y/n]: ").strip()
        keep_existing = _parse_yes_no(keep_input, True)
        if keep_existing:
            return
    else:
        setup_input = input("Set username/password now before launch? [Y/n]: ").strip()
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
    characters — e.g. the box-drawing glyphs in the startup banner or the
    arrows in log messages — then raises ``UnicodeEncodeError`` and takes the
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
            "Run the interactive first-run setup — storage path, port, HTTPS, "
            "then the username and password — even if a config file already "
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
    if not _prepare_startup_permissions(args.server_config, server_config):
        return 1

    log_level = _resolve_log_level(server_config.get("log_level"))
    log_file = server_config.get("log_file")
    if log_file and log_level != logging.INFO:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        setup_logging(log_file=log_file, log_level=log_level)
    else:
        setup_logging(log_level=log_level)

    try:
        server = Server(server_config_path=args.server_config, path_map=path_map)
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

    server.vault.ensure_ready()
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
