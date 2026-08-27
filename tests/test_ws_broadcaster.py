"""Unit tests for the WebSocket broadcaster (pixlstash/ws/broadcaster.py).

Pins the load-bearing §15 invariant (docs/backend_architecture.md): the
broadcaster runs on a different task than the request, where the
``origin_client_id`` contextvar is dead, so it must derive every envelope field
from the event ``data`` dict ONLY - never from the contextvar. A relocation is
exactly the kind of change that could silently reintroduce a contextvar read,
so this test guards against it directly.
"""

from pixlstash.utils.request_origin import origin_client_id_var
from pixlstash.ws.broadcaster import WsBroadcasterMixin


def test_source_from_defaults_to_external_and_reads_data():
    assert WsBroadcasterMixin._source_from(None) == "external"
    assert WsBroadcasterMixin._source_from({}) == "external"
    assert WsBroadcasterMixin._source_from({"source": "ui"}) == "ui"
    # Legacy "user" migrates to "ui".
    assert WsBroadcasterMixin._source_from({"source": "user"}) == "ui"
    # Unknown values fall back to the external default.
    assert WsBroadcasterMixin._source_from({"source": "bogus"}) == "external"


def test_origin_from_defaults_to_none_and_reads_data():
    assert WsBroadcasterMixin._origin_from(None) is None
    assert WsBroadcasterMixin._origin_from({}) is None
    assert WsBroadcasterMixin._origin_from({"origin_client_id": "tab-1"}) == "tab-1"
    # Non-string origins are ignored.
    assert WsBroadcasterMixin._origin_from({"origin_client_id": 123}) is None


def test_change_kind_and_picture_ids_read_from_data():
    assert WsBroadcasterMixin._change_kind_from({"change_kind": "removed"}) == "removed"
    assert WsBroadcasterMixin._change_kind_from({"change_kind": "bogus"}) is None
    assert WsBroadcasterMixin._change_kind_from([1, 2]) is None

    # Every declared wire value survives the allowlist. The gate DROPS an
    # unrecognised value rather than raising, so a kind missing from
    # ``CHANGE_KINDS`` fails silently and the SPA falls back to "updated" -
    # which for a lifecycle change leaves a 404-clickable card behind.
    for kind in WsBroadcasterMixin.CHANGE_KINDS:
        assert WsBroadcasterMixin._change_kind_from({"change_kind": kind}) == kind
    # ``restored`` in particular: a scrapheap undo/restore is NOT an import.
    assert (
        WsBroadcasterMixin._change_kind_from({"change_kind": "restored"}) == "restored"
    )

    assert WsBroadcasterMixin._picture_ids_from({"picture_ids": [1, 2]}) == [1, 2]
    assert WsBroadcasterMixin._picture_ids_from({"ids": [3]}) == [3]
    assert WsBroadcasterMixin._picture_ids_from([4, 5]) == [4, 5]
    assert WsBroadcasterMixin._picture_ids_from(None) == []


def test_broadcaster_ignores_contextvar_reads_data_only():
    """The §15 invariant: even with the origin contextvar set, the envelope
    helpers derive nothing from it - only from ``data``."""
    token = origin_client_id_var.set("contextvar-tab-should-be-ignored")
    try:
        # No origin in data -> None, despite the contextvar being live.
        assert WsBroadcasterMixin._origin_from({}) is None
        assert WsBroadcasterMixin._source_from({}) == "external"
        # An explicit data origin wins and is unaffected by the contextvar.
        assert (
            WsBroadcasterMixin._origin_from({"origin_client_id": "data-tab"})
            == "data-tab"
        )
    finally:
        origin_client_id_var.reset(token)


def test_operation_log_undo_emits_origin_in_data_not_from_the_contextvar():
    """The same §15 invariant for the operation log's undo/redo emissions.

    Undo runs on the DB worker thread, so the ``origin_client_id`` contextvar is
    dead there exactly as it is on the broadcaster's loop. The op-log therefore
    receives the origin explicitly from the handler and puts it in the event
    ``data`` dict; a contextvar read anywhere on that path would silently
    misattribute every undo to whichever tab last touched the contextvar. This
    pins the *producer* side of the contract the assertions above pin for the
    consumer.
    """
    from pixlstash.event_types import EventType
    from pixlstash.services import operation_log_service

    emitted: list[tuple] = []

    class _Vault:
        def notify(self, event_type, data=None):
            emitted.append((event_type, data))

    token = origin_client_id_var.set("contextvar-tab-should-be-ignored")
    try:
        operation_log_service._emit(
            _Vault(), [1, 2], {operation_log_service.FACET_TAGS}, "undo-tab"
        )
        # Nothing was emitted with the contextvar's value...
        operation_log_service._emit(_Vault(), [3], {"score"}, None)
    finally:
        origin_client_id_var.reset(token)

    assert emitted, "undo emitted no event"
    with_origin = [
        data for _event, data in emitted if data.get("picture_ids") == [1, 2]
    ]
    assert with_origin
    for data in with_origin:
        assert data["origin_client_id"] == "undo-tab"
        # And the broadcaster derives the envelope from exactly this dict.
        assert WsBroadcasterMixin._origin_from(data) == "undo-tab"
        assert WsBroadcasterMixin._source_from(data) == "ui"

    # An undo with no originating tab defaults to no origin - never the
    # contextvar's live value.
    without_origin = [
        data for _event, data in emitted if data.get("picture_ids") == [3]
    ]
    assert without_origin
    for data in without_origin:
        assert data["origin_client_id"] is None
        assert WsBroadcasterMixin._origin_from(data) is None

    # A tag restoration announces both the tag and the grid event.
    assert EventType.CHANGED_TAGS in {event for event, _data in emitted}


def test_operation_log_emit_announces_the_scrapheap_lifecycle():
    """The producer side of the ``change_kind`` contract for scrapheap undo/redo.

    ``_emit`` splits one restoration into three announcements: pictures the
    restoration puts BACK are ``restored``, pictures it moves INTO the scrapheap
    are ``removed``, and everything else is an ordinary ``updated``.

    ``restored`` must not be ``added``. Both bring a card back, but the SPA
    reads ``added`` as "new to the vault" and flashes the sidebar's NEW marker
    on the counts that grew - a lie for a picture that was in the library the
    whole time (the reported bug). The value must also survive the broadcaster's
    allowlist, or the hint is dropped and the grid falls back to ``updated``.
    """
    from pixlstash.event_types import EventType
    from pixlstash.services import operation_log_service

    emitted: list[tuple] = []

    class _Vault:
        def notify(self, event_type, data=None):
            emitted.append((event_type, data))

    operation_log_service._emit(
        _Vault(),
        [1, 2, 3],
        {operation_log_service.FACET_DELETED},
        "undo-tab",
        lifecycle={"scrapheaped": [3], "restored": [1]},
    )

    # Picture 2 is announced twice on purpose (see the stack-facet test below),
    # so pick the ordinary announcement by its absence of a ``fields`` list
    # rather than by "the last one with these ids".
    by_ids = {
        tuple(data["picture_ids"]): data
        for _event, data in emitted
        if not data.get("fields")
    }
    assert by_ids[(1,)]["change_kind"] == "restored"
    assert by_ids[(3,)]["change_kind"] == "removed"
    assert by_ids[(2,)]["change_kind"] == "updated"

    for data in by_ids.values():
        # Every kind survives the envelope gate - a dropped hint is the silent
        # failure mode this whole test exists to catch.
        assert WsBroadcasterMixin._change_kind_from(data) == data["change_kind"]
        assert data["origin_client_id"] == "undo-tab"
        assert WsBroadcasterMixin._source_from(data) == "ui"

    assert all(
        event == EventType.CHANGED_PICTURES
        for event, data in emitted
        if data["change_kind"] in ("restored", "removed")
    )


def test_operation_log_emit_announces_the_surviving_stack_members_count():
    """A lifecycle move changes the live member count of the stacks it touched.

    The members that did NOT move render that count as their stack badge, and
    they carry no facet diff, so they are not even in ``picture_ids``: undoing a
    "Keep cover only" whose union was a no-op put four copies back and announced
    nothing at all about the cover, which went on drawing itself as a stack of
    one. ``lifecycle["stack_siblings"]`` names them and they get their own
    ``fields=["stack_count"]`` announcement.

    It is ADDITIVE: the blanket no-``fields`` ``updated`` still goes out for
    anything in ``picture_ids``, so a facet change that may affect any view
    keeps its conservative treatment.
    """
    from pixlstash.event_types import EventType
    from pixlstash.services import operation_log_service

    emitted: list[tuple] = []

    class _Vault:
        def notify(self, event_type, data=None):
            emitted.append((event_type, data))

    operation_log_service._emit(
        _Vault(),
        [1, 2, 3],
        {operation_log_service.FACET_DELETED},
        "undo-tab",
        lifecycle={"scrapheaped": [3], "restored": [1], "stack_siblings": [7]},
    )

    stack_events = [
        data for _event, data in emitted if data.get("fields") == ["stack_count"]
    ]
    assert len(stack_events) == 1, emitted
    stack = stack_events[0]
    # The survivors, and only them: the moved pictures already carry a lifecycle
    # kind of their own, and merging the two would tell the grid a vanished
    # picture was merely updated and leave a 404-clickable card behind.
    assert stack["picture_ids"] == [7]
    assert stack["change_kind"] == "updated"
    assert stack["origin_client_id"] == "undo-tab"
    assert WsBroadcasterMixin._source_from(stack) == "ui"
    assert all(
        event == EventType.CHANGED_PICTURES
        for event, data in emitted
        if data.get("fields") == ["stack_count"]
    )
    # The conservative announcement over the touched-but-unmoved picture is
    # still there, unnarrowed.
    assert any(
        data["picture_ids"] == [2] and not data.get("fields")
        for _event, data in emitted
    )


def test_operation_log_emit_stays_silent_about_stacks_when_nothing_is_stacked():
    """No stack siblings, no stack announcement.

    An ordinary metadata undo (a tag edit, a score) cannot change any stack's
    live member count, so the extra event would be pure churn: every receiving
    tab would issue a stack read for nothing.
    """
    from pixlstash.services import operation_log_service

    emitted: list[tuple] = []

    class _Vault:
        def notify(self, event_type, data=None):
            emitted.append((event_type, data))

    operation_log_service._emit(
        _Vault(),
        [1, 2, 3],
        {operation_log_service.FACET_SCORE},
        "undo-tab",
        lifecycle={"scrapheaped": [], "restored": [], "stack_siblings": []},
    )

    assert emitted, "an undo must still announce itself"
    assert not [data for _event, data in emitted if data.get("fields")]
