"""Tests for the model_locations configuration feature.

Covered scenarios
-----------------
1. Auto path
   No model_locations config; PictureTagger resolves WD14 and custom-tagger
   paths to MODEL_DIR.  Mocked hf_hub_download creates stub files there; no
   custom-path directories are created.

2. Custom path
   model_locations specifies an explicit directory.  Custom paths always
   require pre-populated model files; automatic download is never performed.
   (a) Empty custom path → PictureTagger.__init__ raises RuntimeError; no
       hf_hub_download call is made.  ModelLocations.validation_errors() also
       reports hard failures so the server would refuse to start.
   (b) Files pre-seeded in the custom path: init succeeds, zero network
       calls.  validation_errors() returns no errors.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import pixlstash.picture_tagger as pt_module
from pixlstash.picture_tagger import DEFAULT_WD14_TAGGER_REPO, MODEL_DIR, PictureTagger
from pixlstash.startup_checks import StartupCheckOutcome, StartupChecks
from pixlstash.utils.model_locations import ModelLocations

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WD14_FILES = ["model.onnx", "selected_tags.csv"]
_CT_FILENAME = "pixlstash-anomaly-tagger.safetensors"
_CT_META_FILENAME = "pixlstash-anomaly-tagger_meta.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wd14_auto_dir() -> str:
    """The WD14 directory that PictureTagger uses when model_locations is None."""
    return os.path.join(MODEL_DIR, DEFAULT_WD14_TAGGER_REPO.replace("/", "_"))


def _stub_hf_download(*, repo_id, filename, local_dir, force_download=False, **kwargs):
    """Side-effect for mocked hf_hub_download: touches a stub file at the
    target location so all file-presence checks pass without real I/O."""
    dest = os.path.join(local_dir, filename)
    os.makedirs(local_dir, exist_ok=True)
    Path(dest).touch()
    return dest


def _seed_wd14_files(wd14_dir: str) -> None:
    os.makedirs(wd14_dir, exist_ok=True)
    for fname in _WD14_FILES:
        Path(os.path.join(wd14_dir, fname)).touch()


def _seed_custom_tagger_files(ct_path: str, ct_meta_path: str) -> None:
    dest_dir = os.path.dirname(os.path.abspath(ct_path))
    os.makedirs(dest_dir, exist_ok=True)
    Path(ct_path).touch()
    Path(ct_meta_path).touch()


# ---------------------------------------------------------------------------
# Unit tests for ModelLocations – pure configuration-class logic
# ---------------------------------------------------------------------------


class TestModelLocationsConfig:
    def test_no_config_means_all_auto(self):
        ml = ModelLocations(None)
        for key in ("wd14_tagger", "custom_tagger", "florence_captioner", "clip", "sentence_transformer"):
            assert ml.is_auto(key)
            assert ml.path(key) is None

    def test_download_key_raises(self):
        with pytest.raises(ValueError, match="no longer supported"):
            ModelLocations({"wd14_tagger": {"path": "auto", "download": False}})

    def test_custom_path_stored(self, tmp_path):
        ml = ModelLocations({"wd14_tagger": {"path": str(tmp_path)}})
        assert not ml.is_auto("wd14_tagger")
        assert ml.path("wd14_tagger") == str(tmp_path)

    def test_relative_path_fails_validation(self):
        ml = ModelLocations({"wd14_tagger": {"path": "relative/path"}})
        assert any("absolute" in e for e in ml.validation_errors())

    def test_missing_dir_fails_validation(self, tmp_path):
        missing = str(tmp_path / "nonexistent")
        ml = ModelLocations({"wd14_tagger": {"path": missing}})
        assert any("does not exist" in e for e in ml.validation_errors())

    def test_missing_files_fail_validation(self, tmp_path):
        wd14_dir = tmp_path / "wd14"
        wd14_dir.mkdir()
        ml = ModelLocations({"wd14_tagger": {"path": str(wd14_dir)}})
        errors = ml.validation_errors()
        assert any("model.onnx" in e for e in errors)
        assert any("selected_tags.csv" in e for e in errors)

    def test_all_files_present_no_errors(self, tmp_path):
        wd14_dir = tmp_path / "wd14"
        _seed_wd14_files(str(wd14_dir))
        ml = ModelLocations({"wd14_tagger": {"path": str(wd14_dir)}})
        assert ml.validation_errors() == []


# ---------------------------------------------------------------------------
# Scenario 1 – auto path
# ---------------------------------------------------------------------------


class TestScenario1AutoPath:
    """No model_locations config → PictureTagger resolves to MODEL_DIR paths."""

    def test_model_location_attribute_uses_model_dir(self, tmp_path):
        """_model_location must point to the standard MODEL_DIR sub-directory."""
        # Seed auto-path WD14 files so no download is triggered.
        auto_wd14_dir = _wd14_auto_dir()
        _seed_wd14_files(auto_wd14_dir)

        # Redirect custom-tagger paths to tmp to avoid touching MODEL_DIR for them.
        ct_path = str(tmp_path / "ct" / _CT_FILENAME)
        ct_meta = str(tmp_path / "ct" / _CT_META_FILENAME)
        _seed_custom_tagger_files(ct_path, ct_meta)

        with (
            patch.object(pt_module, "CUSTOM_TAGGER_PATH", ct_path),
            patch.object(pt_module, "CUSTOM_TAGGER_META_PATH", ct_meta),
        ):
            tagger = PictureTagger(model_locations=None)

        assert tagger._model_location == auto_wd14_dir

    def test_download_targets_model_dir_when_files_absent(self, tmp_path):
        """When WD14 files are missing, hf_hub_download is called under MODEL_DIR."""
        import shutil

        auto_wd14_dir = _wd14_auto_dir()
        # Remove existing WD14 stubs so a download is triggered.
        if os.path.isdir(auto_wd14_dir):
            shutil.rmtree(auto_wd14_dir)

        ct_path = str(tmp_path / "ct" / _CT_FILENAME)
        ct_meta = str(tmp_path / "ct" / _CT_META_FILENAME)
        _seed_custom_tagger_files(ct_path, ct_meta)

        mock_dl = MagicMock(side_effect=_stub_hf_download)
        with (
            patch("huggingface_hub.hf_hub_download", mock_dl),
            patch.object(pt_module, "CUSTOM_TAGGER_PATH", ct_path),
            patch.object(pt_module, "CUSTOM_TAGGER_META_PATH", ct_meta),
        ):
            PictureTagger(model_locations=None)

        # Every hf_hub_download call must target a sub-path of MODEL_DIR.
        assert mock_dl.call_count > 0, "Expected at least one hf_hub_download call"
        for c in mock_dl.call_args_list:
            local_dir = c.kwargs.get("local_dir", "")
            assert local_dir.startswith(MODEL_DIR), (
                f"Expected download to MODEL_DIR, got local_dir={local_dir!r}"
            )


# ---------------------------------------------------------------------------
# Scenario 2 – custom path
# ---------------------------------------------------------------------------


class TestScenarioCustomPath:
    """Custom path: fail when files missing, succeed when pre-populated."""

    # -- 2a: files NOT present --

    def test_2a_tagger_raises_when_wd14_files_missing(self, tmp_path):
        """PictureTagger.__init__ must raise RuntimeError; hf_hub_download not called."""
        custom_wd14_dir = str(tmp_path / "wd14_empty")
        os.makedirs(custom_wd14_dir)

        model_locations = ModelLocations(
            {"wd14_tagger": {"path": custom_wd14_dir}}
        )

        ct_path = str(tmp_path / "ct" / _CT_FILENAME)
        ct_meta = str(tmp_path / "ct" / _CT_META_FILENAME)
        _seed_custom_tagger_files(ct_path, ct_meta)

        mock_dl = MagicMock()
        with (
            patch("huggingface_hub.hf_hub_download", mock_dl),
            patch.object(pt_module, "CUSTOM_TAGGER_PATH", ct_path),
            patch.object(pt_module, "CUSTOM_TAGGER_META_PATH", ct_meta),
            pytest.raises(RuntimeError, match="Custom paths require"),
        ):
            PictureTagger(model_locations=model_locations)

        mock_dl.assert_not_called()

    def test_2a_validation_errors_reported_for_missing_files(self, tmp_path):
        """ModelLocations.validation_errors() lists the missing WD14 file errors,
        which StartupChecks converts to hard failures causing server init to fail."""
        custom_wd14_dir = str(tmp_path / "wd14_empty")
        os.makedirs(custom_wd14_dir)

        ml = ModelLocations({"wd14_tagger": {"path": custom_wd14_dir}})
        errors = ml.validation_errors()

        assert len(errors) > 0, "Expected hard-failure errors for missing files"
        assert any("wd14_tagger" in e for e in errors)

    def test_2a_startup_check_produces_hard_failure(self, tmp_path):
        """_check_model_locations adds a hard failure when required files are
        absent, which would block server initialisation."""
        custom_wd14_dir = str(tmp_path / "wd14_empty")
        os.makedirs(custom_wd14_dir)

        outcome = StartupCheckOutcome()
        checker = StartupChecks(
            server_config={
                "model_locations": {
                    "wd14_tagger": {"path": custom_wd14_dir}
                }
            },
            server_config_path=str(tmp_path / "cfg.json"),
            logger=_NoopLogger(),
        )
        checker._check_model_locations(outcome)  # noqa: SLF001

        assert len(outcome.hard_failures) > 0
        assert any("wd14_tagger" in f for f in outcome.hard_failures)

    # -- 2b: files present --

    def test_2b_tagger_succeeds_when_files_present(self, tmp_path):
        """Pre-seeded files → init succeeds; hf_hub_download must not be called."""
        custom_wd14_dir = str(tmp_path / "wd14_populated")
        _seed_wd14_files(custom_wd14_dir)

        model_locations = ModelLocations(
            {"wd14_tagger": {"path": custom_wd14_dir}}
        )

        ct_path = str(tmp_path / "ct" / _CT_FILENAME)
        ct_meta = str(tmp_path / "ct" / _CT_META_FILENAME)
        _seed_custom_tagger_files(ct_path, ct_meta)

        mock_dl = MagicMock()
        with (
            patch("huggingface_hub.hf_hub_download", mock_dl),
            patch.object(pt_module, "CUSTOM_TAGGER_PATH", ct_path),
            patch.object(pt_module, "CUSTOM_TAGGER_META_PATH", ct_meta),
        ):
            tagger = PictureTagger(model_locations=model_locations)

        assert tagger._model_location == custom_wd14_dir
        mock_dl.assert_not_called()

    def test_2b_validation_errors_empty_when_files_present(self, tmp_path):
        """ModelLocations.validation_errors() returns [] when all required files exist;
        StartupChecks would produce no hard failures so the server can start."""
        custom_wd14_dir = str(tmp_path / "wd14_populated")
        _seed_wd14_files(custom_wd14_dir)

        ml = ModelLocations({"wd14_tagger": {"path": custom_wd14_dir}})
        assert ml.validation_errors() == []

    def test_2b_startup_check_produces_no_failure_when_files_present(self, tmp_path):
        """_check_model_locations produces zero hard failures when required files exist."""
        custom_wd14_dir = str(tmp_path / "wd14_populated")
        _seed_wd14_files(custom_wd14_dir)

        outcome = StartupCheckOutcome()
        checker = StartupChecks(
            server_config={
                "model_locations": {
                    "wd14_tagger": {"path": custom_wd14_dir}
                }
            },
            server_config_path=str(tmp_path / "cfg.json"),
            logger=_NoopLogger(),
        )
        checker._check_model_locations(outcome)  # noqa: SLF001

        assert outcome.hard_failures == []


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


class _NoopLogger:
    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass
