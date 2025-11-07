import os
import argparse
import logging

from platformdirs import user_config_dir

from pixelurgy_vault.logging import setup_logging
from pixelurgy_vault.server import Server

APP_NAME = "pixelurgy-vault"
CONFIG_PATH = os.path.join(user_config_dir(APP_NAME), "config.json")
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
        "--config",
        type=str,
        default=CONFIG_PATH,
        help="Path to remote configurable settings file.",
    )
    parser.add_argument(
        "--server-config",
        type=str,
        default=SERVER_CONFIG_PATH,
        help="Path to server config file.",
    )
    args = parser.parse_args()

    server_config = Server.init_server_config(args.server_config)
    log_file = server_config.get("log_file")
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
    log_level = _resolve_log_level(server_config.get("log_level"))
    setup_logging(log_file=log_file, log_level=log_level)

    server = Server(config_path=args.config, server_config_path=args.server_config)
    server.run()


if __name__ == "__main__":
    main()
