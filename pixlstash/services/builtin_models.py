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

    The same expression `inference/engine.py` and `image_embedding_task.py`
    build for themselves, in one place so the declaration cannot point at a
    different folder than the downloaders fill.

    Machine-global on purpose: one download serves every library and every
    server instance on the host, exactly as the hub itself does.
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
    now = _utcnow()
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model_folder (path, kind, owner, movable, created_at) "
            "VALUES (?, ?, ?, 'root_only', ?) "
            "ON CONFLICT(path) DO UPDATE SET kind = excluded.kind, "
            "owner = excluded.owner",
            (folder_path, BUILTIN_KIND, BUILTIN_OWNER, now),
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

        for engine in BUILTIN_ENGINES:
            absolute = os.path.join(folder_path, engine.relpath)
            present = os.path.isfile(absolute)
            size = os.path.getsize(absolute) if present else None
            state = "present" if present else "missing"
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
                (folder_id, engine.relpath),
            ).fetchone()
            if existing is None:
                cursor = conn.execute(
                    "INSERT INTO model (file_kind, kind, display_name, filename, "
                    "provenance, file_size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        FILE_ENGINE,
                        engine.role,
                        engine.display_name,
                        os.path.basename(engine.relpath),
                        BUILTIN_PROVENANCE,
                        size,
                        now,
                    ),
                )
                model_id = int(cursor.lastrowid)
                conn.execute(
                    "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                    "state, seen_at) VALUES (?, ?, ?, ?, ?)",
                    (model_id, folder_id, engine.relpath, state, now),
                )
                continue

            model_id = int(existing[0])
            # The declaration is the authority for what this row IS, so it is
            # written outright rather than COALESCE'd: unlike a scanned row
            # there is no owner curation here to preserve.
            conn.execute(
                "UPDATE model SET file_kind = ?, kind = ?, display_name = ?, "
                "file_size = COALESCE(?, file_size) WHERE id = ?",
                (FILE_ENGINE, engine.role, engine.display_name, size, model_id),
            )
            conn.execute(
                "UPDATE model_file SET state = ?, seen_at = ? "
                "WHERE model_folder_id = ? AND relpath = ?",
                (state, now, folder_id, engine.relpath),
            )
        conn.execute(
            "UPDATE model_folder SET last_checked = ? WHERE id = ?", (now, folder_id)
        )
    return folder_id
