"""Validate a release tag against the version in ``pyproject.toml``.

Usage::

    python scripts/check_release_tag.py <tag> <expected-version>

Exits 0 when the tag is a supported release tag naming that exact version.
The comparison is PEP 440 normalised, so ``v1.12.0-dev.2``, ``v1.12.0.dev.2``
and ``v1.12.0.dev2`` all match a pyproject version of ``1.12.0.dev.2``. The
release workflows used to compare the two as raw strings, which accepted the
``-dev`` spelling in the format check and then rejected it in the equality
check: v1.12.0-dev.1 and v1.12.0-dev.2 both failed there with the tag and the
version naming the same release.
"""

import re
import sys

from packaging.version import InvalidVersion, Version

# ``\A``/``\Z`` rather than ``^``/``$``: Python's ``$`` also matches before a
# trailing newline, which would accept a tag the shell checks this replaced
# rejected.
TAG_PATTERN = re.compile(
    r"\Av[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+|[.-]dev[.-]?[0-9]+)?\Z"
)


def main(argv: list[str]) -> int:
    """Return 0 if ``argv`` is a supported tag naming the expected version."""
    if len(argv) != 2:
        print("usage: check_release_tag.py <tag> <expected-version>", file=sys.stderr)
        return 2
    tag, expected = argv
    if not TAG_PATTERN.match(tag):
        print(f"Release tag has an unsupported format: {tag}", file=sys.stderr)
        return 1
    try:
        if Version(tag[1:]) != Version(expected):
            print(
                f"Tag {tag} does not match pyproject version {expected}",
                file=sys.stderr,
            )
            return 1
    except InvalidVersion as exc:
        print(
            f"Cannot compare tag {tag} with pyproject version {expected}: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
