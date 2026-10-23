"""A workflow's own picture, the custom thumbnail its card shows.

The image itself is in the hub's content-addressed icon store
(:mod:`pixlstash.services.model_icons`); a ``workflow_thumbnail`` row names it
by hash. Unpinned, the card shows it only until the workflow has pictures of
its own, which is how a workflow made from a ComfyUI template starts; pinned,
it is shown in place of them.
"""

from __future__ import annotations

from dataclasses import dataclass

from pixlstash.hub.db import HubDatabase


@dataclass(frozen=True)
class Thumbnail:
    """One workflow's custom thumbnail: the stored image and whether it is pinned."""

    icon_sha256: str
    pinned: bool


def thumbnails(hub: HubDatabase) -> dict[str, Thumbnail]:
    """Every workflow's custom thumbnail, by workflow id."""
    return {
        row["workflow_id"]: Thumbnail(row["icon_sha256"], bool(row["pinned"]))
        for row in hub.fetchall(
            "SELECT workflow_id, icon_sha256, pinned FROM workflow_thumbnail"
        )
    }


def set_thumbnail(
    hub: HubDatabase, workflow_id: str, icon_sha256: str, pinned: bool
) -> None:
    """Give *workflow_id* the stored image *icon_sha256*, replacing any before."""
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_thumbnail (workflow_id, icon_sha256, pinned) "
            "VALUES (?, ?, ?) ON CONFLICT(workflow_id) DO UPDATE SET "
            "icon_sha256 = excluded.icon_sha256, pinned = excluded.pinned",
            (workflow_id, icon_sha256, int(pinned)),
        )


def set_pinned(hub: HubDatabase, workflow_id: str, pinned: bool) -> bool:
    """Pin or unpin *workflow_id*'s thumbnail; ``False`` when it has none."""
    with hub.transaction() as conn:
        return bool(
            conn.execute(
                "UPDATE workflow_thumbnail SET pinned = ? WHERE workflow_id = ?",
                (int(pinned), workflow_id),
            ).rowcount
        )
