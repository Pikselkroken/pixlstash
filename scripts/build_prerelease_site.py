"""Assemble pixlstash.dev/prerelease/, the release-candidate mirror of the site.

``pages.yml`` runs this on every deploy, in two steps with a checkout of the
chosen tag in between::

    python scripts/build_prerelease_site.py select --releases-json releases.json
    python scripts/build_prerelease_site.py build --site-dir _site \\
        --rc-website rc/website --tag v2.0.0rc1
    python scripts/build_prerelease_site.py build --site-dir _site   # no RC

``select`` prints the newest ``rc`` tag that is newer than the newest stable
release (or nothing), and writes it as ``tag=`` to ``$GITHUB_OUTPUT``. ``build``
copies that tag's ``website/`` to ``<site-dir>/prerelease/`` with its download
links and install commands pointed at the RC, and writes
``<site-dir>/prerelease.json``, which the stable site's header reads. Without a
tag it writes a redirect to the stable site and ``{"tag": null}``.

Every deploy replaces the whole Pages site, so both halves are rebuilt each
run. ``build`` raises on real faults (a rewrite that matched nothing, a missing
checkout); ``pages.yml`` then falls back to ``build`` without a tag so the
stable site still deploys, and fails the run after the deploy.

``released`` guards the automatic deploy. A push to ``main`` can carry site
copy for a version that is not out yet (``index.html`` saying 2.0 while
``website/latest-version.json``, which the download links are built from, still
names 1.13.2). It writes ``released=true`` only when ``pyproject.toml``'s
version is no newer than ``latest-version.json``, which the release bot raises
once a release is published; ``pages.yml`` skips a push deploy otherwise. A
manual run always deploys::

    python scripts/build_prerelease_site.py released \\
        --pyproject pyproject.toml --latest-version website/latest-version.json

Versions are parsed with a pattern rather than ``packaging`` so the Pages job,
which can deploy, installs nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path
from urllib.parse import quote

REPO_URL = "https://github.com/pikselkroken/pixlstash"
IMAGE = "ghcr.io/pikselkroken/pixlstash"

# Only these two shapes take part: vX.Y.Z (stable) and vX.Y.ZrcN. Dev, alpha
# and beta builds are not offered to users.
_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:rc(\d+))?$")


def _version_key(tag: str) -> tuple[int, ...] | None:
    """Return a sortable key for a stable or rc tag, ``None`` for anything else.

    A stable release sorts above every rc of the same version.
    """
    match = _TAG_RE.match(tag)
    if not match:
        return None
    major, minor, patch, rc = match.groups()
    return (int(major), int(minor), int(patch), int(rc) if rc else sys.maxsize)


def pick_release_candidate(releases: list[dict]) -> str | None:
    """Return the newest rc tag newer than the newest stable release, if any.

    Args:
        releases: ``gh release list --json tagName,isPrerelease,isDraft`` rows.
    """
    stable: tuple[int, ...] = ()
    candidates = []
    for release in releases:
        if release.get("isDraft"):
            continue
        tag = release["tagName"]
        key = _version_key(tag)
        if key is None:
            continue
        if key[3] == sys.maxsize:
            stable = max(stable, key)
        else:
            candidates.append((key, tag))
    newer = [(key, tag) for key, tag in candidates if key > stable]
    return max(newer)[1] if newer else None


def is_released(version: str, latest: str) -> bool:
    """Return whether ``version`` (from pyproject.toml) has been published.

    Args:
        version: The code's version, e.g. ``2.0.0rc5`` or ``2.0.0``.
        latest: The published stable version from ``latest-version.json``.

    A version this module cannot parse (a dev build, an alpha) counts as not
    released, so the site waits for a manual run rather than guessing.
    """
    key = _version_key(f"v{version}")
    published = _version_key(f"v{latest}")
    return key is not None and published is not None and key <= published


def _rewrites(tag: str, version: str) -> list[tuple[str, re.Pattern, str]]:
    """Return (name, pattern, replacement) for each required rewrite."""
    return [
        (
            "releases/latest",
            re.compile(re.escape(f"{REPO_URL}/releases/latest")),
            f"{REPO_URL}/releases/tag/{tag}",
        ),
        (
            ":latest-gpu",
            re.compile(re.escape(f"{IMAGE}:latest-gpu")),
            f"{IMAGE}:{version}-gpu",
        ),
        (
            ":latest",
            # The lookahead leaves :latest-gpu to the rule above.
            re.compile(re.escape(f"{IMAGE}:latest") + r"(?![\w-])"),
            f"{IMAGE}:{version}",
        ),
        # A pinned pre-release installs without --pre. Leaves pixlstash[extra],
        # pixlstash==x and pixlstash-foo alone.
        (
            "pip install pixlstash",
            re.compile(r"(pip install (?:--upgrade |-U )?pixlstash)(?![\[=\w-])"),
            rf"\g<1>=={version}",
        ),
    ]


def _chrome(version: str) -> str:
    """Return the banner and bug-report box injected after ``<body>``."""
    issue_url = (
        f"{REPO_URL}/issues/new?template=bug_report.md"
        f"&amp;title={quote(f'[{version}] ', safe='')}"
    )
    return f"""
<style>
  .rc-banner {{
    margin: 8px 16px 0;
    padding: 8px 14px;
    border: 1px solid var(--accent);
    border-radius: var(--radius-md);
    color: var(--muted);
    font-size: 0.88rem;
    text-align: center;
  }}
  .rc-banner strong {{ color: var(--accent); }}
  .rc-banner a, .rc-report a {{ color: var(--accent); text-decoration: underline; }}
  .rc-report {{
    position: fixed;
    right: 16px;
    bottom: 16px;
    z-index: 50;
    max-width: 240px;
    padding: 10px 14px;
    background: var(--toolbar);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    box-shadow: 0 2px 10px var(--shadow);
    font-size: 0.85rem;
  }}
  .rc-report a:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
  .rc-report .rc-report-short {{ display: none; }}
  @media (max-width: 760px) {{
    .rc-report {{ right: 12px; bottom: 12px; padding: 6px 12px; border-radius: 999px; }}
    .rc-report .rc-report-long {{ display: none; }}
    .rc-report .rc-report-short {{ display: inline; }}
  }}
</style>
<div class="rc-banner" role="note">
  You're on the release-candidate site for <strong>{version}</strong>. It may
  have rough edges. <a href="{{root}}index.html">Back to the stable site</a>
</div>
<aside class="rc-report" aria-label="Report a bug">
  <a href="{issue_url}" target="_blank" rel="noopener">
    <span class="rc-report-long">Found a bug in {version}? Report it here on GitHub</span>
    <span class="rc-report-short">Report a bug</span>
  </a>
</aside>
"""


def build(site_dir: Path, rc_website: Path, tag: str) -> None:
    """Copy *rc_website* to ``site_dir/prerelease`` and point it at *tag*.

    Raises:
        FileNotFoundError: *rc_website* is not a directory.
        RuntimeError: a required rewrite matched nothing anywhere in the copy.
    """
    if not rc_website.is_dir():
        raise FileNotFoundError(f"RC website checkout missing: {rc_website}")
    version = tag.removeprefix("v")
    out = site_dir / "prerelease"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(rc_website, out)
    # Its manifest names the stable release; install.html would use it to turn
    # every RC download button back into a stable download.
    shutil.rmtree(out / "latest-version", ignore_errors=True)

    rewrites = _rewrites(tag, version)
    hits = dict.fromkeys((name for name, _, _ in rewrites), 0)
    chrome = _chrome(version)
    for page in sorted(out.rglob("*.html")):
        text = page.read_text(encoding="utf-8")
        for name, pattern, replacement in rewrites:
            text, count = pattern.subn(replacement, text)
            hits[name] += count
        depth = len(page.relative_to(out).parts) - 1
        root = "../" * (depth + 1)
        text = re.sub(
            r"(<head(?:\s[^>]*)?>)",
            r'\1\n    <meta name="robots" content="noindex" />',
            text,
            count=1,
        )
        text = re.sub(
            r"(<body(?:\s[^>]*)?>)",
            lambda m: m.group(1) + chrome.replace("{root}", root),
            text,
            count=1,
        )
        page.write_text(text, encoding="utf-8")

    missing = [name for name, count in hits.items() if not count]
    if missing:
        raise RuntimeError(
            f"Prerelease rewrite(s) matched nothing in {rc_website}: {missing}. "
            "The website copy changed; update _rewrites() so the mirror does not "
            "install the stable release."
        )
    (site_dir / "prerelease.json").write_text(
        json.dumps({"tag": tag, "version": version}) + "\n", encoding="utf-8"
    )


def build_empty(site_dir: Path) -> None:
    """Write the no-RC state: a redirect to the stable site and a null tag."""
    out = site_dir / "prerelease"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "index.html").write_text(
        '<!doctype html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8" />\n'
        '<meta name="robots" content="noindex" />\n'
        '<meta http-equiv="refresh" content="0; url=../index.html" />\n'
        "<title>PixlStash</title>\n</head>\n<body>\n"
        '<p>No release candidate right now. <a href="../index.html">'
        "Go to the PixlStash site</a>.</p>\n</body>\n</html>\n",
        encoding="utf-8",
    )
    (site_dir / "prerelease.json").write_text(
        json.dumps({"tag": None}) + "\n", encoding="utf-8"
    )


def _write_output(name: str, value: str) -> None:
    """Append ``name=value`` to ``$GITHUB_OUTPUT`` when running in Actions."""
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select", help="Pick the RC to mirror")
    select.add_argument("--releases-json", type=Path, required=True)
    build_cmd = sub.add_parser("build", help="Write prerelease/ into the site")
    build_cmd.add_argument("--site-dir", type=Path, required=True)
    build_cmd.add_argument("--rc-website", type=Path)
    build_cmd.add_argument("--tag", default="")
    released = sub.add_parser("released", help="Is the code's version published?")
    released.add_argument("--pyproject", type=Path, required=True)
    released.add_argument("--latest-version", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "released":
        version = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))["project"][
            "version"
        ]
        latest = json.loads(args.latest_version.read_text(encoding="utf-8"))["version"]
        ok = is_released(version, latest)
        print(
            f"pyproject.toml is {version}, latest-version.json is {latest}: "
            + ("released" if ok else "not released yet")
        )
        _write_output("released", "true" if ok else "false")
        return

    if args.command == "select":
        releases = json.loads(args.releases_json.read_text(encoding="utf-8"))
        tag = pick_release_candidate(releases) or ""
        print(tag or "no release candidate in flight")
        _write_output("tag", tag)
    elif args.tag:
        if args.rc_website is None:
            parser.error("build --tag needs --rc-website")
        build(args.site_dir, args.rc_website, args.tag)
    else:
        build_empty(args.site_dir)


if __name__ == "__main__":
    main()
