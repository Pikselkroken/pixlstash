import base64
import json
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


def serialize_user_config(user) -> dict:
    from pixlvault.db_models import (
        User,
        DEFAULT_SMART_SCORE_PENALIZED_TAGS,
        DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    )

    default_user = User()
    source = user or default_user
    data = safe_model_dict(source)

    allowed_fields = {
        "description",
        "sort",
        "descending",
        "columns",
        "show_stars",
        "show_face_bboxes",
        "show_hand_bboxes",
        "show_format",
        "show_resolution",
        "show_problem_icon",
        "similarity_character",
        "auto_scrapheap_smart_score_threshold",
        "auto_scrapheap_lookback_minutes",
    }

    config = {
        key: (value if value is not None else getattr(default_user, key))
        for key in allowed_fields
        for value in (data.get(key),)
    }

    config["smart_score_penalized_tags"] = normalize_smart_score_penalized_tags(
        getattr(source, "smart_score_penalized_tags", None),
        DEFAULT_SMART_SCORE_PENALIZED_TAGS,
        default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    )
    config["sort_order"] = config["sort"]
    return config


def apply_user_config_patch(user, patch_data) -> bool:
    from pixlvault.db_models import DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT

    allowed_fields = {
        "description",
        "sort",
        "descending",
        "columns",
        "show_stars",
        "show_face_bboxes",
        "show_hand_bboxes",
        "show_format",
        "show_resolution",
        "show_problem_icon",
        "similarity_character",
        "smart_score_penalized_tags",
        "auto_scrapheap_smart_score_threshold",
        "auto_scrapheap_lookback_minutes",
    }

    updated = False
    for key, value in patch_data.items():
        if key not in allowed_fields:
            raise ValueError(f"Key '{key}' does not exist in config.")
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
        if key == "smart_score_penalized_tags":
            if value in ("", None):
                new_value = None
            else:
                normalized = normalize_smart_score_penalized_tags(
                    value,
                    None,
                    allow_empty=True,
                    default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
                )
                if normalized is None:
                    raise ValueError(
                        "smart_score_penalized_tags must be a JSON list or object"
                    )
                new_value = json.dumps(normalized)
            if user.smart_score_penalized_tags != new_value:
                user.smart_score_penalized_tags = new_value
                updated = True
            continue
        if key == "auto_scrapheap_smart_score_threshold":
            if value in ("", None, "null"):
                new_value = None
            else:
                new_value = float(value)
            if user.auto_scrapheap_smart_score_threshold != new_value:
                user.auto_scrapheap_smart_score_threshold = new_value
                updated = True
            continue
        if key == "auto_scrapheap_lookback_minutes":
            if value in ("", None, "null"):
                new_value = None
            else:
                new_value = int(value)
            if user.auto_scrapheap_lookback_minutes != new_value:
                user.auto_scrapheap_lookback_minutes = new_value
                updated = True
            continue
        if key == "columns":
            new_value = int(value)
            if user.columns != new_value:
                user.columns = new_value
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


def normalize_thumbnail_size(value):
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


def normalize_smart_score_penalized_tags(
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
        normalized = {}
        for tag in tags:
            if tag is None:
                continue
            clean = str(tag).strip().lower()
            if not clean:
                continue
            normalized[clean] = default_weight
    elif isinstance(tags, dict):
        normalized = {}
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
            existing = normalized.get(clean)
            if existing is None or weight_value > existing:
                normalized[clean] = weight_value
    else:
        return fallback

    if normalized:
        return normalized
    return {} if allow_empty else fallback
