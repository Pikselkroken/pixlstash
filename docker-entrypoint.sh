#!/bin/bash
set -e

# Platformdirs default config path (mirrors a regular Linux install):
# ~/.config/pixlvault/server-config.json
# Override by setting PIXLVAULT_CONFIG in the environment.
CONFIG_PATH="${PIXLVAULT_CONFIG:-${HOME}/.config/pixlvault/server-config.json}"
HOST="${PIXLVAULT_HOST:-0.0.0.0}"
PORT="${PIXLVAULT_PORT:-9537}"

mkdir -p "$(dirname "$CONFIG_PATH")"

# Write a default config on first run with Docker-appropriate settings
# (host 0.0.0.0 so the server is reachable from outside the container).
# If the config already exists it is left untouched so user edits survive restarts.
if [ ! -f "$CONFIG_PATH" ]; then
    echo "Creating default server config at $CONFIG_PATH"
    # Default image_root mirrors what Server._init_server_config uses:
    # os.path.join(config_dir, "images")
    DEFAULT_IMAGE_ROOT="$(dirname "$CONFIG_PATH")/images"
    IMAGE_ROOT="${PIXLVAULT_IMAGE_ROOT:-$DEFAULT_IMAGE_ROOT}"
    mkdir -p "$IMAGE_ROOT"
    cat > "$CONFIG_PATH" <<EOF
{
  "host": "$HOST",
  "port": $PORT,
  "log_level": "info",
  "log_file": null,
  "require_ssl": false,
  "cookie_samesite": "Lax",
  "cookie_secure": false,
  "image_root": "$IMAGE_ROOT",
  "default_device": "auto",
  "min_free_disk_gb": 1.0,
  "min_free_vram_mb": 1024.0,
  "cors_origins": [],
  "watch_folders": []
}
EOF
fi

exec python -m pixlvault.app --server-config "$CONFIG_PATH" "$@"
