"""What PixlStash downloads for itself, declared rather than discovered.

The shelf catalogues model files by **reading** them: the scanner walks a
registered folder, reads each ``.safetensors`` header and decides what the file
is. That is the right approach for a folder of LoRAs the owner assembled, and
the wrong one for our own engines — half of them are ONNX or ``.pt``, which the
scanner does not even yield (``MODEL_SUFFIX`` is ``.safetensors``), and all of
them are files *we* chose to download. We do not have to guess what they are.
We know.

So this module declares them, and :func:`declare_builtin_models` writes the rows
from the declaration. Nothing is parsed and nothing is hashed, which is also why
a 339 MB engine costs nothing at start-up.

**Why the filenames are restated here rather than imported.** Every downloader
names its files as module constants — ``PIXLSTASH_TAGGER_FILENAME``,
``WD14_CSV_FILE``, ``ImageEmbeddingTask.AESTHETIC_MODELS`` — but those modules
import onnxruntime, torch, cv2 and PIL at module level, and start-up must not
pay that to learn two strings. They are duplicated here and pinned by
``tests/test_builtin_models.py``, which imports the real modules and asserts the
two agree. The same trade the 48-hex ``SET_COLORS`` list already makes.

Drift here is also self-announcing rather than silent: a renamed file makes its
declared row go ``missing`` and the real file appear under
:func:`unclaimed_files`, which is a visible pair, not a quiet wrong answer.

**These rows are protected.** The folder answers 409 to ``DELETE`` and every
shelf verb refuses them, because they are ours: renaming our own tagger would
make the shelf lie about it, and assigning a tagger to a character means
nothing. They are on the shelf for completeness — so the owner can see what is
on their disk and what it costs — not to be curated.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.adapter_header import FILE_ENGINE

logger = get_logger(__name__)

# The folder PixlStash downloads its own engines into. `foreign` rather than
# `managed`: `managed` is the ONE store the owner may drop models into and
# relocate as their own, and there is exactly one of those. This is ours.
BUILTIN_KIND = "foreign"
BUILTIN_OWNER = "pixlstash"

# How a declared folder moves, which is a statement about the folder rather than
# a permission. `root_only` means "if it relocates, it relocates whole" — true of
# PixlStash's own downloads and of the InsightFace packs, whichever of them has a
# relocate route yet. `fixed` means it cannot be relocated at all because
# something else owns where it lives: the HuggingFace cache's location is
# `HF_HOME`, read at import by a library shared with every other tool on the
# machine, so "moving" it is a restart and a re-download rather than a move.
MOVABLE_ROOT_ONLY = "root_only"
MOVABLE_FIXED = "fixed"

# Everything in this folder arrived because PixlStash fetched it.
BUILTIN_PROVENANCE = "builtin"

# `hf_hub_download(local_dir=...)` leaves its own bookkeeping beside the files it
# writes, at the top level and again inside every subdirectory it fills. It is
# HuggingFace's, not ours and not the owner's, so it is neither declared nor
# reported as unclaimed — it is simply not a model file.
TOOLING_DIRS = (".cache",)


@dataclass(frozen=True)
class BuiltinEngine:
    """One engine PixlStash downloads, and the files it owns.

    Attributes:
        key: Stable identifier, used as the row's filename-independent identity.
        display_name: What the shelf calls it.
        role: What it does — ``tagger``, ``captioner``, ``scorer``, ``face``.
            Stored in ``model.kind``, which already holds free text (``lora``,
            ``lokr``) and already renders as the row's label, so ``file_kind``
            stays a four-value vocabulary instead of growing one entry per role.
        relpath: The engine's own file, relative to the folder. This is what the
            shelf shows and what its size is read from.
        companions: Files that belong to the engine but are not it — a label
            set, a revision sidecar. They get no row of their own and are not
            reported as unclaimed.
    """

    key: str
    display_name: str
    role: str
    relpath: str
    companions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def owned(self) -> tuple[str, ...]:
        """Every relative path this engine accounts for."""
        return (self.relpath, *self.companions)


@dataclass(frozen=True)
class DeclaredEntry:
    """One row to write, with its existence already resolved.

    The writer below takes these rather than reaching for the disk itself, so
    the caller decides what "present" and "how big" mean for its own root. That
    matters because the three roots answer those questions in three different
    ways: an engine is one `stat` of one file, an InsightFace pack is the sum of
    a directory, and a HuggingFace repo is a number its own cache index already
    holds. Only the writing is common, so only the writing is shared.

    Attributes:
        relpath: Location within the folder — `model_file`'s own identity.
        display_name: What the shelf calls it.
        role: Stored in ``model.kind``; see :class:`BuiltinEngine.role`.
        size: Bytes, or None when it could not be read. None never overwrites a
            size already recorded.
        present: Whether it is on disk now. False writes ``missing``, which is a
            normal state here and not a warning.
        capabilities: Every feature these weights serve, primary first, written
            to ``model_capability``. Empty means "just the role", which is what
            all but one caller means: a model that does one thing does not have
            to say it twice.
    """

    relpath: str
    display_name: str
    role: str
    size: Optional[int]
    present: bool
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    @property
    def declared_capabilities(self) -> tuple[str, ...]:
        """The capability set to write — never empty, `role` first."""
        return self.capabilities or (self.role,)


# Mirrors `pixlstash_tagger.PIXLSTASH_TAGGER_FILENAME` /
# `..._META_FILENAME`, `wd14.WD14_CSV_FILE`, and
# `ImageEmbeddingTask.AESTHETIC_MODELS`. Pinned by tests/test_builtin_models.py.
BUILTIN_ENGINES: tuple[BuiltinEngine, ...] = (
    BuiltinEngine(
        key="pixlstash-anomaly-tagger",
        display_name="PixlStash anomaly tagger",
        role="tagger",
        relpath="pixlstash-anomaly-tagger.safetensors",
        # The meta file carries the label set; the revision sidecar is written
        # by our own code rather than by the download, and `needs_download()`
        # reads it to decide whether the pinned revision has moved.
        companions=(
            "pixlstash-anomaly-tagger_meta.json",
            "pixlstash-anomaly-tagger.revision",
        ),
    ),
    BuiltinEngine(
        key="wd14-convnext-tagger-v3",
        display_name="WD14 ConvNeXt tagger v3",
        role="tagger",
        relpath=os.path.join("SmilingWolf_wd-convnext-tagger-v3", "model.onnx"),
        companions=(
            os.path.join("SmilingWolf_wd-convnext-tagger-v3", "selected_tags.csv"),
        ),
    ),
    BuiltinEngine(
        key="aesthetic-vit-b-32",
        display_name="Aesthetic scorer (ViT-B/32)",
        role="scorer",
        relpath="sa_0_4_vit_b_32_linear.pth",
    ),
    BuiltinEngine(
        key="aesthetic-vit-l-14",
        display_name="Aesthetic scorer (ViT-L/14)",
        role="scorer",
        relpath="sac+logos+ava1-l14-linearMSE.pth",
    ),
)


# Lets a test point the declaration at a temp folder. Without it a Server built
# on a temp config dir still declares rows about the developer's REAL home, so
# the shelf's contents depend on which engines that machine happens to have
# downloaded — which is how `test_workers_api` came to assert `3 == 0` on a
# runner whose model cache was warm.
BUILTIN_MODEL_DIR_ENV = "PIXLSTASH_BUILTIN_MODEL_DIR"


def builtin_model_dir() -> str:
    """Where PixlStash downloads its engines.

    Machine-global on purpose: one download serves every library and every
    server instance on the host, exactly as the hub itself does.

    **The same expression the downloaders build, not a shared one.**
    `inference/engine.py` and `image_embedding_task.py` each compute
    `user_data_dir("pixlstash")/downloaded_models` for themselves, and an earlier
    version of this docstring claimed being "in one place" meant the declaration
    could not point somewhere the downloaders do not fill. It does not — the
    three agree because they spell the same thing, which is a convention rather
    than a guarantee.

    That matters because of the override below, which redirects **only this
    function**. That is exactly what the test suite wants: point the declaration
    at an empty directory so a Server built on a temp config does not describe
    the developer's real home, while nothing redirects downloads that the tests
    never make. It is a test seam and is not safe as a way to relocate the
    store — set it in production and the shelf would declare an empty folder
    while the engines kept landing in the real one. Relocating for real is
    `POST /model-folders/{id}/relocate`, which moves the files too.

    Making all three share this function is the right end state; it needs
    `ImageEmbeddingTask.AESTHETIC_MODELS` to stop being built at import time,
    which is a change to the download path and not to this one.
    """
    override = os.environ.get(BUILTIN_MODEL_DIR_ENV, "").strip()
    if override:
        return override

    from platformdirs import user_data_dir

    return os.path.join(user_data_dir("pixlstash"), "downloaded_models")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def declared_paths() -> set[str]:
    """Every relative path the engines above account for."""
    return {path for engine in BUILTIN_ENGINES for path in engine.owned}


def unclaimed_files(folder_path: str) -> list[dict]:
    """Files present in the folder that no declaration accounts for.

    **Not "orphaned".** We know our own manifest; we do not know that a previous
    build, a plugin or the owner did not put a file there deliberately. This
    reports what nothing in *this build* claims, which is a smaller and true
    statement — the same distinction the scan already draws between ``missing``
    (we looked and it was not there) and ``unreachable`` (we could not look).

    Detection proposes and never applies: nothing here deletes.

    Args:
        folder_path: The built-in folder to inspect.

    Returns:
        ``{"relpath", "size"}`` per unclaimed file, smallest path first. Empty
        when the folder does not exist, which is the normal state before the
        first download.
    """
    declared = declared_paths()
    found: list[dict] = []
    for directory, dirs, files in os.walk(folder_path):
        dirs[:] = [d for d in dirs if d not in TOOLING_DIRS]
        for name in files:
            absolute = os.path.join(directory, name)
            relpath = os.path.relpath(absolute, folder_path)
            if relpath in declared:
                continue
            try:
                size = os.path.getsize(absolute)
            except OSError as exc:
                logger.warning(
                    "Cannot size %r while listing unclaimed built-in files (%s); "
                    "reporting it without one.",
                    absolute,
                    exc,
                )
                size = 0
            found.append({"relpath": relpath, "size": size})
    return sorted(found, key=lambda item: item["relpath"])


def declare_builtin_models(hub, folder_path: str) -> Optional[int]:
    """Register the built-in folder and write a row per engine present.

    Runs at start-up beside ``ensure_managed_folder``. Idempotent: it upserts
    the folder, upserts one ``model`` row per engine, and stamps each engine's
    ``model_file`` state from a plain existence check.

    **The folder scanner must skip this folder**, which is why it carries an
    ``owner``. The scanner yields only ``.safetensors`` and sweeps whatever it
    did not see to ``missing``; pointed here it would mark the ONNX tagger and
    both ``.pth`` scorers missing on every pass.

    Args:
        hub: The open hub database.
        folder_path: Where PixlStash downloads its engines.

    Returns:
        The ``model_folder.id``, or ``None`` if the row could not be written.
    """
    entries = []
    for engine in BUILTIN_ENGINES:
        absolute = os.path.join(folder_path, engine.relpath)
        # One `stat` rather than `isfile` then `getsize`. The pair is a race
        # on the one directory the downloaders are actively writing into: a
        # file that arrives or is replaced between the two calls makes
        # `getsize` raise `OSError` on a path `isfile` just confirmed, and
        # that would abort the declaration for every engine after it.
        try:
            size = os.stat(absolute).st_size
            present = True
        except OSError:
            size = None
            present = False
        entries.append(
            DeclaredEntry(
                relpath=engine.relpath,
                display_name=engine.display_name,
                role=engine.role,
                size=size,
                present=present,
            )
        )
    return declare_folder(hub, folder_path, entries)


def declare_folder(
    hub, folder_path: str, entries, movable: str = MOVABLE_ROOT_ONLY
) -> Optional[int]:
    """Upsert one PixlStash-owned folder and a row per declared entry.

    Shared by all three roots PixlStash owns: the engines it downloads, the
    InsightFace packs, and the HuggingFace cache. The caller resolves what is
    there; this writes it.

    Idempotent, and the declaration is the authority — a second call restates
    every field rather than merging, because unlike a scanned row there is no
    owner curation here to preserve.

    Args:
        hub: The open hub database.
        folder_path: The root being declared.
        entries: The :class:`DeclaredEntry` rows to write under it.
        movable: :data:`MOVABLE_ROOT_ONLY` (the default) or
            :data:`MOVABLE_FIXED` for a root whose location another tool owns.

    Returns:
        The ``model_folder.id``, or ``None`` if the row could not be written.
    """
    now = _utcnow()
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model_folder (path, kind, owner, movable, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            # `movable` is re-asserted with the rest. A path the owner had
            # already registered as a `user` folder keeps its own
            # `movable` otherwise, so claiming it for PixlStash would
            # leave the built-in folder advertising `per_item` — the
            # engines individually movable, which is exactly what the
            # protection exists to prevent.
            "ON CONFLICT(path) DO UPDATE SET kind = excluded.kind, "
            "owner = excluded.owner, movable = excluded.movable",
            (folder_path, BUILTIN_KIND, BUILTIN_OWNER, movable, now),
        )
        row = conn.execute(
            "SELECT id FROM model_folder WHERE path = ?", (folder_path,)
        ).fetchone()
        if row is None:
            logger.error(
                "Built-in model folder %r vanished between write and read; the "
                "shelf will not list PixlStash's own engines this session.",
                folder_path,
            )
            return None
        folder_id = int(row[0])

        for entry in entries:
            size = entry.size
            state = "present" if entry.present else "missing"
            # An engine that has not been downloaded yet is NOT an error and not
            # a warning: the ViT-L/14 scorer is fetched only for the CLIP model
            # that needs it, so "declared and absent" is the normal state for
            # about half of these on any given machine.
            #
            # Identity is the LOCATION — `model_file`'s own primary key — not a
            # hash we would have to read 339 MB to compute, and not `run_key`,
            # which belongs to ai-toolkit runs and is COALESCE'd by the run
            # importer.
            existing = conn.execute(
                "SELECT model_id FROM model_file "
                "WHERE model_folder_id = ? AND relpath = ?",
                (folder_id, entry.relpath),
            ).fetchone()
            if existing is None:
                cursor = conn.execute(
                    "INSERT INTO model (file_kind, kind, display_name, filename, "
                    "provenance, file_size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        FILE_ENGINE,
                        entry.role,
                        entry.display_name,
                        os.path.basename(entry.relpath),
                        BUILTIN_PROVENANCE,
                        size,
                        now,
                    ),
                )
                model_id = int(cursor.lastrowid)
                conn.execute(
                    "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                    "state, seen_at) VALUES (?, ?, ?, ?, ?)",
                    (model_id, folder_id, entry.relpath, state, now),
                )
            else:
                model_id = int(existing[0])
                # The declaration is the authority for what this row IS, so it
                # is written outright rather than COALESCE'd: unlike a scanned
                # row there is no owner curation here to preserve.
                conn.execute(
                    "UPDATE model SET file_kind = ?, kind = ?, display_name = ?, "
                    "file_size = COALESCE(?, file_size) WHERE id = ?",
                    (FILE_ENGINE, entry.role, entry.display_name, size, model_id),
                )
                conn.execute(
                    "UPDATE model_file SET state = ?, seen_at = ? "
                    "WHERE model_folder_id = ? AND relpath = ?",
                    (state, now, folder_id, entry.relpath),
                )

            # Both branches, and restated wholesale for the same reason `kind`
            # is: a capability the declaration no longer claims must go, or a
            # model that stopped serving a feature would still be listed under
            # it. Two rows at most, so a diff would be more code than the
            # rewrite it saves.
            conn.execute("DELETE FROM model_capability WHERE model_id = ?", (model_id,))
            conn.executemany(
                "INSERT INTO model_capability (model_id, capability) VALUES (?, ?)",
                [(model_id, capability) for capability in entry.declared_capabilities],
            )
        # The sweep, and these folders have nowhere else to get one. The folder
        # scanner does this pass for every folder it walks — anything it did not
        # see this run goes `missing` — and it skips these precisely because
        # they carry an `owner`, so without this a row here could never stop
        # being `present`.
        #
        # It is a no-op for the built-in engines, whose entry set is a fixed
        # tuple and always names every row. It exists for the DISCOVERED roots:
        # `huggingface-cli delete-cache` drops a repo out of the index and
        # deleting an InsightFace pack drops it out of the listing, and the row
        # left behind would otherwise claim its bytes are still on the disk
        # forever — inflating the very `present_bytes` figure the folder list
        # reports.
        #
        # `seen_at <` the run's own stamp rather than `!=`, the same predicate
        # the scanner uses, so a concurrent declaration that stamped a later
        # time cannot have its rows swept by this one.
        conn.execute(
            "UPDATE model_file SET state = 'missing' "
            "WHERE model_folder_id = ? AND seen_at < ? AND state <> 'missing'",
            (folder_id, now),
        )
        conn.execute(
            "UPDATE model_folder SET last_checked = ? WHERE id = ?", (now, folder_id)
        )
    return folder_id
