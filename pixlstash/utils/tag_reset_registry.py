"""In-memory record of when each picture's tags were last reset (#1361).

A retag deletes a picture's Tag rows and writes a fresh sentinel, but the new
sentinel can be byte-identical to the old one, so the Tag table alone cannot
tell a tag task that captured the picture *before* the reset from one queued
after it. Without that distinction an older in-flight task clears the new
sentinel with its stale output, and the task the retag asked for then keeps
those rows as if another writer had added them.

Every writer that replaces a picture's tags *in place of the tagger* - a
retag reset, and a sidecar replacing them on an existing picture - calls
:meth:`mark_reset` after its commit. A manual edit does not: a tag the owner
adds or replaces while tagging is pending keeps the #1357 behaviour, where the
tagger adds its tags beside it. a :class:`TagTask` reads :meth:`current` before its pictures are read
and drops the write for any picture reset since. Marking after the commit makes
the check err one way only: a task can at worst be judged stale when it was not,
which leaves the sentinel for the next sweep.

Process-lifetime is enough because tag tasks do not survive a restart.
"""

import threading


class TagResetRegistry:
    """Thread-safe monotonic reset generation per picture."""

    def __init__(self):
        self._lock = threading.Lock()
        self._generation = 0
        # ponytail: one int per picture ever reset, bounded by the library size.
        self._reset_at: dict[int, int] = {}

    def current(self) -> int:
        """The generation a task captures before reading its pictures."""
        with self._lock:
            return self._generation

    def mark_reset(self, picture_ids) -> None:
        """Record that *picture_ids* just had their tags replaced."""
        with self._lock:
            self._generation += 1
            for pic_id in picture_ids:
                self._reset_at[int(pic_id)] = self._generation

    def reset_since(self, picture_ids, generation: int) -> set[int]:
        """The ids in *picture_ids* whose tags were reset after *generation*."""
        with self._lock:
            return {
                pic_id
                for pic_id in picture_ids
                if self._reset_at.get(pic_id, 0) > generation
            }
