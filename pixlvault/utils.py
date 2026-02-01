import base64
import json
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
