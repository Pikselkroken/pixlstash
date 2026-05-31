"""Whole-file zstd compression for vault snapshot archives.

Snapshots are full SQLite copies of the vault DB. To keep the expensive,
GPU-regenerated blobs (CLIP image/text embeddings, InsightFace face
features) inside the snapshot *without* the on-disk cost, the ``.sqlite``
file is compressed with zstd at rest and decompressed to a temp file only
when it is actually read (restore / preview).

SQLite cannot query a compressed file in place — its pager needs random,
seekable page access — so a snapshot is treated as an archive: compress on
creation, decompress to a scratch ``.sqlite`` before any DB work. This module
is the single place that knows the on-disk format.

Streaming (chunked) compress/decompress is used so a multi-GB snapshot never
has to be held in memory.
"""

import os

import zstandard

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# On-disk suffixes. New snapshots are written compressed; legacy snapshots
# created before compression remain plain ``.sqlite`` and are detected by the
# absence of the ``.zst`` suffix so they still restore.
COMPRESSED_SUFFIX = ".sqlite.zst"
LEGACY_SUFFIX = ".sqlite"

# Compression level. Level 3 is zstd's default: it gave a ~3x ratio on real
# embedding-heavy snapshots at ~0.03s for a 14 MB file (≈8x faster than gzip
# for an essentially identical ratio). Higher levels buy little here because
# float32 embeddings are close to incompressible — the win is structural.
_COMPRESSION_LEVEL = 3

# Chunk size for streaming copy_stream (1 MiB).
_CHUNK_SIZE = 1 << 20


def is_compressed(path: str) -> bool:
    """Return True if *path* names a zstd-compressed snapshot archive.

    Args:
        path: Snapshot file path (absolute or relative).

    Returns:
        True when the path ends with ``.sqlite.zst``.
    """
    return path.endswith(".zst")


def compress_snapshot(src_sqlite_path: str, dst_zst_path: str) -> int:
    """Compress a plain SQLite file to a zstd archive.

    Streams the source through zstd so the whole DB is never buffered in
    memory.

    Args:
        src_sqlite_path: Path to the source ``.sqlite`` file.
        dst_zst_path: Destination path for the ``.sqlite.zst`` archive.

    Returns:
        Size of the written archive in bytes.

    Raises:
        OSError: If the source cannot be read or the destination written.
        zstandard.ZstdError: If compression fails.
    """
    cctx = zstandard.ZstdCompressor(level=_COMPRESSION_LEVEL)
    with open(src_sqlite_path, "rb") as src, open(dst_zst_path, "wb") as dst:
        cctx.copy_stream(src, dst, read_size=_CHUNK_SIZE, write_size=_CHUNK_SIZE)
    size = os.path.getsize(dst_zst_path)
    logger.info(
        "snapshot_compression: compressed %s (%d bytes) → %s (%d bytes)",
        src_sqlite_path,
        os.path.getsize(src_sqlite_path),
        dst_zst_path,
        size,
    )
    return size


def decompress_snapshot(src_zst_path: str, dst_sqlite_path: str) -> None:
    """Decompress a zstd snapshot archive to a plain SQLite file.

    Streams so a multi-GB snapshot is never fully buffered.

    Args:
        src_zst_path: Path to the ``.sqlite.zst`` archive.
        dst_sqlite_path: Destination path for the decompressed ``.sqlite``.

    Raises:
        OSError: If the source cannot be read or the destination written.
        zstandard.ZstdError: If the archive is corrupt.
    """
    dctx = zstandard.ZstdDecompressor()
    with open(src_zst_path, "rb") as src, open(dst_sqlite_path, "wb") as dst:
        dctx.copy_stream(src, dst, read_size=_CHUNK_SIZE, write_size=_CHUNK_SIZE)
    logger.debug(
        "snapshot_compression: decompressed %s → %s (%d bytes)",
        src_zst_path,
        dst_sqlite_path,
        os.path.getsize(dst_sqlite_path),
    )


def materialize_snapshot(src_path: str, dst_sqlite_path: str) -> None:
    """Produce a usable plain ``.sqlite`` at *dst_sqlite_path* from *src_path*.

    Decompresses when *src_path* is a zstd archive; otherwise copies the
    legacy plain ``.sqlite`` byte-for-byte. Either way the caller is left with
    an independent, writable SQLite file it can alembic-upgrade and read.

    Args:
        src_path: On-disk snapshot path (``.sqlite`` or ``.sqlite.zst``).
        dst_sqlite_path: Destination for the plain ``.sqlite`` copy.

    Raises:
        OSError / zstandard.ZstdError: On read/write/decompression failure.
    """
    if is_compressed(src_path):
        decompress_snapshot(src_path, dst_sqlite_path)
    else:
        import shutil

        shutil.copy2(src_path, dst_sqlite_path)
