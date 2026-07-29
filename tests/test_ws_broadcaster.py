"""Unit tests for the WebSocket broadcaster (pixlstash/ws/broadcaster.py).

Pins the load-bearing §15 invariant (docs/backend_architecture.md): the
broadcaster runs on a different task than the request, where the
``origin_client_id`` contextvar is dead, so it must derive every envelope field
from the event ``data`` dict ONLY — never from the contextvar. A relocation is
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

    assert WsBroadcasterMixin._picture_ids_from({"picture_ids": [1, 2]}) == [1, 2]
    assert WsBroadcasterMixin._picture_ids_from({"ids": [3]}) == [3]
    assert WsBroadcasterMixin._picture_ids_from([4, 5]) == [4, 5]
    assert WsBroadcasterMixin._picture_ids_from(None) == []


def test_broadcaster_ignores_contextvar_reads_data_only():
    """The §15 invariant: even with the origin contextvar set, the envelope
    helpers derive nothing from it — only from ``data``."""
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

    # An undo with no originating tab defaults to no origin — never the
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
