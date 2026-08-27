"""Anti-vacuity guard for the authorization/scope test suites.

``server.py`` registers a catch-all ``@self.api.get("/{full_path:path}")`` that
serves the built frontend's ``index.html`` for any unmatched GET. The consequence
is easy to miss and hard to notice: **a GET to a nonexistent API path returns
200**, so a bare ``assert resp.status_code == 200`` is fully satisfied by a
typo'd, renamed, or never-existing URL, and proves nothing at all.

That is not hypothetical. ``test_integration_scoped_list_pass_through_not_over
blocked`` asserted 200 against ``/stacks/{id}/stack`` - a route that has never
existed - and stayed green while its docstring claimed to cover
``/stacks/{id}/pictures``, one of this repo's three historical whole-library BOLA
leaks (CLAUDE.md, "Security & authorization review process"). A red test gets
fixed; a green one that proves nothing silently certifies a leak vector.

The remedy has to be structural rather than a one-off URL correction, because the
failure mode is silence. :func:`no_spa_fallback` makes the vacuous case impossible
to reintroduce anywhere in a module that opts in: **any 2xx answered by the
catch-all is an immediate hard failure.** It requires no per-assertion discipline
-- a helper that must be remembered at every call site is precisely the
"correctness by remembering" pattern that §16.2 exists to abolish -- and it leaves
deliberate absent-route probes alone (e.g.
``test_test_hooks_route_is_absent_unless_the_flag_is_on``, which asserts 404/405):
non-2xx responses are never flagged.

Usage, one line per module::

    from tests.authz_guard import no_spa_fallback  # noqa: F401

    pytestmark = pytest.mark.usefixtures("no_spa_fallback")

This module is the interim home. The guard belongs in ``tests/conftest.py`` as an
``autouse`` fixture, which would extend it to every suite without the opt-in line;
that file was owned by another change when this landed.
"""

import pytest
from starlette.routing import Match
from starlette.testclient import TestClient

SPA_FALLBACK_PATH = "/{full_path:path}"


def resolves_to_real_route(app, method: str, path: str) -> bool:
    """Report whether ``method path`` matches a route other than the SPA fallback.

    Args:
        app: The Starlette/FastAPI app whose route table is consulted.
        method: HTTP method, e.g. ``"GET"``.
        path: A concrete request path, e.g. ``"/api/v1/stacks/3/pictures"``.

    Returns:
        ``True`` when some real (non-catch-all) route fully matches, else ``False``.
    """
    scope = {
        "type": "http",
        "method": method.upper(),
        "path": path,
        "root_path": "",
        "headers": [],
    }
    for route in getattr(app, "routes", []):
        try:
            match, _ = route.matches(scope)
        except Exception as exc:
            # A route that cannot evaluate the scope simply is not a match here.
            # Logged rather than swallowed so a routing-API change is visible.
            print(f"authz_guard: route {route!r} failed to match {path!r}: {exc}")
            continue
        if match == Match.FULL and getattr(route, "path", None) != SPA_FALLBACK_PATH:
            return True
    return False


def assert_real_route(app, method: str, path: str) -> None:
    """Assert ``method path`` is a mounted API route, not the SPA catch-all.

    Use at the top of a test that hardcodes a security-relevant URL, so a renamed
    or deleted route fails loudly at the assertion rather than dissolving into a
    200 from the frontend fallback.

    Args:
        app: The Starlette/FastAPI app (``server.api``).
        method: HTTP method, e.g. ``"GET"``.
        path: A concrete request path.

    Raises:
        AssertionError: If no real route matches.
    """
    assert resolves_to_real_route(app, method, path), (
        f"{method} {path} matches no mounted API route - only the SPA catch-all "
        f"'{SPA_FALLBACK_PATH}' would answer it, with a 200. Any assertion against "
        "this path is vacuous."
    )


@pytest.fixture
def no_spa_fallback():
    """Fail any test whose ``TestClient`` receives a 2xx from the SPA catch-all.

    Wraps ``TestClient.request``, the single funnel every verb helper goes through
    (including the ``/api/v1`` path normalisation applied in ``conftest.py``), so
    the path inspected is the one actually sent over the wire.
    """
    original = TestClient.request

    def _guarded(self, method, url, *args, **kwargs):
        response = original(self, method, url, *args, **kwargs)
        if 200 <= response.status_code < 300:
            path = str(response.request.url.path)
            if not resolves_to_real_route(self.app, method, path):
                raise AssertionError(
                    f"VACUOUS ASSERTION: {method} {path} matched no API route and "
                    f"was answered by the SPA catch-all '{SPA_FALLBACK_PATH}' with "
                    f"status {response.status_code}. Anything asserted about this "
                    "response describes the frontend fallback, not the API. "
                    "Fix the URL."
                )
        return response

    TestClient.request = _guarded
    try:
        yield
    finally:
        TestClient.request = original
