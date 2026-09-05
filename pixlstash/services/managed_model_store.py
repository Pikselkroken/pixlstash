"""PixlStash's own model storage: exactly one folder, always there.

**The problem it solves.** With no registered folder there is nowhere to drop a
file and nowhere to import a run into, so drag-in is impossible on a fresh
install. Seeding a ``user`` folder and then forbidding removal of the last one
would be a lie - a ``user`` folder is an association the owner made, and one the
owner cannot dissolve is not an association. A zero-folder error state is worse:
it makes a normal condition (nothing catalogued in place yet) look broken.

**The answer is the ``managed`` kind**, which has been in the enum since B2 and
was created by nothing. It means storage PixlStash owns, the way the vault owns
picture files:

* **exactly one row always exists**, created at first run and never deleted;
* it is the default destination for a drop or an import;
* it is **relocatable but not removable** - there is no association to dissolve,
  only a place for the bytes to be;
* ``user`` and ``foreign`` folders may legitimately number zero. That is not an
  error and gets no message.

**Where it goes: beside the hub, not at a fixed platform path.** The hub already
sits next to ``server-config.json`` rather than at ``user_data_dir`` (issue
#168) precisely so a test or an alternate deployment gets its own instead of
reaching into the user's real one. That reasoning is *stronger* here, not
weaker: this is a directory files are copied into and unlinked from, so a fixed
path would have every test suite writing into the owner's real store. In the
default deployment the config dir is under the platform user directory, so a
real install does get it there; a `--server-config` deployment gets its own; and
either can move it, which is what relocation is for.

Creation is idempotent and is the only writer of a ``managed`` row: the HTTP
create route accepts ``user`` and ``source`` only, so a second one cannot be made
over the API, and the delete route refuses this one.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.builtin_caches import is_insightface_models_dir
from pixlstash.services.builtin_models import (
    BUILTIN_KIND,
    BUILTIN_OWNER,
    MOVABLE_ROOT_ONLY,
    is_builtin_model_dir,
)

logger = get_logger(__name__)

MANAGED_KIND = "managed"

# Who owns the folder. The other values name a subsystem whose artifacts happen
# to live somewhere (``insightface``, ``huggingface``, ``tagger``, ``ai-toolkit``);
# this one names the application itself, because the directory exists for no
# reason other than that PixlStash needs somewhere to put things.
MANAGED_OWNER = "pixlstash"

# ``root_only``: the store moves as a unit. Note that nothing enforces
# ``movable`` today - the mover does not read it - so this is a description of
# what the folder is, not a permission check. Whether the UI offers moving a
# single file *out* of the store is a verb question and is not decided here.
MANAGED_MOVABLE = "root_only"

# The directory name under the config dir. Plural and boring on purpose: it will
# be visible in a file browser and in ComfyUI once the picker node registers it.
MANAGED_DIRNAME = "models"


# The folder kinds whose contents are the owner's to destroy. `user` is a folder
# they registered; `managed` is the store PixlStash keeps for files it was given,
# which is where `Add file` and an import land, so a shelf that could not delete
# from it could not undo either of them.
#
# Not the whole rule on its own. `foreign` - one of the two kinds not listed
# here, beside the `source` roots the scanner never catalogues in place - covers
# PixlStash's own download store as well as the caches. See
# `deletes_unclaimed_files`.
DELETABLE_FOLDER_KINDS = ("user", MANAGED_KIND)


def managed_store_path(config_dir: str) -> str:
    """Where the managed store goes for a given config directory."""
    return os.path.join(config_dir, MANAGED_DIRNAME)


def deletes_unclaimed_files(folder_kind: str, folder_path: str) -> bool:
    """Whether the shelf may unlink a file it does not CLAIM out of this folder.

    Recognised by path for the same reason :func:`relocatable_identity` is:
    ``declare_folder`` writes the same three columns for every root PixlStash
    declares, so the columns cannot tell the download folder apart from the
    InsightFace packs or the HuggingFace cache.

    Three folders say yes. A ``user`` folder and the managed store are the
    owner's outright. The third is the folder PixlStash downloads its engines
    into: the ENGINES there are ours and refused by their own gate wherever they
    live, but a file we merely *found* beside them is not, and it is declared
    (#927) exactly so a 339 MB ``best.pt`` is visible rather than invisible.
    Visible and unremovable is the wrong half of that - Forget wants every copy
    gone before it will act, so there would be nothing left but a file manager.

    The other two declared roots say no. The InsightFace packs are re-fetched,
    and the HuggingFace cache is a symlink store shared with every other tool on
    the machine.

    Args:
        folder_kind: ``model_folder.kind``.
        folder_path: ``model_folder.path``, as registered.
    """
    return folder_kind in DELETABLE_FOLDER_KINDS or is_builtin_model_dir(folder_path)


def relocatable_identity(folder) -> Optional[tuple[str, str, str]]:
    """The ``(kind, owner, movable)`` a folder keeps when it relocates, or None.

    A relocation registers its destination as an ordinary ``user`` folder while
    the files move and promotes it back in one transaction at the end, so it has
    to know what the folder *is* - and that answer differs per root. This is the
    one place that says which roots relocate at all; the route enforces it and
    ``GET /model-folders`` reports it, from here, rather than each deciding.

    Three do. The managed store, which is storage PixlStash owns outright; the
    folder PixlStash downloads its own engines into, whose location became a
    recorded setting in #905 (closing the long-open #112); and the InsightFace
    packs, whose root became one in #906 - recorded the same way and for the same
    reason, because it is machine-global too. The fourth declared root does not:
    the HuggingFace cache is ``fixed`` - its location is read from the
    environment at import by a library shared with every other tool on the
    machine, so "moving" it is a restart and a re-download.

    The two recorded roots return the same identity because ``declare_folder``
    writes the same three columns for every root PixlStash declares - which is
    also why each is recognised by *path* rather than by those columns.

    Args:
        folder: A ``model_folder`` row, or anything with ``kind`` and ``path``.

    Returns:
        The identity to restore at the new path, or None when the folder does
        not relocate.
    """
    if folder["kind"] == MANAGED_KIND:
        return (MANAGED_KIND, MANAGED_OWNER, MANAGED_MOVABLE)
    if is_builtin_model_dir(folder["path"]) or is_insightface_models_dir(
        folder["path"]
    ):
        return (BUILTIN_KIND, BUILTIN_OWNER, MOVABLE_ROOT_ONLY)
    return None


def find_managed_folder(hub: HubDatabase) -> Optional[dict]:
    """Return the managed folder row, or None before first-run creation.

    If more than one exists - which nothing should be able to produce - the
    lowest id wins and the rest are logged loudly rather than deleted. Deleting a
    folder row drops its ``model_file`` rows, so guessing wrong here would
    tombstone real locations to tidy up a bookkeeping error.
    """
    rows = hub.fetchall(
        "SELECT id, path, kind, owner, movable, created_at FROM model_folder "
        "WHERE kind = ? ORDER BY id",
        (MANAGED_KIND,),
    )
    if not rows:
        return None
    if len(rows) > 1:
        logger.error(
            "%d managed model folders exist (%s); exactly one is expected. Using "
            "id %s. The extras are left alone: dropping a folder row would "
            "tombstone its location rows, which is not a safe way to tidy up a "
            "bookkeeping error.",
            len(rows),
            [row["path"] for row in rows],
            rows[0]["id"],
        )
    return dict(rows[0])


def ensure_managed_folder(hub: HubDatabase, config_dir: str) -> Optional[dict]:
    """Create the managed store if it is not there yet, and return its row.

    Idempotent, and safe to call on every start: an existing row is returned
    untouched, including one the owner has relocated to another drive, because
    the row's ``path`` is the authority and this function never overrules it.

    Args:
        hub: The open hub database.
        config_dir: The directory ``server-config.json`` and ``hub.db`` live in.
            The store is created beside them.

    Returns:
        The managed folder row, or ``None`` if the directory could not be
        created - a shelf with no default destination is degraded but usable,
        and refusing to start the server over it would be worse.
    """
    existing = find_managed_folder(hub)
    if existing is not None:
        _ensure_directory(existing["path"], existing=True)
        return existing

    path = managed_store_path(config_dir)
    if not _ensure_directory(path, existing=False):
        return None

    now = datetime.now(timezone.utc).isoformat()
    with hub.transaction() as conn:
        # A folder already registered at this path (a user who pointed a `user`
        # folder here first) is promoted rather than duplicated: `path` is UNIQUE
        # and the alternative is a failed insert on every start.
        conn.execute(
            "INSERT INTO model_folder (path, kind, owner, movable, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET kind = excluded.kind, "
            "owner = excluded.owner, movable = excluded.movable",
            (path, MANAGED_KIND, MANAGED_OWNER, MANAGED_MOVABLE, now),
        )
    row = find_managed_folder(hub)
    logger.info("Managed model store registered at %s (id=%s).", path, row["id"])
    return row


def _ensure_directory(path: str, *, existing: bool) -> bool:
    """Make sure the store's directory is there, without ever failing a start."""
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError as exc:
        logger.error(
            "Could not create the managed model store at %s: %s. %s The shelf "
            "will have no default destination until the directory is reachable.",
            path,
            exc,
            "The folder is still registered, so a relocation can fix it."
            if existing
            else "It is not registered; the next start will try again.",
        )
        return False
