"""The tag chip size of #2139, per user.

Stored in the hub beside the user row (hub rule: new tables, no new ``user``
columns) and served in ``/users/me/config`` as ``tag_chip_size``. It is the
owner's own working preference, like ``hidden_tags``, so it is the same in
every library.
"""

from __future__ import annotations

from typing import Optional

CHIP_SIZES = ("compact", "comfortable", "large")
DEFAULT_CHIP_SIZE = "comfortable"

#: The config key this module owns. The PATCH handler pops it before the
#: ``user`` row patch, which refuses keys it does not know.
CONFIG_KEY = "tag_chip_size"


def read(hub, user_id: Optional[int]) -> str:
    """The stored chip size, or the default when nothing is stored."""
    if hub is None or user_id is None:
        return DEFAULT_CHIP_SIZE
    row = hub.fetchone(
        "SELECT chip_size FROM user_tag_preference WHERE user_id = ?", (user_id,)
    )
    if row is not None and row["chip_size"] in CHIP_SIZES:
        return row["chip_size"]
    return DEFAULT_CHIP_SIZE


def validate(chip_size) -> str:
    """Return *chip_size* when it is one of the three sizes.

    Raises:
        ValueError: It is not.
    """
    if chip_size not in CHIP_SIZES:
        raise ValueError(f"tag_chip_size must be one of {', '.join(CHIP_SIZES)}")
    return chip_size


def write(hub, user_id: int, chip_size) -> None:
    """Store the chip size.

    Raises:
        ValueError: It is not one of the three sizes.
    """
    chip_size = validate(chip_size)
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO user_tag_preference (user_id, chip_size) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET chip_size = excluded.chip_size",
            (user_id, chip_size),
        )
