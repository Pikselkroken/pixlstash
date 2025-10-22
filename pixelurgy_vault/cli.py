import argparse
import requests
import os
import json
from platformdirs import user_config_dir

APP_NAME = "pixelurgy-vault"
CONFIG_PATH = os.path.join(user_config_dir(APP_NAME), "config.json")


def get_default_port():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
        return config.get("port", 9537)
    return 9537


def main():
    default_port = get_default_port()
    parser = argparse.ArgumentParser(description="Pixelurgy Vault CLI Tool")
    parser.add_argument("--api-url", default=None, help="Base URL of the Pixelurgy Vault API.")
    parser.add_argument("--import-from-path", type=str, help="Path to image file or directory to import.")
    parser.add_argument("--character", help="Character name for the imported images.")
    parser.add_argument("--description", default="", help="Description for the character (if created).")


    args = parser.parse_args()
    print(args)

    api_url = args.api_url
    if not api_url:
        api_url = f"http://localhost:{default_port}"

    # Only run import logic if import_from_path is provided
    if args.import_from_path:
        if not os.path.exists(args.import_from_path):
            import sys
            print(f"Error: Path '{args.import_from_path}' does not exist.", file=sys.stderr)
            sys.exit(1)

        # Check if character exists
        r = requests.get(f"{api_url}/characters", params={"name": args.character})
        r.raise_for_status()
        chars = r.json()
        if chars:
            char_id = chars[0]["id"]
            print(f"Character '{args.character}' exists (id={char_id})")
        else:
            # Create character
            print(f"Creating character '{args.character}'...")
            resp = requests.post(f"{api_url}/characters", json={"id": args.character, "name": args.character, "description": args.description})
            resp.raise_for_status()
            char_id = resp.json()["character"]["id"]
            print(f"Character created (id={char_id})")

        # Import images by sending the path to the server as form data
        form_data = {
            "character_id": char_id,
            "file_path": args.import_from_path,
            "description": args.description,
            "tags": "[]"
        }
        r = requests.post(f"{api_url}/pictures", data=form_data)
        print(f"Import request sent for path '{args.import_from_path}': {r.status_code} {r.text}")


if __name__ == "__main__":
    main()
