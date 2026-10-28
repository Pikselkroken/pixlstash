"""What the Civitai by-hash lookup sends, and how it reads each answer (#1517).

``requests.get`` is replaced in every test, so nothing here reaches the network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests

from pixlstash.services import civitai_lookup
from pixlstash.services.civitai_lookup import LookupUnavailable, lookup_base_model

SHA = "ab" * 32


def _not_json():
    raise ValueError("Expecting value")


@pytest.fixture
def civitai(monkeypatch):
    """Answer every request with ``civitai.answer`` and record what was sent."""
    state = SimpleNamespace(calls=[], answer=SimpleNamespace(status_code=404))

    def get(url, **kwargs):
        state.calls.append((url, kwargs))
        if isinstance(state.answer, Exception):
            raise state.answer
        return state.answer

    monkeypatch.setattr(civitai_lookup.requests, "get", get)
    return state


@pytest.mark.parametrize(
    "bad",
    ["AB" * 32, "ab" * 31, "ab" * 33, "zz" * 32, "../" + "a" * 61, "", None],
)
def test_anything_but_a_lowercase_hex_sha256_is_refused_before_a_request(civitai, bad):
    with pytest.raises(ValueError):
        lookup_base_model(bad)
    assert civitai.calls == []


def test_only_the_hash_is_sent_to_the_by_hash_url_without_redirects(civitai):
    civitai.answer = SimpleNamespace(
        status_code=200, json=lambda: {"baseModel": "  SDXL 1.0 "}
    )

    assert lookup_base_model(SHA) == "SDXL 1.0"

    [(url, kwargs)] = civitai.calls
    assert url == f"https://civitai.com/api/v1/model-versions/by-hash/{SHA}"
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == civitai_lookup.TIMEOUT_SECONDS
    assert set(kwargs) == {"allow_redirects", "timeout", "headers"}, (
        "something beyond the hash went into the request"
    )
    assert "Authorization" not in kwargs["headers"]
    assert "Cookie" not in kwargs["headers"]


@pytest.mark.parametrize(
    "answer",
    [
        SimpleNamespace(status_code=404),
        SimpleNamespace(status_code=200, json=lambda: {"baseModel": "   "}),
        SimpleNamespace(status_code=200, json=lambda: {"name": "no base model"}),
        SimpleNamespace(status_code=200, json=lambda: {"baseModel": 7}),
        SimpleNamespace(status_code=200, json=lambda: {"baseModel": "x" * 101}),
        SimpleNamespace(status_code=200, json=lambda: ["not", "a", "dict"]),
    ],
)
def test_a_miss_is_an_answer(civitai, answer):
    civitai.answer = answer
    assert lookup_base_model(SHA) is None


@pytest.mark.parametrize(
    "answer",
    [
        SimpleNamespace(status_code=429),
        SimpleNamespace(status_code=500),
        SimpleNamespace(status_code=503),
        SimpleNamespace(status_code=301),
        SimpleNamespace(status_code=302),
        SimpleNamespace(status_code=200, json=_not_json),
        requests.Timeout("read timed out"),
        requests.ConnectionError("refused"),
    ],
)
def test_civitai_not_answering_raises_lookup_unavailable(civitai, answer):
    civitai.answer = answer
    with pytest.raises(LookupUnavailable):
        lookup_base_model(SHA)
