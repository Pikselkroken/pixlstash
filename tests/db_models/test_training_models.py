"""Unit tests for the ai-toolkit config plumbing on the owner's user row.

The training run table lives in the hub and is tested with the hub schema
(``tests/test_hub_model_shelf_schema.py``). What is left here is the config
round-trip: the three ai-toolkit paths and commit serialise, patch and clear
through the shared user-settings helpers.
"""

from pixlstash.db_models import User
from pixlstash.utils.service.user_settings_utils import (
    apply_user_config_patch,
    serialize_user_config,
)


_AI_TOOLKIT_KEYS = {
    "ai_toolkit_repo_path": "/opt/ai-toolkit",
    "ai_toolkit_python": "/opt/ai-toolkit/.venv/bin/python",
    "ai_toolkit_commit": "abc123",
}


def test_config_round_trip_ai_toolkit_keys():
    user = User()

    updated = apply_user_config_patch(user, dict(_AI_TOOLKIT_KEYS))
    assert updated is True
    for key, value in _AI_TOOLKIT_KEYS.items():
        assert getattr(user, key) == value

    config = serialize_user_config(user)
    for key, value in _AI_TOOLKIT_KEYS.items():
        assert config[key] == value


def test_config_ai_toolkit_keys_clearable():
    user = User()
    apply_user_config_patch(user, dict(_AI_TOOLKIT_KEYS))

    cleared = apply_user_config_patch(user, {key: "" for key in _AI_TOOLKIT_KEYS})
    assert cleared is True
    for key in _AI_TOOLKIT_KEYS:
        assert getattr(user, key) is None


def test_config_ai_toolkit_keys_trimmed():
    user = User()
    apply_user_config_patch(user, {"ai_toolkit_repo_path": "  /opt/ai-toolkit  "})
    assert user.ai_toolkit_repo_path == "/opt/ai-toolkit"
