import argparse
import json
import os
import re
import shutil
import tempfile
import tomllib

import pixlstash
from pixlstash.server import Server, render_scalar_html

# Images referenced by the API description (logo + token screenshots), bundled
# with the package and served at /scalar-assets on a live server. The published
# page uses page-relative URLs, so a copy must sit next to each version's
# index.html.
_SCALAR_ASSETS_SRC = os.path.join(
    os.path.dirname(os.path.abspath(pixlstash.__file__)), "data", "scalar-assets"
)


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


def _write_scalar_html(target_dir: str) -> None:
    # Relative spec URL: the page sits next to its own openapi.json in the
    # versioned directory.
    html = render_scalar_html("openapi.json")
    with open(os.path.join(target_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    if os.path.isdir(_SCALAR_ASSETS_SRC):
        shutil.copytree(
            _SCALAR_ASSETS_SRC,
            os.path.join(target_dir, "scalar-assets"),
            dirs_exist_ok=True,
        )


def _heal_legacy_docs(output_dir: str) -> None:
    """Bring previously published docs in line with the current Scalar setup.

    Docs published before the Scalar migration left two kinds of stale artifact
    behind, and the release publisher only ever *adds* files, so they would
    linger forever otherwise:

    * each older ``v{major}.{minor}/index.html`` still renders ReDoc — rewrite
      it to the Scalar template (the page is version-agnostic, it just loads the
      sibling ``openapi.json``);
    * a top-level ``openapi.json`` is no longer emitted (the root only holds the
      redirect ``index.html``), so drop it if an old run left one behind.
    """
    orphan_spec = os.path.join(output_dir, "openapi.json")
    if os.path.isfile(orphan_spec):
        os.remove(orphan_spec)

    for name in os.listdir(output_dir):
        version_dir = os.path.join(output_dir, name)
        if re.fullmatch(r"v\d+\.\d+", name) and os.path.isdir(version_dir):
            _write_scalar_html(version_dir)


def _write_latest_redirect(output_dir: str) -> None:
    """Point ``output_dir/index.html`` at the highest available version dir.

    The public ``/api`` link loads this file. Without it the entry page stays
    pinned to whatever spec was first written there. We scan the sibling version
    directories and redirect to the highest one (rather than assuming the
    just-generated version is newest), so backfilling an older release does not
    downgrade the canonical link.
    """
    versions = []
    for name in os.listdir(output_dir):
        m = re.fullmatch(r"v(\d+)\.(\d+)", name)
        if m and os.path.isdir(os.path.join(output_dir, name)):
            versions.append((int(m.group(1)), int(m.group(2)), name))
    if not versions:
        return
    latest = max(versions)[2]
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>PixlStash API Reference</title>
    <meta http-equiv="refresh" content="0; url=./{latest}/" />
    <link rel="canonical" href="./{latest}/" />
  </head>
  <body>
    <p>Redirecting to the latest API reference
       (<a href="./{latest}/">{latest}</a>)…</p>
  </body>
</html>
"""
    with open(os.path.join(output_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


def _get_api_version() -> str:
    """Return 'v{major}.{minor}' derived from pyproject.toml in the project root."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pyproject_path = os.path.join(project_root, "pyproject.toml")
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    version_str = data["project"]["version"]
    parts = version_str.split(".")
    major = parts[0]
    # Strip pre-release suffixes from minor (e.g. "2b1" → "2").
    minor_raw = parts[1] if len(parts) > 1 else "0"
    minor = re.match(r"^\d+", minor_raw).group(0)
    return f"v{major}.{minor}"


def generate_docs(output_dir: str) -> None:
    output_dir = os.path.abspath(output_dir)
    api_version = _get_api_version()
    versioned_dir = os.path.join(output_dir, api_version)
    os.makedirs(versioned_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="pixlstash-openapi-") as tmp_dir:
        config_path = os.path.join(tmp_dir, "server-config.json")
        image_root = os.path.join(tmp_dir, "images")
        _build_server_config(config_path, image_root)

        # Ensure deterministic and lightweight startup for CI docs generation.
        Server.DEFAULT_FORCE_CPU = True

        with Server(config_path) as server:
            schema = server.api.openapi()

    openapi_path = os.path.join(versioned_dir, "openapi.json")
    with open(openapi_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)

    _write_scalar_html(versioned_dir)
    _heal_legacy_docs(output_dir)
    _write_latest_redirect(output_dir)
    print(f"Generated OpenAPI docs for {api_version} in {versioned_dir}")


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
