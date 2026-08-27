"""The model shelf's icon store: dedup, refusals, and the digest containment.

The assertions worth having are the ones about what the store REFUSES. It is
the one place on the shelf where caller-supplied bytes are written to disk and
handed back from our own origin, so "it is an image" has to be a fact about the
payload rather than a claim about it - and the hash in a URL must not be able
to become a path.
"""

from __future__ import annotations

import hashlib
import os

import pytest

from pixlstash.services.model_icons import (
    MAX_ICON_BYTES,
    IconRefused,
    icon_dir,
    icon_path,
    media_type_of,
    store_icon,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64


@pytest.fixture
def hub_path(tmp_path):
    """A stand-in for the hub database path; only its directory is used."""
    return str(tmp_path / "hub.db")


def test_the_same_bytes_store_once(hub_path):
    """Dedup is the point, not a bonus.

    Forty Flux checkpoints wanting one logo is the normal case for an icon and
    the opposite of how samples behave, so the store is addressed by content.
    """
    first = store_icon(hub_path, PNG)
    second = store_icon(hub_path, PNG)
    assert first == second == hashlib.sha256(PNG).hexdigest()
    assert os.listdir(icon_dir(hub_path)) == [f"{first}.webp"]


def test_different_bytes_store_separately(hub_path):
    assert store_icon(hub_path, PNG) != store_icon(hub_path, JPEG)
    assert len(os.listdir(icon_dir(hub_path))) == 2


def test_every_accepted_format_is_accepted(hub_path):
    """The positive control. Over-blocking is its own regression, and WebP is
    the one whose magic is not a prefix - RIFF, four size bytes, then WEBP."""
    for data in (PNG, JPEG, WEBP):
        assert store_icon(hub_path, data)


def test_bytes_that_are_not_an_image_are_refused(hub_path):
    """Checked on the PAYLOAD, never on a filename or a client content type.

    The stored file is served back from our own origin, so accepting an
    attacker-influenced blob because it was *called* a png is the whole risk.
    """
    with pytest.raises(IconRefused) as exc:
        store_icon(hub_path, b"<html><script>alert(1)</script></html>")
    assert exc.value.reason == "not_an_image"
    assert not os.path.isdir(icon_dir(hub_path)) or not os.listdir(icon_dir(hub_path))


def test_an_svg_is_refused(hub_path):
    """SVG is script-bearing and is deliberately not in the allowlist, even
    though it is an image format a browser would render."""
    with pytest.raises(IconRefused):
        store_icon(hub_path, b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')


def test_an_empty_or_oversized_payload_is_refused(hub_path):
    with pytest.raises(IconRefused) as empty:
        store_icon(hub_path, b"")
    assert empty.value.reason == "empty"

    with pytest.raises(IconRefused) as big:
        store_icon(hub_path, PNG + b"\x00" * MAX_ICON_BYTES)
    assert big.value.reason == "too_large"


def test_a_segment_that_is_not_a_digest_never_becomes_a_path(hub_path):
    """The hash reaches this from a URL segment, so it is validated as a digest
    BEFORE it is joined - a traversal is refused as "not a digest" rather than
    surfacing as a containment error, and never as a read."""
    for bad in (
        "../../../etc/passwd",
        "..",
        "",
        "z" * 64,
        "abc",
        f"{'a' * 63}/",
    ):
        with pytest.raises(IconRefused) as exc:
            icon_path(hub_path, bad)
        assert exc.value.reason == "not_a_digest", bad


def test_a_real_digest_resolves_inside_the_icon_directory(hub_path):
    """The positive control for the check above."""
    digest = "a" * 64
    resolved = icon_path(hub_path, digest)
    assert resolved == os.path.join(icon_dir(hub_path), f"{digest}.webp")


def test_an_uppercase_digest_resolves_to_the_same_file(hub_path):
    """A hex digest is case-insensitive; two spellings must not become two
    files, or dedup would silently depend on how the client wrote the URL."""
    digest = store_icon(hub_path, PNG)
    assert icon_path(hub_path, digest.upper()) == icon_path(hub_path, digest)


def test_the_media_type_comes_from_the_bytes_not_the_suffix(hub_path):
    """The store names everything `.webp` and keeps the original encoding, so
    the suffix is a naming convention and would be a lie as a content type."""
    png_path = icon_path(hub_path, store_icon(hub_path, PNG))
    jpeg_path = icon_path(hub_path, store_icon(hub_path, JPEG))
    assert png_path.endswith(".webp") and jpeg_path.endswith(".webp")
    assert media_type_of(png_path) == "image/png"
    assert media_type_of(jpeg_path) == "image/jpeg"


def test_a_partial_write_is_never_left_under_the_final_name(hub_path):
    """Written to a temporary name and renamed.

    A truncated file under a name that claims to be the hash of its content is
    the one failure every later reader would trust, because the store's whole
    contract is that the name IS the hash.
    """
    store_icon(hub_path, PNG)
    assert not [n for n in os.listdir(icon_dir(hub_path)) if n.endswith(".partial")]
