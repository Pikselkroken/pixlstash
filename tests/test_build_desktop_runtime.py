"""Unit tests for the desktop-runtime build script's download integrity check.

These cover the SHA256 verification added to ``fetch_standalone_python`` so a
truncated / redirect-HTML / tampered CPython tarball is rejected (and never
cached as valid) instead of failing confusingly later at ``tarfile.open``. No
network is touched: the download and the published-checksum fetch are stubbed.
"""

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

# The build script lives under scripts/ (not an installed package), so load it
# by path rather than importing pixlstash.*.
_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "build_desktop_runtime.py"
)
_spec = importlib.util.spec_from_file_location("build_desktop_runtime", _SCRIPT)
build_desktop_runtime = importlib.util.module_from_spec(_spec)
sys.modules["build_desktop_runtime"] = build_desktop_runtime
_spec.loader.exec_module(build_desktop_runtime)

TRIPLE = "x86_64-unknown-linux-gnu"


def _write_fake_archive(path: Path, body: bytes) -> str:
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def test_download_rejected_on_sha256_mismatch(tmp_path, monkeypatch):
    """A downloaded body whose digest differs from the published one is refused."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    dest_dir = tmp_path / "out"
    dest_dir.mkdir()

    # The "published" checksum the script will compare against.
    monkeypatch.setattr(
        build_desktop_runtime,
        "fetch_expected_sha256",
        lambda triple: "0" * 64,
    )

    # Simulate a download that writes a body NOT matching the expected digest
    # (e.g. an HTML redirect page or a truncated tarball).
    def fake_download(url, dest):
        dest.write_bytes(b"<html>404 not found</html>")

    monkeypatch.setattr(build_desktop_runtime, "download", fake_download)

    with pytest.raises(SystemExit) as exc:
        build_desktop_runtime.fetch_standalone_python(TRIPLE, dest_dir, cache_dir)
    assert "SHA256 verification" in str(exc.value)

    # The bad body must NOT have been cached under the real asset name.
    cached = cache_dir / build_desktop_runtime.pbs_asset_name(TRIPLE)
    assert not cached.exists()
    # The temp .part file must be cleaned up too.
    assert not (cached.with_suffix(cached.suffix + ".part")).exists()


def test_poisoned_cache_is_dropped_and_redownloaded(tmp_path, monkeypatch):
    """A pre-existing cached file that no longer matches is dropped, not trusted."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    dest_dir = tmp_path / "out"
    dest_dir.mkdir()

    cached = cache_dir / build_desktop_runtime.pbs_asset_name(TRIPLE)
    _write_fake_archive(cached, b"poisoned-old-body")  # wrong digest on purpose

    good_body = b"the-only-trusted-bytes"
    expected = hashlib.sha256(good_body).hexdigest()
    monkeypatch.setattr(
        build_desktop_runtime, "fetch_expected_sha256", lambda triple: expected
    )

    downloaded = {"called": False}

    def fake_download(url, dest):
        downloaded["called"] = True
        dest.write_bytes(good_body)

    monkeypatch.setattr(build_desktop_runtime, "download", fake_download)

    # We only care that the mismatched cache was dropped and a fresh, verified
    # download happened; stub extraction to avoid needing a real tar (our fake
    # body is not a valid gzip tar).
    class _FakeTar:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extractall(self, dest):
            (Path(dest) / "python").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        build_desktop_runtime.tarfile, "open", lambda *a, **k: _FakeTar()
    )

    result = build_desktop_runtime.fetch_standalone_python(TRIPLE, dest_dir, cache_dir)

    assert downloaded["called"] is True
    assert result == dest_dir / "python"
    # The cache now holds the good, verified body.
    assert cached.read_bytes() == good_body
