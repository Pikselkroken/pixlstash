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
    """Recursively create a safe, serializable dict from any SQLModel instance, dict, or SQLAlchemy adapter.

    Args:
        obj: An SQLModel instance, dict, list, or any primitive value to serialize.

    Returns:
        A JSON-safe dict (or list/primitive) representation of obj.
        - bytes fields are encoded as base64 strings.
        - Fields ending with '_' are treated as JSON/text and their key has the trailing '_' stripped.
        - Recurses into SQLModel relationships, lists, dicts, and SQLAlchemy collection adapters.
    """
    if CollectionAdapter and isinstance(obj, CollectionAdapter):
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
