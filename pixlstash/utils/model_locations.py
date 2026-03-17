import os

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Files that must exist in the directory when download=false
# ---------------------------------------------------------------------------

# Models where we can enumerate exact required filenames
_REQUIRED_FILES: dict[str, list[str]] = {
    "wd14_tagger": ["model.onnx", "selected_tags.csv"],
    "custom_tagger": [
        "pixlstash-anomaly-tagger.safetensors",
        "pixlstash-anomaly-tagger_meta.json",
    ],
    # HuggingFace models: presence of config.json is the canonical marker
    "florence_captioner": ["config.json"],
    "sentence_transformer": ["config.json"],
    # open_clip HuggingFace export filename
    "clip": ["open_clip_pytorch_model.bin"],
}

# Per-CLIP-model filename for the aesthetic predictor .pth weight file
_AESTHETIC_FILENAME_BY_CLIP: dict[str, str] = {
    "ViT-B-32": "sa_0_4_vit_b_32_linear.pth",
    "ViT-L-14": "sac+logos+ava1-l14-linearMSE.pth",
}

class ModelLocations:
    """Parsed and validated model location configuration.

    Args:
        config: The value of ``server_config["model_locations"]``.  May be
            ``None`` or an empty dict — both mean "all auto".

    Raises:
        ValueError: If the config structure is malformed (wrong types, relative
            path when absolute is required, or ``download: false`` combined
            with ``path: "auto"``).
    """

    def __init__(self, config: dict | None) -> None:
        raw = config or {}
        if not isinstance(raw, dict):
            raise ValueError(
                "model_locations must be a JSON object (dict), "
                f"got {type(raw).__name__}."
            )
        self._entries: dict[str, dict] = {}
        for key, value in raw.items():
            self._entries[key] = self._parse_entry(key, value)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_auto(self, key: str) -> bool:
        """Return True if the model uses automatic path resolution."""
        entry = self._entries.get(key)
        return entry is None or entry["path"] == "auto"

    def path(self, key: str) -> str | None:
        """Return the configured directory path, or None when auto."""
        entry = self._entries.get(key)
        if entry is None or entry["path"] == "auto":
            return None
        return entry["path"]

    def download_enabled(self, key: str) -> bool:
        """Return whether automatic download is enabled for this model.

        Always returns True for auto-path models (they download by default).
        For explicit-path models returns the configured ``download`` value.
        """
        entry = self._entries.get(key)
        if entry is None or entry["path"] == "auto":
            return True
        return entry["download"]

    def validation_errors(self) -> list[str]:
        """Return hard-failure messages for misconfigured model locations.

        Checks:
        - Path must be absolute.
        - When ``download=false``: directory must exist, required files must
          be present.
        - When ``download=true``: if the path exists it must be a directory
          (not a file).
        """
        errors: list[str] = []
        for key, entry in self._entries.items():
            path = entry["path"]
            download = entry["download"]
            if path == "auto":
                continue

            # --- path must be absolute ---
            if not os.path.isabs(path):
                errors.append(
                    f"model_locations.{key}: path '{path}' must be an absolute path."
                )
                continue

            if download:
                # The directory will be created at runtime; just make sure it
                # is not pointing at an existing non-directory (e.g. a file).
                if os.path.exists(path) and not os.path.isdir(path):
                    errors.append(
                        f"model_locations.{key}: path '{path}' exists but is not a "
                        "directory."
                    )
            else:
                # download=false: the model must already be there
                if not os.path.isdir(path):
                    errors.append(
                        f"model_locations.{key}: path '{path}' does not exist or is "
                        "not a directory (download is disabled for this model)."
                    )
                    continue

                if key == "face_detector":
                    # InsightFace uses <root>/models/ sub-layout
                    models_dir = os.path.join(path, "models")
                    if not os.path.isdir(models_dir):
                        errors.append(
                            f"model_locations.{key}: path '{path}' must contain a "
                            "'models' subdirectory with InsightFace model packs "
                            "(e.g. models/buffalo_l/). "
                            "download is disabled for this model."
                        )
                    continue

                if key == "aesthetic_predictor":
                    errors.extend(self._check_aesthetic_predictor(path))
                    continue

                required = _REQUIRED_FILES.get(key, [])
                if required:
                    for fname in required:
                        if not os.path.isfile(os.path.join(path, fname)):
                            errors.append(
                                f"model_locations.{key}: required file '{fname}' not "
                                f"found in '{path}' (download is disabled for this model)."
                            )
                else:
                    # No specific file list: just require a non-empty directory
                    try:
                        if not any(os.scandir(path)):
                            errors.append(
                                f"model_locations.{key}: path '{path}' is empty "
                                "(download is disabled for this model)."
                            )
                    except OSError as exc:
                        errors.append(
                            f"model_locations.{key}: cannot read path '{path}': {exc}"
                        )

        return errors

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_entry(key: str, raw: object) -> dict:
        if not isinstance(raw, dict):
            raise ValueError(
                f"model_locations.{key}: entry must be a JSON object with a "
                '"path" field and optionally a "download" field, '
                f"got {type(raw).__name__}."
            )

        path = raw.get("path", "auto")
        if not isinstance(path, str) or not path.strip():
            raise ValueError(
                f'model_locations.{key}: "path" must be a non-empty string '
                '(use "auto" for automatic resolution).'
            )
        path = path.strip()

        if "download" in raw:
            download = raw["download"]
            if not isinstance(download, bool):
                raise ValueError(
                    f'model_locations.{key}: "download" must be a boolean '
                    f"(true or false), got {type(download).__name__}."
                )
            if path == "auto" and not download:
                raise ValueError(
                    f'model_locations.{key}: "download": false has no effect '
                    'when path is "auto".'
                )
        else:
            # Default: download=true for custom paths, irrelevant for auto
            download = True

        return {"path": path, "download": download}

    @staticmethod
    def _check_aesthetic_predictor(path: str) -> list[str]:
        """Determine the expected filename from the active CLIP model and check."""
        try:
            from pixlstash.picture_tagger import CLIP_MODEL_NAME  # noqa: PLC0415
        except Exception:
            CLIP_MODEL_NAME = "ViT-B-32"

        fname = _AESTHETIC_FILENAME_BY_CLIP.get(CLIP_MODEL_NAME)
        if fname is None:
            # Unknown CLIP model — skip file-level check, just require non-empty dir
            try:
                if not any(os.scandir(path)):
                    return [
                        f"model_locations.aesthetic_predictor: path '{path}' is empty "
                        "(download is disabled for this model)."
                    ]
            except OSError:
                logger.warning(
                    f"model_locations.aesthetic_predictor: cannot read path '{path}' "
                    "to check for emptiness."
                )
            return []

        if not os.path.isfile(os.path.join(path, fname)):
            return [
                f"model_locations.aesthetic_predictor: required file '{fname}' not "
                f"found in '{path}' (download is disabled for this model). "
                f"Expected filename is determined by the active CLIP model "
                f"({CLIP_MODEL_NAME})."
            ]
        return []
