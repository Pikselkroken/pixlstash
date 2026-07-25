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
