"""Opt-in telemetry support.

Two halves. ``install_id`` is local-only and opens no socket: it generates and
stores the identifier. ``sender`` is the one place anything is transmitted, and
it does nothing unless the user has turned the install-ID category on.
"""

from pixlstash.telemetry.install_id import (
    InstallIdentity,
    ensure_install_identity,
    install_id_path,
    read_install_identity,
    recreate_install_identity,
)
from pixlstash.telemetry.sender import (
    maybe_send_in_background,
    send_install_ping,
)

__all__ = [
    "InstallIdentity",
    "maybe_send_in_background",
    "send_install_ping",
    "ensure_install_identity",
    "install_id_path",
    "read_install_identity",
    "recreate_install_identity",
]
