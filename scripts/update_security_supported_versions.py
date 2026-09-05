"""Rewrite the supported-versions table in ``SECURITY.md`` for a new release.

PixlStash supports exactly one minor series: the latest. The table therefore
always has three rows - the supported minor, the one it just demoted, and a
catch-all ``< previous`` row - and it was maintained by hand at release time.
``.github/workflows/release-version.yml`` runs this script against the ``main``
checkout so the table lands in the same bot PR as the other website updates.

The demoted minor is read back out of the existing table rather than computed
by subtracting one, because ``2.0.0`` follows ``1.9.x`` and arithmetic would
produce ``2.-1``. That makes the current table a required input: if it cannot
be parsed the script fails instead of writing a fresh table over a file whose
shape it did not recognise.

Usage
-----
::

    python scripts/update_security_supported_versions.py SECURITY.md v1.10.0
"""

import argparse
import re
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version

SUPPORTED = ":white_check_mark:"
UNSUPPORTED = ":x:"

# Column widths are those of the widest cell in each column. The right column
# is always the ":white_check_mark:" marker; the left one is the "Version"
# header until a version string outgrows it (``< 1.10.x`` is 8 characters).
_MIN_VERSION_WIDTH = len("Version")
_SUPPORTED_WIDTH = len(SUPPORTED)

# The existing table, which must be present and well-formed for us to know
# which minor is being demoted.
_TABLE_RE = re.compile(
    r"^\| Version +\| Supported +\|\n"
    r"^\| -+ \| -+ \|\n"
    # The trailing lookahead makes a fourth row a parse *failure* rather than
    # a silent partial match that would leave an orphaned row behind.
    r"(?:^\|[^\n]*\|\n){3}(?!\|)",
    re.MULTILINE,
)
_SUPPORTED_ROW_RE = re.compile(
    rf"^\| (\d+)\.(\d+)\.x +\| {re.escape(SUPPORTED)} +\|$", re.MULTILINE
)


def render_table(major: int, minor: int, prev_major: int, prev_minor: int) -> str:
    """Render the three-row table for *major.minor*, demoting the previous."""
    prev = f"{prev_major}.{prev_minor}.x"
    versions = ["Version", f"{major}.{minor}.x", prev, f"< {prev}"]
    width = max(_MIN_VERSION_WIDTH, *(len(v) for v in versions))

    def row(version: str, mark: str) -> str:
        return f"| {version:<{width}} | {mark:<{_SUPPORTED_WIDTH}} |"

    return (
        "\n".join(
            [
                row("Version", "Supported"),
                row("-" * width, "-" * _SUPPORTED_WIDTH),
                row(f"{major}.{minor}.x", SUPPORTED),
                row(prev, UNSUPPORTED),
                row(f"< {prev}", UNSUPPORTED),
            ]
        )
        + "\n"
    )


def update_supported_versions(text: str, tag: str) -> tuple[str, str]:
    """Return ``(new_text, message)`` for *text* after release *tag*.

    ``new_text`` is *text* unchanged when the release must not move the table
    (a pre-release, or anything not strictly newer than the current minor).
    Raises :class:`ValueError` if the existing table cannot be parsed.
    """
    try:
        version = Version(tag.lstrip("v"))
    except InvalidVersion:
        return text, f"Release tag '{tag}' is not a valid version; skipping."

    if version.is_prerelease:
        return text, f"Release {version} is a pre-release; skipping."

    table_match = _TABLE_RE.search(text)
    if table_match is None:
        raise ValueError(
            "could not find the supported-versions table; expected a "
            "'| Version | Supported |' header, a dashed separator and exactly "
            "three rows"
        )
    supported_match = _SUPPORTED_ROW_RE.search(table_match.group(0))
    if supported_match is None:
        raise ValueError(
            "the supported-versions table has no supported row; expected one "
            f"row of the form '| <major>.<minor>.x | {SUPPORTED} |'"
        )

    current = (int(supported_match.group(1)), int(supported_match.group(2)))
    new = (version.major, version.minor)
    if new <= current:
        return text, (
            f"Release {version} is not a newer minor than the currently "
            f"supported {current[0]}.{current[1]}.x; skipping."
        )

    table = render_table(new[0], new[1], current[0], current[1])
    return text[: table_match.start()] + table + text[table_match.end() :], (
        f"Supported versions now {new[0]}.{new[1]}.x "
        f"(demoted {current[0]}.{current[1]}.x)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Path to the SECURITY.md to update")
    parser.add_argument("tag", help="Release tag, e.g. v1.10.0")
    args = parser.parse_args(argv)

    text = args.path.read_text(encoding="utf-8")
    try:
        new_text, message = update_supported_versions(text, args.tag)
    except ValueError as exc:
        print(f"{args.path}: {exc}", file=sys.stderr)
        return 1

    if new_text != text:
        args.path.write_text(new_text, encoding="utf-8")
    print(f"{args.path}: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
