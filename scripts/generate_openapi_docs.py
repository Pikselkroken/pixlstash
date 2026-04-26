import argparse
import json
import os
import tempfile

from pixlstash.server import Server


def _build_server_config(config_path: str, image_root: str) -> None:
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    os.makedirs(image_root, exist_ok=True)

    config = {
        "host": "127.0.0.1",
        "port": 0,
        "log_level": "warning",
        "log_file": os.path.join(os.path.dirname(config_path), "server.log"),
        "require_ssl": False,
        "cookie_samesite": "Lax",
        "cookie_secure": False,
        "image_root": image_root,
        "default_device": "cpu",
        "min_free_disk_gb": 0.0,
        "min_free_vram_mb": 0.0,
        "cors_origins": [],
        "max_attachment_size_mb": 50,
        "generate_thumbnails_on_startup": False,
    }

    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)


def _write_redoc_html(target_dir: str) -> None:
    html = """<!doctype html>
<html>
  <head>
    <title>PixlStash API Reference</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
  </head>
  <body>
    <redoc spec-url="openapi.json"></redoc>
    <script src="https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js"></script>
  </body>
</html>
"""

    with open(os.path.join(target_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


def generate_docs(output_dir: str) -> None:
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="pixlstash-openapi-") as tmp_dir:
        config_path = os.path.join(tmp_dir, "server-config.json")
        image_root = os.path.join(tmp_dir, "images")
        _build_server_config(config_path, image_root)

        # Ensure deterministic and lightweight startup for CI docs generation.
        Server.DEFAULT_FORCE_CPU = True

        with Server(config_path) as server:
            schema = server.api.openapi()

    openapi_path = os.path.join(output_dir, "openapi.json")
    with open(openapi_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)

    _write_redoc_html(output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate PixlStash OpenAPI docs for static hosting."
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join("website", "api"),
        help="Directory where openapi.json and docs HTML will be written.",
    )
    args = parser.parse_args()
    generate_docs(args.output_dir)


if __name__ == "__main__":
    main()
