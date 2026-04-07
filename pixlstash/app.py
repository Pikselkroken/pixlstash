import os
import argparse
import logging
import sys
import json
import getpass

from platformdirs import user_config_dir
from passlib.hash import bcrypt


from pixlstash.pixl_logging import setup_logging, get_logger
from pixlstash.server import Server
from pixlstash.startup_checks import StartupCheckError

logger = get_logger(__name__)

APP_NAME = "pixlstash"
SERVER_CONFIG_PATH = os.path.join(user_config_dir(APP_NAME), "server-config.json")


def _resolve_log_level(value):
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        pass

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


def _normalize_watch_folder_entries(raw_entries) -> list[dict]:
    entries = []
    for entry in raw_entries or []:
        if isinstance(entry, dict):
            folder = entry.get("folder")
            if not folder:
                continue
            normalized = {
                "folder": str(folder),
                "delete_after_import": bool(entry.get("delete_after_import", False)),
            }
            if "last_checked" in entry:
                normalized["last_checked"] = entry.get("last_checked")
            entries.append(normalized)
            continue
        if isinstance(entry, str) and entry.strip():
            entries.append(
                {
                    "folder": entry.strip(),
                    "delete_after_import": False,
                }
            )
    return entries


def _print_watch_folders(entries: list[dict]) -> None:
    if not entries:
        print("Current watch folders: (none)")
        return
    print("Current watch folders:")
    for index, entry in enumerate(entries, start=1):
        folder = str(entry.get("folder") or "")
        delete_after_import = bool(entry.get("delete_after_import", False))
        delete_label = "yes" if delete_after_import else "no"
        print(f"  {index}. {folder}  (delete_after_import: {delete_label})")


def _edit_watch_folders(entries: list[dict]) -> list[dict]:
    if not entries:
        print("No existing watch folders to edit.")
        return entries

    updated_entries = [dict(entry) for entry in entries]
    print(
        "Edit mode: enter folder number to change delete_after_import. Enter to finish."
    )
    while True:
        _print_watch_folders(updated_entries)
        selection = input("Folder number to edit: ").strip()
        if not selection:
            break
        try:
            index = int(selection)
        except Exception:
            print("Please enter a valid number.")
            continue
        if index < 1 or index > len(updated_entries):
            print("Selection out of range.")
            continue

        entry = updated_entries[index - 1]
        folder = str(entry.get("folder") or "")
        default_delete = bool(entry.get("delete_after_import", False))
        delete_hint = "Y/n" if default_delete else "y/N"
        delete_input = input(f"Delete after import for '{folder}'? [{delete_hint}]: ")
        entry["delete_after_import"] = _parse_yes_no(delete_input, default_delete)
        updated_entries[index - 1] = entry

    return updated_entries


def _prompt_watch_folders(raw_existing_entries) -> list[dict]:
    entries = _normalize_watch_folder_entries(raw_existing_entries)
    _print_watch_folders(entries)

    action = (
        input("Watch folders: Enter keep, A add/update, E edit existing, N clear: ")
        .strip()
        .lower()
    )
    if not action:
        return entries
    if action in {"n", "none", "clear", "-"}:
        return []
    if action == "e":
        return _edit_watch_folders(entries)
    if action != "a":
        return entries

    existing_by_folder = {}
    for entry in entries:
        folder = str(entry.get("folder") or "")
        if folder:
            existing_by_folder[folder] = dict(entry)

    print("Add folders one at a time. Leave blank to finish.")
    while True:
        folder_input = input("Folder path: ").strip()
        if not folder_input:
            break

        folder = os.path.abspath(os.path.expanduser(folder_input))
        existing_entry = existing_by_folder.get(folder)
        default_delete = bool(
            existing_entry.get("delete_after_import", False)
            if existing_entry
            else False
        )
        delete_hint = "Y/n" if default_delete else "y/N"
        delete_input = input(f"Delete after import for '{folder}'? [{delete_hint}]: ")
        delete_after_import = _parse_yes_no(delete_input, default_delete)

        updated_entry = {
            "folder": folder,
            "delete_after_import": delete_after_import,
        }
        if existing_entry and "last_checked" in existing_entry:
            updated_entry["last_checked"] = existing_entry.get("last_checked")
        existing_by_folder[folder] = updated_entry

    updated_entries = list(existing_by_folder.values())
    _print_watch_folders(updated_entries)
    return updated_entries


def _should_prompt_bootstrap(server_config_path: str, force: bool) -> bool:
    if force:
        return True
    if not os.path.exists(server_config_path):
        return True
    try:
        with open(server_config_path, "r") as handle:
            data = json.load(handle)
        return not isinstance(data, dict)
    except Exception:
        return True


def _bootstrap_server_config(server_config_path: str, force: bool = False) -> bool:
    if not _should_prompt_bootstrap(server_config_path, force):
        return False
    if not sys.stdin.isatty():
        return False

    config = Server._init_server_config(server_config_path)

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
            pass
        print("Please enter a valid port between 1 and 65535.")

    ssl_default = bool(config.get("require_ssl", False))
    ssl_hint = "Y/n" if ssl_default else "y/N"
    ssl_input = input(f"Use HTTPS? [{ssl_hint}]: ").strip()
    require_ssl = _parse_yes_no(ssl_input, ssl_default)

    existing_watch_entries = list(config.get("watch_folders") or [])
    watch_folders = _prompt_watch_folders(existing_watch_entries)

    config["image_root"] = image_root
    config["port"] = port
    config["require_ssl"] = require_ssl
    config["cookie_secure"] = require_ssl
    config["watch_folders"] = watch_folders

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


def main():
    parser = argparse.ArgumentParser(description=f"Run the {APP_NAME} server.")
    parser.add_argument(
        "--server-config",
        type=str,
        default=SERVER_CONFIG_PATH,
        help="Path to server config file.",
    )
    parser.add_argument(
        "--remove-password",
        action="store_true",
        help="Cause the server to recreate the password on next login.",
    )
    parser.add_argument(
        "--retag-and-embed",
        action="store_true",
        help="Re-tag all images and refresh text embeddings in the database.",
    )
    parser.add_argument(
        "--clear-embeddings",
        action="store_true",
        help="Clear all text embeddings for all images (does not touch tags).",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "Run interactive first-run setup for storage path, port, HTTPS, and watch folders."
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
    args = parser.parse_args()

    ran_bootstrap = _bootstrap_server_config(args.server_config, force=args.bootstrap)
    Server.DEFAULT_CLEANUP_MISSING_PICTURES = bool(args.cleanup_missing_pictures)

    server_config = Server._init_server_config(args.server_config)

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
        server = Server(server_config_path=args.server_config)
    except StartupCheckError as exc:
        print("Startup checks failed. Please resolve the following issues:")
        for failure in exc.failures:
            print(f"- {failure}")
        return 1

    if ran_bootstrap:
        _prompt_bootstrap_credentials(server)

    if args.remove_password:
        server.remove_password_hash()
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
