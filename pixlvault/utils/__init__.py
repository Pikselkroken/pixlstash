import base64
import json
import subprocess
from datetime import date, datetime
from sqlmodel import SQLModel

# Add import for SQLAlchemy CollectionAdapter
try:
    from sqlalchemy.orm.collections import CollectionAdapter
except ImportError:
    CollectionAdapter = None


def safe_model_dict(obj) -> dict:
    """
    Recursively create a safe, serializable dict from any SQLModel instance, dict, or SQLAlchemy adapter.
    - Encodes bytes fields as base64.
    - Parses JSON/text fields ending with '_'.
    - Recurses into SQLModel relationships, lists, dicts, and adapters.
    """
    if CollectionAdapter and isinstance(obj, CollectionAdapter):
        # Convert SQLAlchemy adapter to list
        return [safe_model_dict(v) for v in list(obj)]
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            result[k] = safe_model_dict(v)
        return result
    if isinstance(obj, list):
        return [safe_model_dict(v) for v in obj]
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    if isinstance(obj, (datetime, date)):
        return obj
    result = {}
    for field, value in obj.__dict__.items():
        if field.startswith("_sa"):
            continue
        if isinstance(value, bytes):
            result[field] = base64.b64encode(value).decode("utf-8")
        elif field.endswith("_") and isinstance(value, str):
            try:
                result[field[:-1]] = json.loads(value)
            except Exception:
                result[field[:-1]] = value
        elif CollectionAdapter and isinstance(value, CollectionAdapter):
            result[field] = [safe_model_dict(v) for v in list(value)]
        elif isinstance(value, SQLModel):
            result[field] = safe_model_dict(value)
        elif isinstance(value, list):
            result[field] = [safe_model_dict(v) for v in value]
        elif isinstance(value, dict):
            result[field] = safe_model_dict(value)
        else:
            result[field] = value
    return result


def default_max_vram_gb() -> float:
    """Return default VRAM budget in GB: min(4GB, 25% of available VRAM).

    Falls back to 4GB when VRAM cannot be detected.
    """
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        totals_mb = []
        for line in output.splitlines():
            value = line.strip()
            if not value:
                continue
            totals_mb.append(int(float(value)))
        total_mb = sum(totals_mb)
        if total_mb <= 0:
            return 4.0
        quarter_gb = (total_mb / 1024.0) / 4.0
        return round(min(4.0, quarter_gb), 2)
    except Exception:
        return 4.0


def serialize_user_config(user) -> dict:
    from pixlvault.db_models import (
        User,
        DEFAULT_SMART_SCORE_PENALIZED_TAGS,
        DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    )

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
    from pixlvault.db_models import DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT

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


def serialize_tag_objects(tags: list | None, empty_sentinel: str = "") -> list[dict]:
    items = []
    for tag in tags or []:
        if not tag or getattr(tag, "tag", None) in (None, empty_sentinel):
            continue
        items.append({"id": getattr(tag, "id", None), "tag": tag.tag})
    return items


def _thumbnail_size(value):
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


def _smart_score_penalised_tags(
    value,
    fallback=None,
    allow_empty: bool = False,
    default_weight: int = 3,
):
    if value is None:
        return fallback

    tags = None
    if isinstance(value, str):
        try:
            tags = json.loads(value)
        except Exception:
            return fallback
    else:
        tags = value

    if isinstance(tags, list):
        d = {}
        for tag in tags:
            if tag is None:
                continue
            clean = str(tag).strip().lower()
            if not clean:
                continue
            d[clean] = default_weight
    elif isinstance(tags, dict):
        d = {}
        for tag, weight in tags.items():
            if tag is None:
                continue
            clean = str(tag).strip().lower()
            if not clean:
                continue
            try:
                weight_value = int(float(weight))
            except (TypeError, ValueError):
                weight_value = default_weight
            weight_value = max(1, min(5, weight_value))
            existing = d.get(clean)
            if existing is None or weight_value > existing:
                d[clean] = weight_value
    else:
        return fallback

    if d:
        return d
    return {} if allow_empty else fallback


def _normalize_hidden_tags(value):
    if value is None:
        return []

    if isinstance(value, str):
        try:
            tags = json.loads(value)
        except Exception:
            return None
    else:
        tags = value

    if isinstance(tags, dict):
        tags = list(tags.keys())
    if not isinstance(tags, list):
        return None

    cleaned = []
    seen = set()
    for tag in tags:
        if tag is None:
            continue
        clean = str(tag).strip().lower()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        cleaned.append(clean)
    return cleaned
