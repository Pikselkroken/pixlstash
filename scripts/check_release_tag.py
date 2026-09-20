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

Both operands are shape-checked, not just the tag. A raw string comparison
forced the pyproject version through the tag pattern transitively - it could
only be equal to a tag that had already matched - and normalising the
comparison drops that. The version string names the installer file and the
Inno Setup ``PIXLSTASH_VERSION``, so an unvalidated one ships in artifact
names while the wheel normalises it away.

Every shape the pattern admits parses, so there is no ``InvalidVersion``
branch to carry: a pattern loosened past PEP 440 would raise, and an
unhandled raise exits non-zero, which is the same fail-closed answer.

Requires ``packaging``. Every caller installs it explicitly: the one workflow
that relied on it arriving with ``build`` would have started rejecting valid
tags the moment the validation step moved ahead of the wheel build.
"""

import re
import sys

from packaging.version import Version

# ``fullmatch`` and ``\A``/``\Z`` are each sufficient alone and kept together
# deliberately: with a bare ``.match()`` the anchors are the only thing
# stopping ``v1.12.0-anything`` from passing as a prefix, and Python's ``$``
# would additionally accept a trailing newline that the shell checks this
# replaced rejected.
TAG_PATTERN = re.compile(
    r"\Av[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+|[.-]dev[.-]?[0-9]+)?\Z"
)


def _fail(message: str) -> int:
    """Print ``message`` as a GitHub annotation and return the failure code."""
    print(f"::error::{message}", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    """Return 0 if ``argv`` is a supported tag naming the expected version."""
    if len(argv) != 2:
        print("usage: check_release_tag.py <tag> <expected-version>", file=sys.stderr)
        return 2
    tag, expected = argv
    if not TAG_PATTERN.fullmatch(tag):
        return _fail(f"Release tag has an unsupported format: {tag}")
    if not TAG_PATTERN.fullmatch(f"v{expected}"):
        return _fail(f"pyproject version has an unsupported format: {expected}")
    if Version(tag[1:]) != Version(expected):
        return _fail(f"Tag {tag} does not match pyproject version {expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
