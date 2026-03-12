"""User configuration serialisation and patching utilities."""

import json

from pixlstash.utils.service.system_utils import default_max_vram_gb  # noqa: F401


def _thumbnail_size(value):
    """Parse a raw thumbnail size value into an int, or return None."""
    if value is None:
        return None
    if isinstance(value, str):
        if value.lower() == "default":
            return None
        if value.isdigit():
            return int(value)
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def serialize_user_config(user) -> dict:
    """Serialise a User ORM object (or None) into a JSON-safe config dict."""
    from pixlstash.db_models import (
        User,
        DEFAULT_SMART_SCORE_PENALIZED_TAGS,
        DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    )
    from pixlstash.utils.quality.smart_score_utils import _smart_score_penalised_tags
    from pixlstash.utils.service.caption_utils import _normalize_hidden_tags

    default_user = User()
    source = user or default_user

    allowed_fields = {
        "description",
        "sort",
        "descending",
        "columns",
        "sidebar_thumbnail_size",
        "show_stars",
        "show_face_bboxes",
        "show_hand_bboxes",
        "show_format",
        "show_resolution",
        "show_problem_icon",
        "date_format",
        "theme_mode",
        "comfyui_url",
        "similarity_character",
        "stack_strictness",
        "apply_tag_filter",
        "keep_models_in_memory",
        "max_vram_gb",
    }

    config = {
        key: (
            getattr(source, key)
            if getattr(source, key) is not None
            else getattr(default_user, key)
        )
        for key in allowed_fields
    }
    config["expand_all_stacks"] = (
        getattr(source, "show_stacks")
        if getattr(source, "show_stacks") is not None
        else getattr(default_user, "show_stacks")
    )

    allowed_sidebar_sizes = tuple(range(32, 65, 8))
    sidebar_size = _thumbnail_size(config.get("sidebar_thumbnail_size"))
    if sidebar_size is None:
        sidebar_size = default_user.sidebar_thumbnail_size
    if sidebar_size not in allowed_sidebar_sizes:
        sidebar_size = min(allowed_sidebar_sizes, key=lambda v: abs(v - sidebar_size))
    config["sidebar_thumbnail_size"] = sidebar_size

    config["smart_score_penalised_tags"] = _smart_score_penalised_tags(
        getattr(source, "smart_score_penalised_tags", None),
        DEFAULT_SMART_SCORE_PENALIZED_TAGS,
        default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    )
    config["hidden_tags"] = _normalize_hidden_tags(getattr(source, "hidden_tags", None))
    config["sort_order"] = config["sort"]
    if config.get("max_vram_gb") is None:
        config["max_vram_gb"] = default_max_vram_gb()
    return config


def apply_user_config_patch(user, patch_data) -> bool:
    """Apply a dict of config changes to a User ORM object in-place.

    Returns:
        True if any field was changed, False otherwise.

    Raises:
        ValueError: If an unknown key is provided or a value fails validation.
    """
    from pixlstash.db_models import DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT
    from pixlstash.utils.quality.smart_score_utils import _smart_score_penalised_tags
    from pixlstash.utils.service.caption_utils import _normalize_hidden_tags

    allowed_fields = {
        "description",
        "sort",
        "descending",
        "columns",
        "sidebar_thumbnail_size",
        "show_stars",
        "show_face_bboxes",
        "show_hand_bboxes",
        "show_format",
        "show_resolution",
        "show_problem_icon",
        "expand_all_stacks",
        "show_stacks",
        "date_format",
        "theme_mode",
        "comfyui_url",
        "similarity_character",
        "stack_strictness",
        "smart_score_penalised_tags",
        "hidden_tags",
        "apply_tag_filter",
        "keep_models_in_memory",
        "max_vram_gb",
    }

    allowed_date_formats = {
        "locale",
        "iso",
        "eu",
        "us",
        "ymd-slash",
        "ymd-dot",
        "ymd-jp",
    }
    allowed_theme_modes = {"light", "dark"}

    updated = False
    for key, value in patch_data.items():
        if key not in allowed_fields:
            raise ValueError(f"Key '{key}' does not exist in config.")
        if key in {"expand_all_stacks", "show_stacks"}:
            new_value = bool(value)
            if user.show_stacks != new_value:
                user.show_stacks = new_value
                updated = True
            continue
        if key == "similarity_character":
            if value in ("", None, "null"):
                new_value = None
            elif isinstance(value, str) and value.isdigit():
                new_value = int(value)
            else:
                new_value = value
            if user.similarity_character != new_value:
                user.similarity_character = new_value
                updated = True
            continue
        if key == "comfyui_url":
            if value in ("", None, "null"):
                new_value = None
            else:
                new_value = str(value).strip()
            if user.comfyui_url != new_value:
                user.comfyui_url = new_value
                updated = True
            continue
        if key == "smart_score_penalised_tags":
            if value in ("", None):
                new_value = None
            else:
                d = _smart_score_penalised_tags(
                    value,
                    None,
                    allow_empty=True,
                    default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
                )
                if d is None:
                    raise ValueError(
                        "smart_score_penalised_tags must be a JSON list or object"
                    )
                new_value = json.dumps(d)
            if user.smart_score_penalised_tags != new_value:
                user.smart_score_penalised_tags = new_value
                updated = True
            continue
        if key == "hidden_tags":
            if value in ("", None, "null"):
                normalized = []
            else:
                normalized = _normalize_hidden_tags(value)
                if normalized is None:
                    raise ValueError("hidden_tags must be a JSON list of strings")
            new_value = json.dumps(normalized)
            if user.hidden_tags != new_value:
                user.hidden_tags = new_value
                updated = True
            continue
        if key == "apply_tag_filter":
            if value in ("", None, "null"):
                new_value = False
            else:
                new_value = bool(value)
            if user.apply_tag_filter != new_value:
                user.apply_tag_filter = new_value
                updated = True
            continue
        if key == "keep_models_in_memory":
            if value in ("", None, "null"):
                new_value = True
            else:
                new_value = bool(value)
            if user.keep_models_in_memory != new_value:
                user.keep_models_in_memory = new_value
                updated = True
            continue
        if key == "max_vram_gb":
            if value in ("", None, "null"):
                new_value = None
            else:
                new_value = float(value)
                if new_value <= 0:
                    raise ValueError("max_vram_gb must be greater than 0")
            if user.max_vram_gb != new_value:
                user.max_vram_gb = new_value
                updated = True
            continue
        if key == "date_format":
            if value in ("", None, "null"):
                new_value = None
            else:
                new_value = str(value)
            if new_value is not None and new_value not in allowed_date_formats:
                raise ValueError("date_format is not a supported value")
            if user.date_format != new_value:
                user.date_format = new_value
                updated = True
            continue
        if key == "theme_mode":
            if value in ("", None, "null"):
                new_value = None
            else:
                new_value = str(value)
            if new_value is not None and new_value not in allowed_theme_modes:
                raise ValueError("theme_mode is not a supported value")
            if user.theme_mode != new_value:
                user.theme_mode = new_value
                updated = True
            continue
        if key == "stack_strictness":
            if value in ("", None, "null"):
                new_value = None
            else:
                new_value = float(value)
            if user.stack_strictness != new_value:
                user.stack_strictness = new_value
                updated = True
            continue
        if key == "columns":
            new_value = int(value)
            if user.columns != new_value:
                user.columns = new_value
                updated = True
            continue
        if key == "sidebar_thumbnail_size":
            new_value = _thumbnail_size(value)
            if new_value is None:
                continue
            allowed_sizes = tuple(range(32, 65, 8))
            if new_value not in allowed_sizes:
                new_value = min(allowed_sizes, key=lambda v: abs(v - new_value))
            if user.sidebar_thumbnail_size != new_value:
                user.sidebar_thumbnail_size = new_value
                updated = True
            continue
        current_value = getattr(user, key, None)
        if current_value != value:
            setattr(user, key, value)
            updated = True
    return updated
