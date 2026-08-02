"""The install ID: a local, random, non-derived identifier for one installation.

The ID exists so that cohort retention is computable at all. Aggregate counts
alone cannot separate an install that paused from one that churned, which is the
question the telemetry plan was written to answer.

Four properties are load-bearing. Weakening any of them turns this file from a
counter into a fingerprint:

* **Random, never derived.** A fresh ``uuid4`` and nothing else. Never seeded
  from a MAC address, hostname, machine ID, serial number, or any other hardware
  or user property. A derived ID is a fingerprint and will be read as one no
  matter what the intent was.
* **Stored beside the server config, not in the library database.** A snapshot
  restore, a library switch, or a rebuilt vault must not change or duplicate the
  identity of the install.
* **Coarse by construction.** The record stores a creation *date*, not a
  timestamp. A precise creation instant is close to unique on its own, so it is
  never written in the first place rather than being trimmed at send time.
* **Never transmitted from here.** Nothing in this module opens a socket. The ID
  is written and read locally; sending it is gated on the user's consent and
  lands in a later change.

``is_new_install`` separates genuinely new installs from the upgrade wave. Every
existing user who upgrades and opts in would otherwise appear with a first-seen
date of the upgrade, so their "week 1" would really be week 40 of their life as a
user and the week-1 retention number would read absurdly high. The flag is
decided once, when the ID is created, from whether this installation already had
a server config: if it did, the install predates the ID and is an upgrade.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.atomic_write import write_json_atomic

logger = get_logger(__name__)

INSTALL_ID_FILENAME = "install-id.json"

# Bumped only if the on-disk shape changes. A record with an unrecognised
# version is treated as unreadable and regenerated rather than guessed at.
_RECORD_VERSION = 1


@dataclass(frozen=True)
class InstallIdentity:
    """One installation's telemetry identity, as stored on disk.

    Attributes:
        install_id: Random UUIDv4 string. Never derived from anything.
        created_date: UTC date the ID was generated, ``YYYY-MM-DD``. Deliberately
            a date and not a timestamp. See the module docstring.
        is_new_install: True only if this installation had no server config when
            the ID was generated, i.e. it is a genuinely fresh install rather
            than an existing one that upgraded into the telemetry release.
    """

    install_id: str
    created_date: str
    is_new_install: bool

    def to_record(self) -> dict:
        """Return the JSON-serialisable on-disk representation."""
        return {
            "version": _RECORD_VERSION,
            "install_id": self.install_id,
            "created_date": self.created_date,
            "is_new_install": self.is_new_install,
        }


def install_id_path(server_config_path: str) -> str:
    """Return the install-ID file path for a given server-config path.

    The ID lives beside ``server-config.json`` so it follows a custom
    ``--server-config`` location and stays out of the library database.

    Args:
        server_config_path: Path to the server config file.

    Returns:
        Absolute path to the install-ID file.
    """
    config_dir = os.path.dirname(os.path.abspath(server_config_path))
    return os.path.join(config_dir, INSTALL_ID_FILENAME)


def _parse_record(record, path: str) -> InstallIdentity | None:
    """Validate a decoded install-ID record.

    Args:
        record: Object decoded from the install-ID file.
        path: File the record came from, for log context.

    Returns:
        The parsed identity, or None if the record is unusable.
    """
    if not isinstance(record, dict):
        logger.warning(
            "Install-ID file %s does not contain a JSON object (got %s); "
            "it will be regenerated.",
            path,
            type(record).__name__,
        )
        return None

    version = record.get("version")
    if version != _RECORD_VERSION:
        logger.warning(
            "Install-ID file %s has unsupported record version %r (expected %d); "
            "it will be regenerated.",
            path,
            version,
            _RECORD_VERSION,
        )
        return None

    raw_id = record.get("install_id")
    try:
        # Round-trip through UUID so a hand-edited or truncated value is rejected
        # rather than sent verbatim to the ingestion endpoint later.
        parsed_id = str(uuid.UUID(str(raw_id)))
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Install-ID file %s holds %r, which is not a valid UUID (%s); "
            "it will be regenerated.",
            path,
            raw_id,
            exc,
        )
        return None

    raw_date = record.get("created_date")
    try:
        date.fromisoformat(str(raw_date))
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Install-ID file %s holds created_date %r, which is not an ISO date "
            "(%s); it will be regenerated.",
            path,
            raw_date,
            exc,
        )
        return None

    return InstallIdentity(
        install_id=parsed_id,
        created_date=str(raw_date),
        is_new_install=bool(record.get("is_new_install", False)),
    )


def read_install_identity(server_config_path: str) -> InstallIdentity | None:
    """Read the stored install identity without creating one.

    Args:
        server_config_path: Path to the server config file.

    Returns:
        The stored identity, or None if there is none or it is unreadable.
    """
    path = install_id_path(server_config_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Could not read install-ID file %s (%s); it will be regenerated on "
            "the next ensure_install_identity call.",
            path,
            exc,
        )
        return None
    return _parse_record(record, path)


def _write_identity(path: str, identity: InstallIdentity) -> InstallIdentity | None:
    """Persist an identity atomically.

    Args:
        path: Install-ID file path.
        identity: Identity to write.

    Returns:
        The identity if it was persisted, None if the write failed.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_json_atomic(path, identity.to_record())
    except OSError as exc:
        # A non-persisted ID would be regenerated on every boot, which would
        # inflate the install count rather than measure it. Report the failure
        # and let the caller treat the install ID as unavailable. Never fall
        # back to an in-memory ID that looks durable and is not.
        logger.warning(
            "Could not write install-ID file %s (%s); the anonymous install ID "
            "is unavailable on this installation and cohort retention cannot be "
            "reported from it. Check that the config directory is writable.",
            path,
            exc,
        )
        return None
    return identity


def ensure_install_identity(server_config_path: str) -> InstallIdentity | None:
    """Return the install identity, generating and storing one if absent.

    Call this *before* the server config file is created, so a genuinely fresh
    install is distinguishable from an upgrade (see the module docstring).

    Args:
        server_config_path: Path to the server config file.

    Returns:
        The identity, or None if it could not be persisted.
    """
    existing = read_install_identity(server_config_path)
    if existing is not None:
        return existing

    # Decided once, here: an installation that already has a server config
    # predates this ID, so it joins the upgrade population rather than the
    # new-install cohort.
    is_new_install = not os.path.exists(server_config_path)
    identity = InstallIdentity(
        install_id=str(uuid.uuid4()),
        created_date=datetime.now(timezone.utc).date().isoformat(),
        is_new_install=is_new_install,
    )
    written = _write_identity(install_id_path(server_config_path), identity)
    if written is not None:
        logger.info(
            "Generated anonymous install ID (new_install=%s). It is stored "
            "locally and is not sent anywhere unless telemetry is enabled.",
            is_new_install,
        )
    return written


def recreate_install_identity(server_config_path: str) -> InstallIdentity | None:
    """Replace the stored install ID with a freshly generated, unlinkable one.

    The previous ID is overwritten and is never sent again; nothing on disk ties
    the two together.

    ``is_new_install`` is forced to False regardless of what the previous record
    said. The installation is not new, only the identity is, and reporting it
    as new would put an established user into the new-install cohort, which is
    the exact bias the flag exists to remove.

    Args:
        server_config_path: Path to the server config file.

    Returns:
        The new identity, or None if it could not be persisted.
    """
    identity = InstallIdentity(
        install_id=str(uuid.uuid4()),
        created_date=datetime.now(timezone.utc).date().isoformat(),
        is_new_install=False,
    )
    written = _write_identity(install_id_path(server_config_path), identity)
    if written is not None:
        logger.info("Recreated the anonymous install ID at the user's request.")
    return written
