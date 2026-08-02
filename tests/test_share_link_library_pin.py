"""A share link only serves the library it was minted for.

``/share/`` is auth-excluded and resolves its own token, so
``request.state.matched_token`` is never set and the authz gate's library pin
never sees it. Without an explicit check in the route, a link minted for library
A and opened while library B is active would serve **B's** picture carrying the
same numeric id: a recipient sees content from a library they were never shown.

Found by adversarial review 2026-08-02, on the one route the gate does not cover.
The check is tested here at the function it lives in rather than through HTTP,
so a routing change cannot quietly turn the assertion into a tautology.
"""

from types import SimpleNamespace

import pytest

from pixlstash.services.share_service import share_token_is_for_active_library

ACTIVE = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


def _auth(active_uuid):
    """A stand-in for the auth service, which is the only thing this needs."""
    return SimpleNamespace(active_library_uuid=lambda: active_uuid)


def _token(library_uuid):
    return SimpleNamespace(library_uuid=library_uuid, resource_id=1, scope="READ")


def test_a_token_for_the_active_library_is_served():
    """The positive direction. Over-blocking breaks every share link there is."""
    assert share_token_is_for_active_library(_auth(ACTIVE), _token(ACTIVE)) is True


def test_a_token_for_another_library_is_refused():
    """The leak this closes: same picture id, different library."""
    assert share_token_is_for_active_library(_auth(ACTIVE), _token(OTHER)) is False


def test_a_token_with_no_library_is_refused():
    """Fails closed rather than treating an unstamped token as universal."""
    assert share_token_is_for_active_library(_auth(ACTIVE), _token(None)) is False


def test_a_token_missing_the_field_entirely_is_refused():
    assert share_token_is_for_active_library(_auth(ACTIVE), SimpleNamespace()) is False


def test_no_registry_means_nothing_to_enforce():
    """A Vault built without a Server has no active library to compare against."""
    assert share_token_is_for_active_library(_auth(None), _token(OTHER)) is True


def test_the_route_actually_calls_the_check():
    """Guards against the check being deleted from the handler it protects.

    The function passing its own unit tests means nothing if the route stops
    calling it, and that is exactly the regression this class of bug arrives as.
    """
    import inspect

    from pixlstash.routes import share

    source = inspect.getsource(share.create_router)
    assert "share_token_is_for_active_library" in source
    # It must run before the picture is fetched, or the refusal happens after
    # the server has already looked up another library's picture.
    assert source.index("share_token_is_for_active_library") < source.index(
        "get_shared_picture"
    )


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_library_id_is_refused(value):
    assert share_token_is_for_active_library(_auth(ACTIVE), _token(value)) is False
