"""Opt-in telemetry support.

This package holds the local-only pieces of the telemetry mechanism: the
install-ID store and (later) the payload builder. Nothing in it opens a socket.
Transmission is a separate, consent-gated concern that lands in a later change.
"""

from pixlstash.telemetry.install_id import (
    InstallIdentity,
    ensure_install_identity,
    install_id_path,
    read_install_identity,
    recreate_install_identity,
)

__all__ = [
    "InstallIdentity",
    "ensure_install_identity",
    "install_id_path",
    "read_install_identity",
    "recreate_install_identity",
]
