#!/bin/bash
# Collect the source and licence texts of every library BtbN's lgpl build links.
#
#   collect_sources.sh <FFmpeg-Builds checkout at the build's commit> <series, e.g. 9.0> <out dir>
#
# Writes <out>/ffmpeg-source-dep-<library>-<commit>.tar.gz (one per library, at the
# commit BtbN built), <out>/THIRD_PARTY_LICENSES.txt and <out>/DEPS.txt.
# "Linked" is taken as "enabled": every scripts.d stage whose ffbuild_enabled
# succeeds for the lgpl variant of each mirrored target. That is a superset of
# what ends up in the binary (build tools are included), never a subset.
set -euo pipefail
btbn=$(cd "$1" && pwd); series=$2; mkdir -p "$3"; out=$(cd "$3" && pwd)
work=$(mktemp -d)
: > "$work/pairs"

cd "$btbn"
for target in linux64 linuxarm64 win64; do
  for script in scripts.d/*.sh scripts.d/*/*.sh; do
    # Same environment generate.sh gives a stage. A stage that cannot be
    # evaluated counts as enabled: extra source is harmless, missing is not.
    (
      source util/vars.sh "$target" lgpl "$series" >/dev/null
      source "$script"
      if ffbuild_enabled; then
        for n in "" 2 3; do
          repo=$(eval echo "\${SCRIPT_REPO$n:-}"); commit=$(eval echo "\${SCRIPT_COMMIT$n:-}")
          [ -n "$repo" ] || continue
          if [ -n "$commit" ]; then kind=git; rev=$commit
          elif [ -n "${SCRIPT_REV:-}" ]; then kind=svn; rev=$SCRIPT_REV # lame
          else echo "::error::$script: $repo has no SCRIPT_COMMIT$n"; exit 3; fi
          echo "$kind $repo $rev $(basename "$script" .sh)"
        done
      fi
    ) >> "$work/pairs" || { [ $? -ne 3 ] || exit 1; echo "::warning::$script could not be evaluated for $target; skipped"; }
  done
done
sort -u "$work/pairs" > "$out/DEPS.txt"

mkdir "$work/deps"
{
  echo "Licence and copyright texts of the libraries linked into the bundled ffmpeg."
  echo "Each section is the licence files at the root of that library's source, at the commit built."
} > "$out/THIRD_PARTY_LICENSES.txt"
while read -r kind repo commit stage; do
  dir="$work/deps/$stage-${commit:0:12}"
  if [ "$kind" = svn ]; then
    svn export -q -r "$commit" "$repo" "$dir"
  else
    git clone -q --filter=blob:none "$repo" "$dir"
    git -C "$dir" checkout -q "$commit"
    git -C "$dir" submodule update -q --init --recursive
  fi
  {
    printf '\n\n======== %s  (%s @ %s)\n' "$stage" "$repo" "$commit"
    find "$dir" -maxdepth 1 -type f \( -iname 'LICENSE*' -o -iname 'LICENCE*' -o -iname 'COPYING*' -o -iname 'COPYRIGHT*' -o -iname 'NOTICE*' -o -iname 'AUTHORS*' \) | sort |
      while read -r f; do printf '\n---- %s\n' "$(basename "$f")"; cat "$f"; done
  } >> "$out/THIRD_PARTY_LICENSES.txt"
  rm -rf "$dir/.git"
done < "$out/DEPS.txt"
# One tarball per library: together they are ~1.5 GB, over what one release asset may hold.
for d in "$work/deps"/*; do tar -czf "$out/ffmpeg-source-dep-$(basename "$d").tar.gz" -C "$work/deps" "$(basename "$d")"; done
rm -rf "$work"
