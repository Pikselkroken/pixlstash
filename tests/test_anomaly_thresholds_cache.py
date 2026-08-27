"""Tests for the per-vault memo on ``resolve_anomaly_apply_thresholds`` (S5).

The resolver's output is fully determined by ``(meta_path, offset, default_threshold)``,
so a memo keyed on that tuple auto-invalidates when the offset moves or the tagger model
(hence its meta.json path) changes - without an explicit invalidation hook. Only the file
read (``load_label_thresholds``) is skipped on a hit; the three cheap getters still run to
build the key. The memo lives on the *vault instance*, never a module global, so separate
vaults (and throwaway test vaults) must never share a cached map.
"""

import json

import pytest

from pixlstash.utils.quality.anomaly_penalty import ANOMALY_PENALTY_TAGS
from pixlstash.utils.service import anomaly_thresholds
from pixlstash.utils.service.anomaly_thresholds import resolve_anomaly_apply_thresholds

# A real anomaly tag so the resolved map is affected by the offset bias.
_TAG = sorted(ANOMALY_PENALTY_TAGS)[0]


class _FakeVault:
    """Minimal stand-in exposing only the three getters the resolver calls.

    A plain object (no ``__slots__``) so the resolver can attach its memo attribute,
    exactly as it does on a real :class:`~pixlstash.vault.Vault`.
    """

    def __init__(self, meta_path, offset, default_threshold):
        self._meta_path = meta_path
        self._offset = offset
        self._default = default_threshold

    def get_pixlstash_tagger_meta_path(self):
        return self._meta_path

    def get_pixlstash_tagger_threshold_offset(self):
        return self._offset

    def get_pixlstash_acceptance_threshold(self):
        return self._default


@pytest.fixture
def meta_file(tmp_path):
    """Write a tagger meta.json with a calibrated per-label threshold and return its path."""
    path = tmp_path / "meta.json"
    path.write_text(json.dumps({"label_thresholds": {_TAG: 0.6}}))
    return str(path)


@pytest.fixture
def spy_load(monkeypatch):
    """Count calls to ``load_label_thresholds`` (the only file-reading step)."""
    calls = {"n": 0}
    real = anomaly_thresholds.load_label_thresholds

    def _counting(meta_path, bias=0.0):
        calls["n"] += 1
        return real(meta_path, bias)

    monkeypatch.setattr(anomaly_thresholds, "load_label_thresholds", _counting)
    return calls


def test_repeated_calls_read_meta_file_once(meta_file, spy_load):
    """Two calls with unchanged inputs read the meta file exactly once."""
    vault = _FakeVault(meta_file, 0.0, 0.6)

    first = resolve_anomaly_apply_thresholds(vault)
    second = resolve_anomaly_apply_thresholds(vault)

    assert spy_load["n"] == 1
    assert first == second
    # The map is complete over the anomaly vocabulary and reflects the calibrated tag.
    assert set(first) == set(ANOMALY_PENALTY_TAGS)
    assert first[_TAG] == pytest.approx(0.6)


def test_offset_change_invalidates_and_re_reads(meta_file, spy_load):
    """Changing the offset is a cache miss → the file is re-read and the map shifts."""
    vault = _FakeVault(meta_file, 0.0, 0.6)

    first = resolve_anomaly_apply_thresholds(vault)
    assert spy_load["n"] == 1

    vault._offset = 0.1
    second = resolve_anomaly_apply_thresholds(vault)

    assert spy_load["n"] == 2
    assert first != second
    # The per-label threshold moved by the offset bias.
    assert second[_TAG] == pytest.approx(0.7)


def test_meta_path_change_invalidates(meta_file, tmp_path, spy_load):
    """A new meta path (e.g. a swapped tagger model) is a cache miss."""
    vault = _FakeVault(meta_file, 0.0, 0.6)
    resolve_anomaly_apply_thresholds(vault)
    assert spy_load["n"] == 1

    other = tmp_path / "meta2.json"
    other.write_text(json.dumps({"label_thresholds": {_TAG: 0.42}}))
    vault._meta_path = str(other)
    resolved = resolve_anomaly_apply_thresholds(vault)

    assert spy_load["n"] == 2
    assert resolved[_TAG] == pytest.approx(0.42)


def test_two_vaults_do_not_share_cache(meta_file, spy_load):
    """Separate vault instances keep separate memos - no module-global cross-talk."""
    vault_a = _FakeVault(meta_file, 0.0, 0.6)
    vault_b = _FakeVault(meta_file, 0.0, 0.6)

    resolve_anomaly_apply_thresholds(vault_a)  # miss → read #1
    resolve_anomaly_apply_thresholds(vault_a)  # hit
    assert spy_load["n"] == 1

    resolve_anomaly_apply_thresholds(vault_b)  # separate instance → miss → read #2
    assert spy_load["n"] == 2
