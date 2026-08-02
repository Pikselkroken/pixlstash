"""Tests for the local install-ID store and the telemetry consent settings.

These cover the properties the telemetry plan calls binding: the ID is random
and non-derived, it is off-by-default at every consent category, the consent
question is recorded as asked exactly once, a fresh install is distinguishable
from an upgrade, a recreate is unlinkable and never claims to be a new install,
and a corrupt or unwritable store degrades to "unavailable" rather than to a
silently non-durable ID.
"""

import json
import os
import uuid

import pytest

from pixlstash.db_models import User
from pixlstash.telemetry.install_id import (
    INSTALL_ID_FILENAME,
    InstallIdentity,
    ensure_install_identity,
    install_id_path,
    read_install_identity,
    recreate_install_identity,
)
from pixlstash.utils.service.user_settings_utils import (
    apply_user_config_patch,
    serialize_user_config,
)

TELEMETRY_CATEGORIES = (
    "telemetry_send_install_id",
    "telemetry_send_feature_usage",
    "telemetry_send_error_reports",
    "telemetry_send_hardware_profile",
)


@pytest.fixture
def config_path(tmp_path):
    """Return a server-config path inside an empty, writable config dir."""
    return str(tmp_path / "server-config.json")


def _write_server_config(path):
    """Create a server-config file, simulating a pre-existing installation."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"port": 9537}, handle)


# ---------------------------------------------------------------------------
# Install-ID generation and storage
# ---------------------------------------------------------------------------


def test_generates_random_uuid4_beside_the_server_config(config_path):
    identity = ensure_install_identity(config_path)

    assert identity is not None
    parsed = uuid.UUID(identity.install_id)
    # Version 4 == randomly generated. A derived ID is a fingerprint; this
    # assertion is the mechanical guard on that promise.
    assert parsed.version == 4
    assert os.path.exists(install_id_path(config_path))
    assert os.path.basename(install_id_path(config_path)) == INSTALL_ID_FILENAME


def test_ids_are_not_derived_from_the_machine(config_path, tmp_path):
    """Two installations on the same machine must not share an ID."""
    other_config = str(tmp_path / "other" / "server-config.json")
    os.makedirs(os.path.dirname(other_config), exist_ok=True)

    first = ensure_install_identity(config_path)
    second = ensure_install_identity(other_config)

    assert first is not None and second is not None
    assert first.install_id != second.install_id


def test_is_stable_across_calls(config_path):
    first = ensure_install_identity(config_path)
    second = ensure_install_identity(config_path)

    assert first is not None
    assert second is not None
    assert first.install_id == second.install_id


def test_created_date_is_a_date_not_a_timestamp(config_path):
    identity = ensure_install_identity(config_path)

    assert identity is not None
    # YYYY-MM-DD and nothing finer: a precise creation instant is close to
    # unique on its own, so it is never recorded in the first place.
    assert len(identity.created_date) == 10
    assert identity.created_date.count("-") == 2


# ---------------------------------------------------------------------------
# is_new_install: separating fresh installs from the upgrade wave
# ---------------------------------------------------------------------------


def test_fresh_install_is_marked_new(config_path):
    identity = ensure_install_identity(config_path)

    assert identity is not None
    assert identity.is_new_install is True


def test_existing_install_upgrading_is_not_marked_new(config_path):
    _write_server_config(config_path)

    identity = ensure_install_identity(config_path)

    assert identity is not None
    assert identity.is_new_install is False


def test_is_new_install_survives_a_reread(config_path):
    _write_server_config(config_path)
    ensure_install_identity(config_path)

    reread = read_install_identity(config_path)

    assert reread is not None
    assert reread.is_new_install is False


# ---------------------------------------------------------------------------
# Recreate
# ---------------------------------------------------------------------------


def test_recreate_replaces_the_id(config_path):
    original = ensure_install_identity(config_path)
    recreated = recreate_install_identity(config_path)

    assert original is not None and recreated is not None
    assert recreated.install_id != original.install_id
    assert read_install_identity(config_path).install_id == recreated.install_id


def test_recreate_leaves_nothing_linking_the_two_ids(config_path):
    original = ensure_install_identity(config_path)
    recreate_install_identity(config_path)

    with open(install_id_path(config_path), "r", encoding="utf-8") as handle:
        raw = handle.read()

    assert original is not None
    assert original.install_id not in raw


def test_recreate_never_claims_to_be_a_new_install(config_path):
    # Even starting from a genuinely fresh install, whose record says new.
    original = ensure_install_identity(config_path)
    assert original is not None and original.is_new_install is True

    recreated = recreate_install_identity(config_path)

    # The identity is new; the installation is not. Reporting otherwise would
    # drop an established user into the new-install cohort.
    assert recreated is not None
    assert recreated.is_new_install is False


# ---------------------------------------------------------------------------
# Degraded stores
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        json.dumps([1, 2, 3]),
        json.dumps({"version": 99, "install_id": str(uuid.uuid4())}),
        json.dumps({"version": 1, "install_id": "not-a-uuid"}),
        json.dumps({"version": 1, "install_id": str(uuid.uuid4())}),
    ],
    ids=["malformed", "not-an-object", "bad-version", "bad-uuid", "missing-date"],
)
def test_unreadable_record_is_regenerated_not_trusted(config_path, content):
    path = install_id_path(config_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)

    assert read_install_identity(config_path) is None

    identity = ensure_install_identity(config_path)
    assert identity is not None
    assert uuid.UUID(identity.install_id).version == 4


def test_missing_store_reads_as_none_without_creating_one(config_path):
    assert read_install_identity(config_path) is None
    assert not os.path.exists(install_id_path(config_path))


def test_unwritable_store_reports_unavailable_rather_than_a_volatile_id(
    monkeypatch, config_path
):
    def _refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(
        "pixlstash.telemetry.install_id.write_json_atomic",
        _refuse,
    )

    # None, not an in-memory ID: a non-durable ID would be regenerated every
    # boot and would inflate install counts rather than measure them.
    assert ensure_install_identity(config_path) is None
    assert recreate_install_identity(config_path) is None


def test_record_round_trips(config_path):
    identity = InstallIdentity(
        install_id=str(uuid.uuid4()),
        created_date="2026-08-02",
        is_new_install=True,
    )
    record = identity.to_record()

    assert record["install_id"] == identity.install_id
    assert record["created_date"] == "2026-08-02"
    assert record["is_new_install"] is True


# ---------------------------------------------------------------------------
# Consent settings
# ---------------------------------------------------------------------------


def test_every_category_is_off_by_default():
    config = serialize_user_config(User())

    for field in TELEMETRY_CATEGORIES:
        assert config[field] is False, f"{field} must default to off"


def test_an_upgraded_row_with_null_columns_reads_as_off():
    # Rows that predate migration 0090 read NULL for all five columns.
    user = User()
    for field in (*TELEMETRY_CATEGORIES, "telemetry_consent_prompted"):
        setattr(user, field, None)

    config = serialize_user_config(user)

    for field in TELEMETRY_CATEGORIES:
        assert config[field] is False
    assert config["telemetry_consent_prompted"] is False


def test_consent_is_recorded_as_unasked_by_default():
    config = serialize_user_config(User())

    assert config["telemetry_consent_prompted"] is False


@pytest.mark.parametrize("field", TELEMETRY_CATEGORIES)
def test_a_category_can_be_enabled_and_disabled(field):
    user = User()

    assert apply_user_config_patch(user, {field: True}) is True
    assert getattr(user, field) is True

    assert apply_user_config_patch(user, {field: False}) is True
    assert getattr(user, field) is False


@pytest.mark.parametrize(
    "falsey",
    ["", None, "null", "false", "False", "0", 0, False],
)
@pytest.mark.parametrize("field", TELEMETRY_CATEGORIES)
def test_falsey_patch_values_never_enable_a_category(field, falsey):
    user = User()
    setattr(user, field, True)

    apply_user_config_patch(user, {field: falsey})

    # "false" as a string is truthy in Python; coercing with bare bool() here
    # would silently turn telemetry on for a client that sent form-encoded data.
    assert getattr(user, field) is False


def test_declining_records_the_prompt_as_answered():
    user = User()

    apply_user_config_patch(
        user,
        {field: False for field in TELEMETRY_CATEGORIES}
        | {"telemetry_consent_prompted": True},
    )

    # Declining is a decision. The prompt must never be raised again.
    assert user.telemetry_consent_prompted is True
    for field in TELEMETRY_CATEGORIES:
        assert getattr(user, field) is False


def test_unknown_telemetry_key_is_rejected():
    with pytest.raises(ValueError):
        apply_user_config_patch(User(), {"telemetry_send_everything": True})
