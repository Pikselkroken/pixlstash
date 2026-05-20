"""Service layer for picture-sharing operations.

Extracted from pixlstash/routes/share.py to keep route handlers thin.
Handles token validation and watermark resolution for shared pictures.
"""

from typing import TYPE_CHECKING

from pixlstash.db_models.picture import Picture
from pixlstash.db_models.user import User
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.watermark import get_default_watermark_bytes

if TYPE_CHECKING:
    from pixlstash.auth import AuthService
    from pixlstash.db_models.user_token import UserToken
    from pixlstash.vault import Vault

logger = get_logger(__name__)


def validate_picture_share_token(
    auth: "AuthService", token_value: str
) -> "UserToken | None":
    """Validate a raw share-token value for picture READ access.

    Checks that the token exists, is not expired, has scope READ, and applies
    to a picture resource.  Returns None for any invalid or mismatched token.

    Args:
        auth: AuthService instance used for token lookup.
        token_value: The raw token string (without extension) from the URL slug.

    Returns:
        The matching UserToken if valid, or None.
    """
    matched_token = auth.token_from_value(token_value)
    if matched_token is None:
        return None
    if (
        matched_token.scope != "READ"
        or matched_token.resource_type != "picture"
        or matched_token.resource_id is None
    ):
        return None
    return matched_token


def get_shared_picture(vault: "Vault", pic_id: int) -> "Picture | None":
    """Fetch a non-deleted picture by ID for share serving.

    Args:
        vault: Application vault, used for DB task dispatch.
        pic_id: Picture ID to fetch.

    Returns:
        The Picture instance, or None if not found or deleted.
    """
    pics = vault.db.run_immediate_read_task(
        lambda session: Picture.find(session, id=pic_id, include_deleted=False)
    )
    return pics[0] if pics else None


def get_user_watermark_bytes(vault: "Vault", user_id: int) -> bytes | None:
    """Return the watermark bytes for a user (custom or default).

    Fetches the user's custom watermark from the database; falls back to the
    default server watermark if none is set.

    Args:
        vault: Application vault, used for DB task dispatch.
        user_id: ID of the user whose watermark to retrieve.

    Returns:
        Watermark image bytes, or None if no watermark is configured.
    """
    user = vault.db.run_immediate_read_task(
        lambda session: session.get(User, user_id)
    )
    custom = getattr(user, "watermark_image", None) if user else None
    if custom:
        return custom
    return get_default_watermark_bytes()
