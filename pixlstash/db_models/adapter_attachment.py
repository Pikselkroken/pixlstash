"""Which adapters this library's characters and sets use.

The only model-shelf table that lives in a **vault**. The adapters themselves,
the folders they sit in and the checkpoints beside them are all hub tables,
because those are facts about the machine. Which character a LoRA belongs to is
a fact about *this library*, so it lives here and travels with the library.

**The link is the sha256, never an integer adapter id.** Two reasons, and both
have bitten this codebase before:

* No foreign key can span the hub and a vault. They are separate SQLite files,
  so an integer id here would be an unenforceable reference that nothing checks.
* SQLite hands a deleted row's id to the next insert. An integer link would
  silently re-point at a *different* adapter after a delete plus an insert, and
  the row would look perfectly valid while being wrong. This is the same
  recycled-identifier hazard `library.uuid` and the hub's `AUTOINCREMENT` ids
  exist to eliminate.

The sha256 is already the adapter's identity, already the API path component,
and stable across a hub rebuild or a re-import, which is exactly what an
attachment needs to survive.
"""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

ENTITY_CHARACTER = "character"
ENTITY_SET = "set"


class AdapterAttachment(SQLModel, table=True):
    """One adapter attached to one character or one picture set.

    Characters and sets are peers here, addressed through ``entity_type`` rather
    than through two nullable scalar columns. A scalar ``character_id`` plus a
    scalar ``set_id`` would make "attached to both" and "attached to neither"
    representable states that mean nothing, and every reader would have to
    decide what to do about them.

    The composite primary key makes attaching the same adapter to the same
    entity twice a no-op at the database level rather than a duplicate row the
    UI has to de-duplicate.
    """

    __tablename__ = "adapter_attachment"

    adapter_sha256: str = Field(primary_key=True, index=True)
    entity_type: str = Field(primary_key=True)
    """``character`` or ``set``. See ENTITY_CHARACTER / ENTITY_SET."""

    entity_id: int = Field(primary_key=True, index=True)
    created_at: Optional[datetime] = Field(default=None)
