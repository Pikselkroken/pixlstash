"""Telemetry routes: reading and recreating the anonymous install ID.

The four consent flags themselves are ordinary user settings and ride the
existing ``/users/me/config`` pair. What needs its own surface is the install ID,
because it is a property of the *installation* rather than of the user row: it
lives beside the server config so a snapshot restore or a library switch cannot
change or duplicate it.

Nothing here transmits anything. These endpoints read and rewrite a local file.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.telemetry import (
    ensure_install_identity,
    recreate_install_identity,
)

logger = get_logger(__name__)


def create_router(server) -> APIRouter:
    """Build the telemetry router.

    Args:
        server: The running :class:`~pixlstash.server.Server` instance.

    Returns:
        An ``APIRouter`` with the install-ID endpoints mounted.
    """
    router = APIRouter()

    class InstallIdResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        available: bool = Field(
            description=(
                "False when the install ID could not be stored, for example a "
                "read-only config directory. A non-persisted ID would be "
                "regenerated on every boot and would inflate install counts "
                "rather than measure them, so none is invented."
            ),
        )
        install_id: Optional[str] = Field(
            default=None,
            description=(
                "Random UUIDv4 identifying this installation. Never derived "
                "from a MAC address, hostname, machine ID, or any other "
                "hardware property. Null when unavailable."
            ),
        )
        created_date: Optional[str] = Field(
            default=None,
            description=(
                "UTC date the ID was generated, YYYY-MM-DD. A date and not a "
                "timestamp, because a precise creation instant is close to "
                "unique on its own."
            ),
        )
        is_new_install: Optional[bool] = Field(
            default=None,
            description=(
                "True only if this installation had no server config when the "
                "ID was generated and install-ID telemetry was not declined in "
                "the initial consent decision. Separates real new-install "
                "cohorts from upgrades and delayed opt-ins. Always false after "
                "a recreate: the identity is new, the installation is not."
            ),
        )

    def _identity_payload(identity) -> dict:
        """Shape an :class:`InstallIdentity` (or None) into the response dict."""
        if identity is None:
            return {
                "available": False,
                "install_id": None,
                "created_date": None,
                "is_new_install": None,
            }
        return {
            "available": True,
            "install_id": identity.install_id,
            "created_date": identity.created_date,
            "is_new_install": identity.is_new_install,
        }

    @router.get(
        "/telemetry/install-id",
        summary="Get the anonymous install ID",
        response_model=InstallIdResponse,
        description=(
            "Returns this installation's anonymous install ID, generating and "
            "storing one if none exists yet. The ID is a locally generated "
            "random UUIDv4 stored beside the server config. It is never sent "
            "anywhere unless the user has enabled the install-ID telemetry "
            "category."
        ),
    )
    def get_install_id(request: Request):
        server.auth.ensure_secure_when_required(request)
        identity = ensure_install_identity(server.server_config_path)
        return _identity_payload(identity)

    @router.post(
        "/telemetry/install-id/recreate",
        summary="Recreate the anonymous install ID",
        response_model=InstallIdResponse,
        description=(
            "Discards the stored install ID and generates a fresh, unlinkable "
            "one. The previous ID is overwritten, is never sent again, and "
            "nothing on disk ties the two together. The new record always "
            "reports is_new_install=false, because the identity is new but the "
            "installation is not."
        ),
    )
    def recreate_install_id(request: Request):
        server.auth.ensure_secure_when_required(request)
        identity = recreate_install_identity(server.server_config_path)
        if identity is None:
            # The old ID may or may not still be on disk; either way the user
            # asked for a new one and did not get it, so say so rather than
            # returning a success-shaped body.
            raise HTTPException(
                status_code=500,
                detail=(
                    "Could not write the install ID. Check that the server "
                    "config directory is writable; see the server log for the "
                    "underlying error."
                ),
            )
        return _identity_payload(identity)

    return router
