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
        is_reference: int = 0,
        has_embedding: bool = False,
        ext: Optional[str] = None,
    ):
        # Always ensure the id has an extension (default to .png)
        if id:
            if "." not in id:
                ext_val = ext if ext else "png"
                self.id = f"{id}.{ext_val}"
            else:
                self.id = id
        else:
            ext_val = ext if ext else "png"
            self.id = f"{uuid.uuid4().hex}.{ext_val}"
        self.character_id = character_id
        self.description = description
        self.tags = tags or []
        self.created_at = created_at or datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        self.is_reference = is_reference
        self.has_embedding = has_embedding
