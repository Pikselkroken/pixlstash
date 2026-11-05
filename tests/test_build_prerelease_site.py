"""Tests for scripts/build_prerelease_site.py, the pixlstash.dev/prerelease/ builder."""

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "build_prerelease_site.py"
)
_spec = importlib.util.spec_from_file_location("build_prerelease_site", _SCRIPT)
bps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bps)

REPO = "https://github.com/pikselkroken/pixlstash"
IMAGE = "ghcr.io/pikselkroken/pixlstash"


def _release(tag, prerelease=False, draft=False):
    return {"tagName": tag, "isPrerelease": prerelease, "isDraft": draft}


@pytest.mark.parametrize(
    ("releases", "expected"),
    [
        # The real list on the day this was written.
        (
            [
                _release("v2.0.0rc1", True),
                _release("v2.0.0.dev.3", True),
                _release("v1.13.2"),
                _release("v1.13.0rc1", True),
                _release("v1.13.0"),
            ],
            "v2.0.0rc1",
        ),
        # An rc of a version that has since shipped is retired.
        ([_release("v2.0.0"), _release("v2.0.0rc1", True)], None),
        # An rc older than a later stable line is retired too.
        ([_release("v2.0.1"), _release("v2.0.0rc3", True)], None),
        # Dev builds, alphas and betas are not release candidates.
        ([_release("v2.0.0.dev.3", True), _release("v1.13.2")], None),
        ([_release("v2.0.0b1", True), _release("v1.13.2")], None),
        # Drafts are not published.
        ([_release("v2.0.0rc1", True, draft=True), _release("v1.13.2")], None),
        ([_release("v1.13.2"), _release("v1.13.1")], None),
        ([], None),
        # With two in flight, the newest wins; numeric, not string, order.
        (
            [
                _release("v2.0.0rc2", True),
                _release("v2.0.0rc10", True),
                _release("v1.13.2"),
            ],
            "v2.0.0rc10",
        ),
    ],
)
def test_pick_release_candidate(releases, expected):
    assert bps.pick_release_candidate(releases) == expected


@pytest.fixture
def rc_website(tmp_path):
    """A small website with one instance of every pattern the builder rewrites."""
    site = tmp_path / "rc" / "website"
    (site / "latest-version").mkdir(parents=True)
    (site / "latest-version" / "electron.json").write_text('{"version": "1.13.2"}')
    (site / "api").mkdir()
    (site / "index.html").write_text(
        "<html><head><title>x</title></head><body><p>home</p></body></html>"
    )
    (site / "install.html").write_text(
        "<html>\n<head>\n</head>\n<body class='x'>\n"
        f'<a href="{REPO}/releases/latest">Download</a>\n'
        f"<code>docker run {IMAGE}:latest</code>\n"
        f'<button data-copy="docker run {IMAGE}:latest-gpu">copy</button>\n'
        "<code>pip install pixlstash</code>\n"
        '<button data-copy="pip install pixlstash">copy</button>\n'
        "<code>pip install pixlstash[cuda]</code>\n"
        "<code>pip install --upgrade pixlstash</code>\n"
        "</body>\n</html>\n"
    )
    (site / "api" / "index.html").write_text(
        "<html><head></head><body>api</body></html>"
    )
    return site


def test_build_rewrites_the_mirror_to_the_release_candidate(tmp_path, rc_website):
    site_dir = tmp_path / "_site"
    site_dir.mkdir()
    bps.build(site_dir, rc_website, "v2.0.0rc1")
    out = site_dir / "prerelease"

    install = (out / "install.html").read_text()
    assert f"{REPO}/releases/tag/v2.0.0rc1" in install
    assert "releases/latest" not in install
    assert f"{IMAGE}:2.0.0rc1<" in install
    assert f"{IMAGE}:2.0.0rc1-gpu" in install
    assert ":latest" not in install
    assert install.count("pip install pixlstash==2.0.0rc1") == 2
    assert "pip install --upgrade pixlstash==2.0.0rc1" in install
    assert "pip install pixlstash[cuda]" in install

    # The stable manifest would turn the RC buttons back into stable downloads.
    assert not (out / "latest-version").exists()
    assert json.loads((site_dir / "prerelease.json").read_text()) == {
        "tag": "v2.0.0rc1",
        "version": "2.0.0rc1",
    }

    issue = f"{REPO}/issues/new?template=bug_report.md&amp;title=%5B2.0.0rc1%5D%20"
    for page, root in (("index.html", "../"), ("install.html", "../"),
                       ("api/index.html", "../../")):  # fmt: skip
        text = (out / page).read_text()
        assert text.count('<meta name="robots" content="noindex" />') == 1, page
        assert "release-candidate site for <strong>2.0.0rc1</strong>" in text, page
        assert f'href="{root}index.html">Back to the stable site' in text, page
        assert f'href="{issue}"' in text, page
        assert 'rel="noopener"' in text, page
        # Injected inside <body>, not before it.
        assert text.index("<body") < text.index('class="rc-banner"'), page


def test_build_refuses_a_site_where_a_rewrite_matches_nothing(tmp_path, rc_website):
    install = rc_website / "install.html"
    install.write_text(install.read_text().replace("pip install", "uv add"))
    with pytest.raises(RuntimeError, match="pip install pixlstash"):
        bps.build(tmp_path, rc_website, "v2.0.0rc1")
    assert not (tmp_path / "prerelease.json").exists()


def test_build_refuses_a_missing_checkout(tmp_path):
    with pytest.raises(FileNotFoundError):
        bps.build(tmp_path, tmp_path / "nope", "v2.0.0rc1")


def test_build_empty_redirects_to_the_stable_site(tmp_path):
    bps.build_empty(tmp_path)
    assert json.loads((tmp_path / "prerelease.json").read_text()) == {"tag": None}
    index = (tmp_path / "prerelease" / "index.html").read_text()
    assert 'http-equiv="refresh" content="0; url=../index.html"' in index


def test_select_writes_the_tag_to_github_output(tmp_path, monkeypatch, capsys):
    releases = tmp_path / "releases.json"
    releases.write_text(json.dumps([_release("v2.0.0rc1", True), _release("v1.13.2")]))
    output = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    bps.main(["select", "--releases-json", str(releases)])
    assert output.read_text() == "tag=v2.0.0rc1\n"

    releases.write_text(json.dumps([_release("v1.13.2")]))
    output.unlink()
    bps.main(["select", "--releases-json", str(releases)])
    assert output.read_text() == "tag=\n"
