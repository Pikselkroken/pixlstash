"""One definition of the SQLite bound-parameter cap and the chunker that respects it.

SQLite limits how many bound parameters a single statement may carry
(``SQLITE_LIMIT_VARIABLE_NUMBER``: 32766 on current builds, 999 on older ones).
Any ``WHERE col IN (:a, :b, ...)`` built from a caller-sized id list can cross
that cap and fail at execution time, so those loads are chunked.

The limit and the chunker used to be redeclared in five modules, each with its
own copy of this rationale and no shared value to change. They live here now.
"""

from typing import Iterator, Sequence, TypeVar

T = TypeVar("T")

# Chunk size for ``IN`` lists built from ids. Sits under the 999-parameter floor
# with room for the other bound values a statement carries alongside the list.
SQLITE_ID_CHUNK = 900


def chunked(seq: Sequence[T], size: int = SQLITE_ID_CHUNK) -> Iterator[Sequence[T]]:
    """Yield consecutive slices of *seq* of at most *size* items.

    Args:
        seq: Sequence to split. An empty sequence yields nothing.
        size: Maximum items per slice; defaults to :data:`SQLITE_ID_CHUNK`.

    Yields:
        Slices of *seq* in order, each at most *size* long.

    Raises:
        ValueError: *size* is not positive (which would loop forever).
    """
    if size <= 0:
        raise ValueError(f"chunk size must be positive, got {size}")
    for start in range(0, len(seq), size):
        yield seq[start : start + size]
