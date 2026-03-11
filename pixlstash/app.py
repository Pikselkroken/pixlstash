import os
import argparse
import logging
import sys

from platformdirs import user_config_dir


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
    args = parser.parse_args()

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
        return

    server.vault.ensure_ready()
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
