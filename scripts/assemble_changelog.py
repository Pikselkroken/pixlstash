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

    Raises:
        ValueError: If a fragment does not start with a markdown list item. The
            file is pasted verbatim under a version heading, so anything else
            would land in the release notes as it stands.
    """
    entries = []
    for path in paths:
        body = path.read_text(encoding="utf-8").strip()
        if not body.startswith("- "):
            raise ValueError(
                f"{path.name} does not start with a markdown "
                "list item ('- '); a fragment is the changelog lines themselves, "
                "with no heading and no version."
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
    if not paths:
        print(f"No fragments in {FRAGMENT_DIR.relative_to(REPO_ROOT)}; nothing to do.")
        return 1

    entries = read_entries(paths)
    heading = f"# [{args.version}]"
    if args.security:
        heading += f" [Security: {args.security}]"

    existing = CHANGELOG.read_text(encoding="utf-8")
    CHANGELOG.write_text(
        f"{heading}\n\n" + "\n".join(entries) + "\n\n" + existing, encoding="utf-8"
    )

    if not args.keep:
        for path in paths:
            path.unlink()

    print(f"{heading}\n")
    for path in paths:
        print(f"  folded in {path.relative_to(REPO_ROOT)}")
    print(
        "\nRead the new section in CHANGELOG.md before committing: the order is "
        "by filename, not by what matters."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
