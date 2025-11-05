import argparse
from pixelurgy_vault.server import Server
from platformdirs import user_config_dir
import os

APP_NAME = "pixelurgy-vault"
CONFIG_PATH = os.path.join(user_config_dir(APP_NAME), "config.json")
SERVER_CONFIG_PATH = os.path.join(user_config_dir(APP_NAME), "server-config.json")


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

    server = Server(config_path=args.config, server_config_path=args.server_config)
    server.run()


if __name__ == "__main__":
    main()
