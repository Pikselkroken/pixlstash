"""Keeps smart picture sets (#2148) up to date as the library changes.

A smart set's rule is a ``GET /pictures`` query string, so it is evaluated by
the listing itself (`select_pictures_for_listing`) on a request built from that
string: the set holds what the grid showed for the same filter, by the same
code. The refresher runs it when library events say pictures may have changed,
at most once per :data:`REFRESH_WINDOW_S`, and writes the members through
`services/smart_sets.py`.
"""

import sys
import threading
from typing import Iterable

from starlette.requests import Request

from pixlstash.database import DBPriority
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services.smart_sets import (
    apply_members_in_session,
    smart_sets_to_refresh_in_session,
)

logger = get_logger(__name__)

# The events after which a picture may match a rule it did not before.
REFRESH_EVENTS = frozenset(
    {
        EventType.CHANGED_PICTURES,
        EventType.PICTURE_IMPORTED,
        EventType.CHANGED_TAGS,
        EventType.CLEARED_TAGS,
        EventType.CHANGED_CHARACTERS,
        EventType.CHANGED_FACES,
        EventType.CHANGED_DESCRIPTIONS,
        EventType.QUALITY_UPDATED,
        EventType.RESTORE_COMPLETED,
        EventType.LIBRARY_SWITCHED,
    }
)

# The `source` of the change event a refresh sends. A refresh does not wake
# another one for its own change.
SMART_SET_SOURCE = "smart_set"

# How long a burst of events (an import, a tagging pass) is gathered before one
# refresh runs. Fixed, not trailing: steady background work still refreshes.
REFRESH_WINDOW_S = 2.0


def rule_picture_ids(server, query: str, project_ids: Iterable[int]) -> set[int]:
    """The pictures a rule matches, as the owner's grid would list them.

    A set in projects matches only pictures in one of them: its projects scope
    its rule, so a smart set never pulls a picture into a project by itself.
    """
    # Imported here, not at the top: the listing imports the routes package,
    # which imports the server's routers, and this module is imported by the
    # server.
    from pixlstash.routes.pictures import select_pictures_for_listing

    scopes = [str(pid) for pid in project_ids] or [None]
    found: set[int] = set()
    for project_id in scopes:
        request = Request(
            {"type": "http", "query_string": query.encode(), "headers": []}
        )
        found.update(
            select_pictures_for_listing(
                server=server,
                request=request,
                sort=None,
                descending=True,
                offset=0,
                limit=sys.maxsize,
                metadata_fields=[],
                return_ids_only=True,
                project_id=project_id,
            )
        )
    return found


def refresh_smart_sets(server, set_ids: Iterable[int] | None = None) -> list[int]:
    """Bring smart sets' members up to date now; returns the ids that changed.

    Every unlocked smart set, or only ``set_ids``. One set's failure is logged
    and leaves the others to run.
    """
    vault = server.vault
    rows = vault.db.run_immediate_read_task(
        smart_sets_to_refresh_in_session,
        list(set_ids) if set_ids is not None else None,
    )
    changed: list[int] = []
    for set_id, query, project_ids in rows:
        try:
            ids = rule_picture_ids(server, query, project_ids)
            changed += vault.db.run_task(
                apply_members_in_session,
                set_id,
                ids,
                priority=DBPriority.IMMEDIATE,
            )
        except Exception as exc:
            logger.warning(
                "Smart set %s was not refreshed (filter %r): %s",
                set_id,
                query,
                exc,
                exc_info=True,
            )
    if changed:
        vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": sorted(set(changed)),
                "source": SMART_SET_SOURCE,
                "change_kind": "updated",
            },
        )
    return changed


class SmartSetRefresher:
    """Runs :func:`refresh_smart_sets` after library events, gathered.

    Attributes:
        server: The server whose current vault is refreshed.
    """

    def __init__(self, server):
        self.server = server
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._closed = False

    def on_event(self, event_type: EventType, data=None) -> None:
        """A vault event: schedule a refresh if pictures may have changed."""
        if event_type not in REFRESH_EVENTS:
            return
        if isinstance(data, dict) and data.get("source") == SMART_SET_SOURCE:
            return
        with self._lock:
            if self._closed or self._timer is not None:
                return
            self._timer = threading.Timer(REFRESH_WINDOW_S, self._run)
            self._timer.daemon = True
            self._timer.start()

    def close(self) -> None:
        """Stop: a pending refresh is dropped and none is scheduled again."""
        with self._lock:
            self._closed = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _run(self) -> None:
        with self._lock:
            self._timer = None
            if self._closed:
                return
        try:
            refresh_smart_sets(self.server)
        except Exception as exc:
            logger.warning("Smart set refresh failed: %s", exc, exc_info=True)
