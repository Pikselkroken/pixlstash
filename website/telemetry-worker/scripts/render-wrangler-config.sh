#!/usr/bin/env sh

set -eu

worker_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
template="$worker_dir/wrangler.toml.template"
output_dir="$worker_dir/build"
output="$output_dir/wrangler.toml"

if [ -z "${D1_DATABASE_ID:-}" ]; then
  echo "D1_DATABASE_ID must be set" >&2
  exit 1
fi

if ! printf '%s\n' "$D1_DATABASE_ID" \
  | grep -Eq '^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$'; then
  echo "D1_DATABASE_ID must be a UUID" >&2
  exit 1
fi

if ! command -v envsubst >/dev/null 2>&1; then
  echo "envsubst is required (install the gettext package)" >&2
  exit 1
fi

mkdir -p "$output_dir"
envsubst '$D1_DATABASE_ID' < "$template" > "$output"

if grep -Fq '${D1_DATABASE_ID}' "$output"; then
  echo "D1_DATABASE_ID was not substituted" >&2
  exit 1
fi

echo "Generated $output"
