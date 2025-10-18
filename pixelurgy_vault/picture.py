from .logging import get_logger
from typing import Optional, List
import uuid
from datetime import datetime, timezone

# Configure logging for the module
logger = get_logger(__name__)


class Picture:
    """Master asset representing a logical picture (stable UUID)."""

    def __init__(
        self,
        id: Optional[str] = None,
        character_id: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        created_at: Optional[str] = None,
    ):
        self.id = id if id else uuid.uuid4().hex
        self.character_id = character_id
        self.description = description
        self.tags = tags or []
        self.created_at = created_at or datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
