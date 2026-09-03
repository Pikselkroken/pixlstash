"""The SPA document must carry the install type the backend detected.

The frontend cannot ask for it in time. Its version check runs from a child
component's ``onMounted`` -- Vue mounts children before parents -- so it fires
before ``App.vue`` has even started ``GET /version``, and
``checkForUpdatesNow`` stamps its 24h throttle before the request. Whatever the
document says at load is therefore what gets reported for the day. Assuming
"pip" filed every restarted Docker and desktop install under ``pip``; see
``pixlstash-metrics/CLAUDE.md`` for the bucket swap that exposed it.
"""

from __future__ import annotations

import pytest

from pixlstash.server import Server


class _Stub:
    """Minimal stand-in: ``_index_html_response`` only needs these two."""

    def __init__(self, index_path):
        self._index_path = index_path
        self._index_html_cache = None

    def _get_frontend_index_path(self):
        return self._index_path


def _write_index(tmp_path):
    index = tmp_path / "index.html"
    index.write_text(
        '<html><head><meta name="pixlstash-install-type" '
        f'content="{Server.INSTALL_TYPE_PLACEHOLDER}" /></head></html>',
        encoding="utf-8",
    )
    return index


@pytest.mark.parametrize("install_type", Server.INSTALL_TYPES)
def test_placeholder_is_replaced_with_every_declared_bucket(
    tmp_path, monkeypatch, install_type
):
    monkeypatch.setattr(Server, "detect_install_type", staticmethod(lambda: install_type))
    body = Server._index_html_response(_Stub(str(_write_index(tmp_path)))).body.decode()

    assert f'content="{install_type}"' in body
    assert Server.INSTALL_TYPE_PLACEHOLDER not in body


def test_no_built_frontend_returns_none(tmp_path):
    assert Server._index_html_response(_Stub(None)) is None


def test_cache_follows_a_changed_install_type(tmp_path, monkeypatch):
    """The cache keys on the install type, so a change is not served stale."""
    stub = _Stub(str(_write_index(tmp_path)))

    monkeypatch.setattr(Server, "detect_install_type", staticmethod(lambda: "docker"))
    assert 'content="docker"' in Server._index_html_response(stub).body.decode()

    monkeypatch.setattr(Server, "detect_install_type", staticmethod(lambda: "electron"))
    assert 'content="electron"' in Server._index_html_response(stub).body.decode()
