#!/usr/bin/env python3
"""Fold ``changelog.d/`` fragments into ``CHANGELOG.md`` as one version section.

Run when a release is cut, from the root of the checkout being released::

    python scripts/assemble_changelog.py 1.11.1
    python scripts/assemble_changelog.py 1.11.1 --security High

Every branch writes its own file under ``changelog.d/`` instead of appending to
the top of ``CHANGELOG.md``, so two open PRs never collide there. This is the
one thing that writes ``CHANGELOG.md``; see ``changelog.d/README.md`` for when a
change deserves a fragment at all.

The fragments are deleted once folded in. Read the assembled section before
committing it: ordering is by filename, which is deterministic but not
editorial.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRAGMENT_DIR = REPO_ROOT / "changelog.d"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
SECURITY_LEVELS = ("Critical", "High", "Moderate", "Low")


def fragments() -> list[Path]:
    """Every fragment file, in the order their lines will appear.

    ``README.md`` documents the directory rather than describing a change, so it
    is never a fragment.
    """
    return sorted(p for p in FRAGMENT_DIR.glob("*.md") if p.name != "README.md")


def read_entries(paths: list[Path]) -> list[str]:
    """The list items from *paths*, one string per fragment file.

    Every line is checked, not just the first: the file is pasted verbatim under
    a version heading, so a stray ``# heading`` on line three lands in the
    release notes as a version of its own and pushes everything below it into the
    wrong release.

    Raises:
        ValueError: If any line is neither a markdown list item, an indented
            continuation of one, nor blank.
    """
    entries = []
    for path in paths:
        body = path.read_text(encoding="utf-8").strip()
        for number, line in enumerate(body.splitlines(), start=1):
            if not line.strip() or line.startswith(("- ", "  ")):
                continue
            raise ValueError(
                f"{path.name} line {number} is neither a list item nor an "
                f"indented continuation of one: {line!r}. A fragment is the "
                "changelog lines themselves, with no heading and no version."
            )
        if not body.startswith("- "):
            raise ValueError(
                f"{path.name} does not start with a markdown list item ('- '); "
                "a fragment is the changelog lines themselves, with no heading "
                "and no version."
            )
        entries.append(body)
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="Version being released, e.g. 1.11.1")
    parser.add_argument(
        "--security",
        choices=SECURITY_LEVELS,
        help=(
            "Tag the heading '[Security: LEVEL]'. The release workflow reads "
            "this off the top heading to publish latest-version.json."
        ),
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Leave the fragments in place instead of deleting them.",
    )
    args = parser.parse_args(argv)

    paths = fragments()
    entries = read_entries(paths)
    heading = f"# [{args.version}]"
    if args.security:
        heading += f" [Security: {args.security}]"

    # The heading is written even with nothing under it. `release-version.yml`
    # reads `[Security: LEVEL]` off whatever heading is at the top of the
    # released tag's CHANGELOG.md, so a release that skipped this step would
    # publish the PREVIOUS release's security level as its own. A release with
    # no user-visible change is a real thing - an rc bump, a cycle of internal
    # fixes - and it still needs its own heading.
    existing = CHANGELOG.read_text(encoding="utf-8")
    section = f"{heading}\n\n" + ("\n".join(entries) + "\n\n" if entries else "")
    CHANGELOG.write_text(section + existing, encoding="utf-8")

    if not args.keep:
        for path in paths:
            path.unlink()

    print(f"{heading}\n")
    for path in paths:
        print(f"  folded in {path.relative_to(REPO_ROOT)}")
    if not paths:
        print(
            f"  no fragments in {FRAGMENT_DIR.relative_to(REPO_ROOT)} - the "
            "section is empty, which says no user-visible change shipped in it.\n"
            "  If that is wrong, the fragments were never written; add them and "
            "run this again."
        )
        return 0
    print(
        "\nRead the new section in CHANGELOG.md before committing: the order is "
        "by filename, not by what matters."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
