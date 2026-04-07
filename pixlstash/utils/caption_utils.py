"""Utilities for normalising tag and caption strings."""


def sanitise_tag(tag: str) -> str:
    """Return a human-readable form of a WD14 tag.

    Replaces underscores with spaces and strips surrounding whitespace,
    preserving the original tag vocabulary that diffusion users expect.

    Args:
        tag: Raw WD14 tag string, e.g. ``'1girl'`` or ``'open_mouth'``.

    Returns:
        Sanitised tag string, e.g. ``'open mouth'``.
    """
    return tag.replace("_", " ").strip().lower()
