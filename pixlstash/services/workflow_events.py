"""Saying that a workflow card changed (v1.12 B4).

One emitter, because there are five places that change a card and they must
agree about the envelope: the card writes and the stack writes
(``routes/workflows.py``), a workflow file being imported or dropped in the
watched inbox (``routes/comfyui.py``, ``Server``), and a saved recipe being
written (``routes/recipes.py``).

**It is a "look again" signal and never a card.** Everything a card shows -
its counts, its cover strip, its rank, the stack it sits in - is computed per
request across the whole vault, so anything carried on the wire would be
something the client has to re-read anyway. The event says which keys changed
and why; ``GET /workflows`` says what they are now.
"""

from __future__ import annotations

from typing import Iterable, Optional

from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.ws.broadcaster import WORKFLOW_CHANGE_REASONS

logger = get_logger(__name__)


def announce_changed_workflows(
    server,
    keys: Iterable[str],
    reason: str,
    origin_client_id: Optional[str] = None,
) -> None:
    """Tell every connected tab to look at these cards again.

    Args:
        server: The Server instance. A vault it has not opened yet is not a
            fault: the inbox is reconciled once during start-up, before the
            vault exists, and those files are on the cards the first read of
            the view will draw anyway.
        keys: The card keys that changed. May be empty - a file that could not
            be keyed still changed the view.
        reason: One of :data:`pixlstash.ws.broadcaster.WORKFLOW_CHANGE_REASONS`.
            The broadcaster degrades an unknown one rather than rejecting it,
            so this only complains.
        origin_client_id: The originating tab's ``X-Client-Id``, so it can
            recognise the echo of its own change. ``None`` for the inbox and
            anything else that did not come from a request.
    """
    vault = getattr(server, "vault", None)
    if vault is None:
        return
    if reason not in WORKFLOW_CHANGE_REASONS:
        logger.warning(
            "A workflow change was announced with the unknown reason %r; the "
            "clients will read it as a plain change.",
            reason,
        )
    vault.notify(
        EventType.CHANGED_WORKFLOWS,
        {
            # "ui" whenever a request caused it, "external" for the watched
            # inbox and the start-up reconcile - the same distinction the
            # picture events make, and the one the SPA's decision rule reads.
            "source": "ui" if origin_client_id else "external",
            "origin_client_id": origin_client_id,
            "keys": list(keys),
            "reason": reason,
        },
    )
