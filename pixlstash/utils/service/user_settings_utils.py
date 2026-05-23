"""User settings serialisation and patching utilities."""

import json

from pixlstash.utils.service.system_utils import default_max_vram_gb, MAX_VRAM_BUDGET_GB  # noqa: F401


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
    from pixlstash.utils.quality.smart_score_utils import smart_score_penalised_tags
    from pixlstash.utils.service.caption_utils import normalize_hidden_tags

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
        "compact_mode",
        "date_format",
        "theme_mode",
        "comfyui_url",
        "public_url",
        "similarity_character",
        "stack_strictness",
        "apply_tag_filter",
        "keep_models_in_memory",
        "max_vram_gb",
        "check_for_updates",
        "show_keyboard_hint",
        "embed_watermark",
    }

    config = {
        key: (
            getattr(source, key)
            if getattr(source, key) is not None
            else getattr(default_user, key)
        )
        for key in allowed_fields
    }
    # check_for_updates is tri-state: serialise as-is, including None (undecided)
    config["check_for_updates"] = getattr(source, "check_for_updates", None)

    config["expand_all_stacks"] = (
        getattr(source, "show_stacks")
        if getattr(source, "show_stacks") is not None
        else getattr(default_user, "show_stacks")
    )

    allowed_sidebar_sizes = tuple(range(24, 65, 8))
    sidebar_size = _thumbnail_size(config.get("sidebar_thumbnail_size"))
    if sidebar_size is None:
        sidebar_size = default_user.sidebar_thumbnail_size
    if sidebar_size not in allowed_sidebar_sizes:
        sidebar_size = min(allowed_sidebar_sizes, key=lambda v: abs(v - sidebar_size))
    config["sidebar_thumbnail_size"] = sidebar_size

    config["smart_score_penalised_tags"] = smart_score_penalised_tags(
        getattr(source, "smart_score_penalised_tags", None),
        DEFAULT_SMART_SCORE_PENALIZED_TAGS,
        default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    )
    config["hidden_tags"] = normalize_hidden_tags(getattr(source, "hidden_tags", None))
    config["sort_order"] = config["sort"]
    if config.get("max_vram_gb") is None:
        config["max_vram_gb"] = default_max_vram_gb()

    # Include tagger_settings, filling in defaults for any missing entries.
    config["tagger_settings"] = _load_tagger_settings(source)

    return config


def _load_tagger_settings(user) -> dict:
    """Return the user's tagger_settings dict, filling defaults for missing entries."""
    from pixlstash.tagger_plugins.registry import get_tagger_plugin_manager

    manager = get_tagger_plugin_manager()
    raw = getattr(user, "tagger_settings", None)
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            parsed = {}
    else:
        parsed = {}
    return manager.fill_defaults(parsed)


def apply_user_config_patch(user, patch_data) -> bool:
    """Apply a dict of config changes to a User ORM object in-place.

    Returns:
        True if any field was changed, False otherwise.

    Raises:
        ValueError: If an unknown key is provided or a value fails validation.
    """
    from pixlstash.db_models import DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT
    from pixlstash.utils.quality.smart_score_utils import smart_score_penalised_tags
    from pixlstash.utils.service.caption_utils import normalize_hidden_tags

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
        "compact_mode",
        "expand_all_stacks",
        "show_stacks",
        "date_format",
        "theme_mode",
        "comfyui_url",
        "public_url",
        "similarity_character",
        "stack_strictness",
        "smart_score_penalised_tags",
        "hidden_tags",
        "apply_tag_filter",
        "keep_models_in_memory",
        "max_vram_gb",
        "check_for_updates",
        "show_keyboard_hint",
        "embed_watermark",
        "tagger_settings",
    }

    allowed_date_formats = {
        "locale",
        "iso",
        "eu",
        "us",
        "british",
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
        if key == "public_url":
            if value in ("", None, "null"):
                new_value = None
            else:
                new_value = str(value).strip().rstrip("/")
            if user.public_url != new_value:
                user.public_url = new_value
                updated = True
            continue
        if key == "smart_score_penalised_tags":
            if value in ("", None):
                new_value = None
            else:
                d = smart_score_penalised_tags(
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
                normalized = normalize_hidden_tags(value)
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
                if new_value > MAX_VRAM_BUDGET_GB:
                    raise ValueError(
                        f"max_vram_gb must not exceed {MAX_VRAM_BUDGET_GB} GB"
                    )
            if user.max_vram_gb != new_value:
                user.max_vram_gb = new_value
                updated = True
            continue
        if key == "check_for_updates":
            if value in ("", "null"):
                new_value = None
            elif value is None:
                new_value = None
            else:
                new_value = bool(value)
            if user.check_for_updates != new_value:
                user.check_for_updates = new_value
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
        if key == "tagger_settings":
            updated |= _apply_tagger_settings_patch(user, value)
            continue
        current_value = getattr(user, key, None)
        if current_value != value:
            setattr(user, key, value)
            updated = True
    return updated


def _apply_tagger_settings_patch(user, patch_value) -> bool:
    """Deep-merge a ``tagger_settings`` patch into the user's stored settings.

    Merges at the plugin level: existing plugin entries are updated with the
    patch's values, unknown plugin names in the patch are rejected with
    :class:`ValueError`.

    Args:
        user: User ORM object.
        patch_value: Partial or full tagger_settings dict (or JSON string).

    Returns:
        True if the stored value changed, False otherwise.

    Raises:
        ValueError: If the patch references an unknown plugin or contains an
            invalid ``active_description_plugin`` value.
    """
    from pixlstash.tagger_plugins.registry import get_tagger_plugin_manager

    if isinstance(patch_value, str):
        try:
            patch_value = json.loads(patch_value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"tagger_settings must be a JSON object: {exc}") from exc
    if not isinstance(patch_value, dict):
        raise ValueError("tagger_settings must be a JSON object")

    manager = get_tagger_plugin_manager()
    known_plugins = set(manager.plugin_names())

    # Load and fill current settings.
    current = _load_tagger_settings(user)

    # Validate and apply active_description_plugin.
    if "active_description_plugin" in patch_value:
        adp = patch_value["active_description_plugin"]
        if adp is not None and adp not in known_plugins:
            raise ValueError(f"active_description_plugin '{adp}' is not a known plugin")
        if adp is not None:
            plugin = manager.get_plugin(adp)
            if plugin is None or not plugin.supports_descriptions:
                raise ValueError(f"Plugin '{adp}' does not support descriptions")
        current["active_description_plugin"] = adp

    # Validate and deep-merge per-plugin entries.
    if "plugins" in patch_value:
        for plugin_name, plugin_patch in patch_value["plugins"].items():
            if plugin_name not in known_plugins:
                raise ValueError(f"Unknown plugin '{plugin_name}' in tagger_settings")
            if not isinstance(plugin_patch, dict):
                raise ValueError(
                    f"Plugin entry for '{plugin_name}' must be a JSON object"
                )
            current_plugin = current["plugins"].setdefault(plugin_name, {})
            # Merge top-level keys (enabled, params).
            if "enabled" in plugin_patch:
                current_plugin["enabled"] = bool(plugin_patch["enabled"])
            if "params" in plugin_patch:
                plugin_obj = manager.get_plugin(plugin_name)
                schema = {
                    f["name"]: f
                    for f in (plugin_obj.parameter_schema() if plugin_obj else [])
                }
                current_params = current_plugin.setdefault("params", {})
                for param_name, param_value in plugin_patch["params"].items():
                    if schema and param_name not in schema:
                        raise ValueError(
                            f"Unknown parameter '{param_name}' for plugin '{plugin_name}'"
                        )
                    current_params[param_name] = param_value

    new_json = json.dumps(current)
    if getattr(user, "tagger_settings", None) != new_json:
        user.tagger_settings = new_json
        return True
    return False
