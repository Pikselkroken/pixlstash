"""Registering the folders the model shelf catalogues.

A ``model_folder`` is a **hub** row: a folder of LoRAs is a fact about this disk,
and re-registering the same folder in every library would be absurd. So these
routes read and write the hub directly rather than going through the vault.

**Removing a folder is a tombstone, not a deletion.** The folder's ``model_file``
rows go and the ``model`` rows stay, with the display name, triggers, corrected
``file_kind`` and vault-side attachments the owner gave them intact. Re-adding the
folder re-links by content on the next scan. That is precisely what lets folder
removal skip a confirmation prompt (shelf plan §7): nothing a person typed is
destroyed by it. If this ever hard-deletes ``model`` rows, the confirmation comes
back.

**Forgetting takes the machine-wide** ``SHELF_IO_LOCK`` **and 409s if it is
held** (#1017). It is harmless to the models, but it deletes exactly the
``model_file`` rows a running move or import is writing, and a move whose source
row disappeared mid-flight would unlink the source with nothing left registering
the copy it had just made.

**It is a slot, not a general exclusion, and the mover is what actually
guarantees the invariant.** A scan writes the same rows and is deliberately
*outside* this lock (backend architecture §"MODEL_FOLDER_SCAN"), and a
multi-run import is a sequence of separate lock-taking requests, so a forget can
still land between two of them. What closes the hole in every case is
``ModelMover._repoint`` refusing a commit that does not move exactly one row.
This half stops the *move-versus-forget* interleaving the report was about, and
turns a file failed halfway through a 40-file batch into a clean 4xx before the
batch starts.

**Exactly one ``managed`` folder always exists and cannot be forgotten.** It
is PixlStash's own model storage - created on first run, the default destination
for a drop or an import - so there is no association to dissolve and ``DELETE``
answers 409. ``user`` and ``foreign`` folders may legitimately number zero; that
is a normal state, not an error. See
:mod:`pixlstash.services.managed_model_store`.

**One folder may be the Downloads folder** (``kind = 'downloads'``). It is
taken from like a ``source`` folder - never scanned, never a destination - and
:mod:`pixlstash.services.downloads_intake` moves the models that land in it into
the default folder. There is one, because "where my browser saves" is one place;
a second registration answers 409.

**One folder may be the default** (``is_default``, set through ``PATCH``): where
``Add file`` and the Downloads folder put a model when nothing else is named.
Only a folder the shelf catalogues and writes into can be it (``user`` or the
managed store), at most one is by a partial unique index, and with none marked
the managed store is the default, so ``is_default`` is reported as the effective
answer rather than the stored flag.

**Two of the four columns are derived, not asked for.** ``movable`` and ``owner``
follow from ``kind``, and offering them as inputs would let a caller register a
combination that means nothing (an ``external``-movable ``user`` folder). Only
``user`` and ``source`` are creatable over HTTP: ``managed`` and ``foreign``
describe locations PixlStash registers for itself (tagger artifacts, the
InsightFace root, the HuggingFace cache), and a hand-made row of either kind would
collide with that registration.

**Nobody scans by hand.** Registering a catalogued folder queues its first
:class:`~pixlstash.tasks.model_folder_scan_task.ModelFolderScanTask` at once,
and :class:`~pixlstash.tasks.model_folder_scan_finder.ModelFolderScanFinder`
rescans it whenever a model file in it changes and every ten minutes besides.
The walk's per-file progress is in ``GET /workers/progress`` →
``workers.ModelFolderScanTask`` and its outcome is the folder's ``scan_status``.

Authorization: the read is ``OWNER_ONLY``; every mutator is
``LOCAL_OWNER_ONLY`` with a §16.3 justification, because they take - or walk - a
caller-supplied host path. That is the same tier and the same reason as the
``reference-folders`` block. ``POST /model-folders/{folder_id}/open`` spawns the
host's file manager and is ``LOOPBACK_OWNER_ONLY`` (§16.3.1), as
``POST /models/{model_id}/open-location`` is. Declared in
``pixlstash/authz/registry.py``, never inline.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Body, HTTPException, Query, Request
from platformdirs import user_config_dir
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services.builtin_caches import huggingface_cache_dir
from pixlstash.services.builtin_models import BUILTIN_OWNER
from pixlstash.services import comfyui_link_service, model_downloads
from pixlstash.services.comfyui_service import (
    comfyui_folder_paths,
    comfyui_model_roots,
)
from pixlstash.services.downloads_intake import (
    IN_FLIGHT_SUFFIXES,
    safetensors_complete,
)
from pixlstash.services.managed_model_store import (
    MANAGED_KIND,
    deletes_unclaimed_files,
    find_default_folder,
    find_download_folder,
    relocatable_identity,
)
from pixlstash.services.model_folder_scanner import (
    _LIKE_ESCAPE,
    DOWNLOADS_FOLDER_KIND,
    SHELF_MODEL_SUFFIXES,
    TAKEN_FROM_KINDS,
    ModelFolderScanner,
)
from pixlstash.services.model_mover import SHELF_IO_LOCK, MoveRefused, resolve_subfolder
from pixlstash.tasks.base_task import TaskPriority
from pixlstash.tasks.model_folder_scan_finder import scanned_folders
from pixlstash.tasks.model_folder_scan_task import (
    claim_scan,
    forget_scan,
    release_scan,
    scan_state,
)
from pixlstash.utils.adapter_header import FILE_ENGINE
from pixlstash.utils.host_open import open_in_file_manager
from pixlstash.utils.library_layout import refusal_for_folder_name
from pixlstash.utils.host_path_utils import is_absolute_host_path, normalize_host_path
from pixlstash.utils.path_utils import path_is_within, resolve_path_within
from pixlstash.utils.reference_folder_validator import (
    data_folder_conflict_detail,
    validate_reference_folder_accessible,
    validate_reference_folder_path,
)
from pixlstash.utils.system_utils import describe_storage_device

logger = get_logger(__name__)

# The folder kinds a caller may register. ``managed`` and ``foreign`` are
# PixlStash's own (tagger artifacts, InsightFace, the HuggingFace cache) and are
# registered by the code that owns them.
CREATABLE_KINDS = ("user", "source", DOWNLOADS_FOLDER_KIND)

# ``movable`` and ``owner`` follow from ``kind``: a user folder holds files that
# can each be moved individually; a source folder is an ai-toolkit output root
# and the downloads folder is where the browser saves, both taken FROM and never
# catalogued in place.
_DERIVED_BY_KIND = {
    "user": ("per_item", None),
    "source": ("external", "ai-toolkit"),
    DOWNLOADS_FOLDER_KIND: ("external", None),
}

# The kinds a model can be put into by default: catalogued, and the owner's to
# write into. Never a taken-from kind, and never PixlStash's own `foreign` roots.
DEFAULT_ELIGIBLE_KINDS = ("user", MANAGED_KIND)

# How long a file must sit unchanged before a pick-up takes it (#1985). A
# workflow names a missing file exactly while it is downloading, and `curl -O`
# writes straight to the final name; the Downloads intake waits the same way.
PICK_UP_SETTLE_S = 10.0


def _stability_matrix_roots() -> list[str]:
    """The resolved folders StabilityMatrix keeps its library and models in.

    StabilityMatrix writes where its library lives to ``library.json`` in its
    own config folder. ``Models`` is a root of its own as well as the library,
    because moving it to a bigger drive behind a symlink is common and a
    registered folder is stored at its resolved path, so the library alone
    would not contain it. The folder is the one ``platformdirs`` names for
    StabilityMatrix's app data, checked on Linux only; a portable install
    keeps its settings beside the app instead, so its folders go unlabelled.

    Returns:
        Zero, one or two resolved paths.
    """
    pointer = os.path.join(
        user_config_dir("StabilityMatrix", appauthor=False, roaming=True),
        "library.json",
    )
    if not os.path.isfile(pointer):
        return []
    try:
        with open(pointer, encoding="utf-8") as handle:
            library = json.load(handle).get("LibraryPath")
    except (OSError, ValueError, AttributeError) as exc:
        logger.warning(
            "Could not read StabilityMatrix's library location from %s (%s); "
            "its model folders will not be labelled as StabilityMatrix's.",
            pointer,
            exc,
        )
        return []
    # StabilityMatrix writes an absolute path. Anything else would be resolved
    # against this server's working directory and label the wrong folders.
    if not isinstance(library, str) or not os.path.isabs(library.strip()):
        return []
    library = os.path.realpath(library.strip())
    return [library, os.path.realpath(os.path.join(library, "Models"))]


def _folder_tool(
    server, row: dict, stability_matrix_roots: Optional[list[str]] = None
) -> Optional[str]:
    """Which tool the folder belongs to, as the folders dialog labels it.

    Args:
        server: The Server instance, for ``hub``.
        row: The ``model_folder`` row.
        stability_matrix_roots: :func:`_stability_matrix_roots`, read once by a
            caller answering for every folder; read here when omitted.
    """
    if row["kind"] == "source":
        return row["owner"]
    if row["kind"] == DOWNLOADS_FOLDER_KIND:
        # The owner's own Downloads folder; the dialog labels it by its kind.
        return None
    if row["kind"] == "user":
        if stability_matrix_roots is None:
            stability_matrix_roots = _stability_matrix_roots()
        if any(path_is_within(row["path"], root) for root in stability_matrix_roots):
            return "StabilityMatrix"
        return None
    cache = huggingface_cache_dir()
    if cache and os.path.realpath(row["path"]) == os.path.realpath(cache):
        # Shared with every other tool on the machine; it is only ours while it
        # holds repos and every one is a repo PixlStash downloaded for itself.
        held = server.hub.fetchone(
            "SELECT COUNT(*) AS n, COALESCE(SUM(m.file_kind != ?), 0) AS others "
            "FROM model_file mf JOIN model m ON m.id = mf.model_id "
            "WHERE mf.model_folder_id = ? AND mf.state = 'present'",
            (FILE_ENGINE, row["id"]),
        )
        if not held["n"] or held["others"]:
            return "HuggingFace"
    return "PixlStash"


class ModelFolderCreateRequest(BaseModel):
    """Body of ``POST /model-folders``."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        description="Absolute host path to register. Owner-chosen and therefore trusted."
    )
    kind: str = Field(
        default="user",
        description=(
            "`user` for a folder to catalogue in place, `source` for an "
            "ai-toolkit output root that is scanned for importable runs instead, "
            "`downloads` for the one folder whose new models are moved into the "
            "default folder."
        ),
    )
    host_path: Optional[str] = Field(
        default=None,
        description="Docker bind source, for the same reason import folders carry one.",
    )
    delete_after_import: bool = Field(
        default=False,
        description="`source` folders only: remove the run's files once imported.",
    )


class ModelFolderUpdateRequest(BaseModel):
    """Body of ``PATCH /model-folders/{folder_id}``.

    Deliberately narrow. Changing ``path`` is a relocation (B7, which must copy,
    verify and only then unlink), and changing ``kind`` changes what the folder
    *is* - neither is a field edit.
    """

    model_config = ConfigDict(extra="forbid")

    host_path: Optional[str] = None
    delete_after_import: Optional[bool] = None
    is_default: Optional[bool] = Field(
        default=None,
        description=(
            "`true` makes this the default folder (the previous one stops being "
            "it); `false` hands the role back to the managed store. A `user` "
            "folder or the managed store only."
        ),
    )
    is_download_target: Optional[bool] = Field(
        default=None,
        description=(
            "`true` makes this the folder a model PixlStash downloads goes into "
            "(the previous one stops being it); `false` hands the role back to "
            "the default folder. A `user` folder or the managed store only."
        ),
    )


class ModelFolderRenameRequest(BaseModel):
    """Body of ``POST /model-folders/{folder_id}/rename``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description=(
            "The folder's new name, one segment; it stays in the same parent "
            "directory. A name the drive cannot hold is refused with the "
            "reason, never rewritten."
        )
    )


class ModelFolderResponse(BaseModel):
    """One registered folder, with what the shelf holds in it."""

    model_config = ConfigDict(extra="allow")

    id: int
    path: str
    kind: str = Field(
        description="`user`, `managed`, `foreign`, `source` or `downloads`."
    )
    owner: Optional[str] = Field(
        default=None,
        description="Which subsystem owns the folder; null for a folder the user chose.",
    )
    tool: Optional[str] = Field(
        default=None,
        description=(
            "The tool whose folder this is, as the folders dialog labels it: "
            "`PixlStash`, `HuggingFace` (the cache, unless every repo in it "
            "is one PixlStash downloaded), `ai-toolkit`, `StabilityMatrix` (a "
            "folder inside its library or its models folder, symlinked or "
            "not), or null for a folder that is simply the owner's."
        ),
    )
    movable: str = Field(
        description=(
            "`per_item` (files move one at a time), `root_only` (relocates as a "
            "whole), `external` (taken from, never written into) or `fixed` "
            "(cannot relocate at all; another tool owns where it lives). What "
            "the folder *is*; read `relocatable` for what can be done to it."
        )
    )
    relocatable: bool = Field(
        default=False,
        description=(
            "Whether `POST /model-folders/{id}/relocate` will move this folder "
            "whole. Reported rather than derived by the client, because "
            "`movable=root_only` does not settle it: it is also what the "
            "HuggingFace cache's neighbours say, and the two roots PixlStash "
            "records a location for are told apart from each other by path, not "
            "by any column. Offer Move on exactly the rows that carry this."
        ),
    )
    deletable: bool = Field(
        default=False,
        description=(
            "Whether `POST /model-files/delete` will unlink a file PixlStash "
            "does not claim from this folder: a folder you registered, the managed "
            "store, and the folder PixlStash downloads its engines into, whose "
            "leftovers are yours (#927). False for the HuggingFace cache, a "
            "symlink store shared with every other tool on the machine, and for "
            "the InsightFace packs. Told apart by path for the same reason "
            "`relocatable` is. An **engine** is refused wherever it lives, so "
            "this is the folder half of the answer and `file_kind` is the other."
        ),
    )
    host_path: Optional[str] = None
    delete_after_import: Optional[bool] = None
    is_default: bool = Field(
        default=False,
        description=(
            "Whether a new model goes here when nothing else is named: `Add "
            "file`, and a model moved in from the Downloads folder. Exactly one "
            "row says true while the managed store is registered: the one the "
            "owner marked, else the managed store."
        ),
    )
    is_download_target: bool = Field(
        default=False,
        description=(
            "Whether a model PixlStash downloads for a workflow goes here. "
            "Exactly one row says true while a default folder exists: the one "
            "the owner marked, else the default folder."
        ),
    )
    last_checked: Optional[str] = Field(
        default=None,
        description="When the scanner last completed a pass. Null if never.",
    )
    created_at: Optional[str] = None
    file_count: int = Field(
        default=0,
        description=(
            "Copies registered under this folder, in any state. Counted in one "
            "grouped query for the whole list, never one per folder."
        ),
    )
    present_bytes: int = Field(
        default=0,
        description=(
            "Bytes registered under this folder whose copies are `present`, in "
            "the same grouped query the capacity meter uses. `missing` and "
            "`unreachable` copies are excluded: one names bytes that are no "
            "longer there and the other bytes we could not look at, and "
            "counting either would report space the drive does not agree is in "
            "use. Zero is therefore a real answer, not an unknown one."
        ),
    )
    scan_status: Optional[str] = Field(
        default=None,
        description=(
            "State of the most recent scan of this folder since the server "
            "started: `pending`, `running`, `completed`, `failed` or "
            "`cancelled`. Scans run by themselves. Null when this server has not "
            "scanned the folder yet - which is not the same as never having "
            "scanned it, so "
            "read `last_checked` for that. Poll this rather than watching "
            "`last_checked` advance: a scan that threw never stamps "
            "`last_checked`, so a timestamp cannot tell a crash from a slow read."
        ),
    )
    scan_error: Optional[str] = Field(
        default=None,
        description="Why the most recent scan failed. Null unless `scan_status` is `failed`.",
    )


class ModelFolderListResponse(BaseModel):
    """Body of ``GET /model-folders``."""

    model_config = ConfigDict(extra="allow")

    folders: list[ModelFolderResponse]


class ModelFolderDeviceResponse(BaseModel):
    """One drive the registered folders sit on."""

    model_config = ConfigDict(extra="allow")

    device_id: Optional[str] = Field(
        default=None,
        description=(
            "Opaque key for the filesystem, grouping the folders that share a "
            "drive. Null when the drive could not be measured, and then the "
            "entry covers exactly one folder, because two folders we cannot "
            "stat cannot be shown to be the same drive."
        ),
    )
    mount_point: str = Field(
        description=(
            "Where the filesystem is mounted (`/`, `/mnt/models`, `D:\\`). "
            "Precise, and on Linux long enough to crowd a band header, so it "
            "is the drive band's tooltip rather than its label. Falls back to "
            "the folder's own path when the drive could not be measured."
        )
    )
    label: Optional[str] = Field(
        default=None,
        description=(
            "What the owner called the volume (`Models`, `WinStorage`), read "
            "from `/dev/disk/by-label` on Linux, `GetVolumeInformationW` on "
            "Windows and the `/Volumes` mount name on macOS. Null when the "
            "filesystem carries no label, which a root partition usually does "
            "not; the band then shows `mount_point` instead."
        ),
    )
    total_bytes: Optional[int] = Field(
        default=None, description="Size of the filesystem. Null if unmeasurable."
    )
    free_bytes: Optional[int] = Field(
        default=None,
        description=(
            "Room left on the filesystem, which is the number to read before "
            "moving a 24 GB checkpoint onto it. Null if unmeasurable."
        ),
    )
    kind: Optional[str] = Field(
        default=None,
        description=(
            "What kind of storage this is: `local`, `network`, `removable` or "
            "`ramdisk`. The CONNECTION, not the medium - it says whether the "
            "bytes are on another machine, on a stick, or in memory, which is "
            "what changes how fast a move runs and whether it survives a "
            "reboot. Null where the platform will not say (macOS always, and "
            "any filesystem type we do not recognise), and null is a normal "
            "answer: the drive band then draws its plain disk glyph rather "
            "than claiming the drive is unknown. Deliberately does NOT "
            "distinguish an SSD from a platter - the flags that would are "
            "wrong in a VM, behind LVM or LUKS, and in a USB enclosure, and a "
            "band that mislabels a slow disk as fast is worse than one that "
            "says nothing."
        ),
    )
    shelf_bytes: int = Field(
        default=0,
        description=(
            "Bytes of registered, `present` copies on this drive. A model held "
            "in two folders on one drive counts twice, because it occupies the "
            "drive twice."
        ),
    )
    folder_ids: list[int] = Field(
        default_factory=list,
        description="Registered folders on this drive, in id order.",
    )


class ModelFolderDeviceListResponse(BaseModel):
    """Body of ``GET /model-folders/devices``."""

    model_config = ConfigDict(extra="allow")

    devices: list[ModelFolderDeviceResponse]


class ModelFolderDirResponse(BaseModel):
    """One subdirectory of the listed directory."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(description="The directory's name, as it sits on disk.")
    path: str = Field(
        description=(
            "POSIX path of this directory relative to the folder, ready to pass "
            "back as the next `path`. The bare name when the listed path is the "
            "folder root."
        )
    )
    model_count: int = Field(
        description=(
            "Distinct models with a `present` copy anywhere under this directory, "
            "however deep."
        )
    )
    direct_model_count: int = Field(
        description=(
            "Distinct models with a `present` copy directly in this directory, "
            "not in a subdirectory of it."
        )
    )
    bytes: int = Field(
        description=(
            "Sum of the sizes of the `present` copies under this directory, "
            "recursively. A model held twice inside it counts twice."
        )
    )
    has_dirs: bool = Field(
        description=(
            "Whether this directory holds at least one subdirectory the tree "
            "would show, so the client knows to draw an expand arrow before it "
            "asks."
        )
    )


class ModelFolderDirsResponse(BaseModel):
    """Body of ``GET /model-folders/{folder_id}/dirs``."""

    model_config = ConfigDict(extra="allow")

    folder_id: int
    path: str = Field(
        description=(
            "The directory listed, normalised: POSIX, relative to the folder, "
            "empty for the folder root."
        )
    )
    locked: bool = Field(
        description=(
            "True when the folder is not browsable (managed store, HuggingFace "
            "cache, InsightFace packs, source and downloads folders). Then `dirs` "
            "is empty and the folder's disk is not read; the counts still come "
            "from the database."
        )
    )
    model_count: int = Field(
        description="Distinct models with a `present` copy under `path`, recursively."
    )
    direct_model_count: int = Field(
        description="Distinct models with a `present` copy directly in `path`."
    )
    bytes: int = Field(
        description="Sum of the `present` copies' sizes under `path`, recursively."
    )
    dirs: list[ModelFolderDirResponse] = Field(
        default_factory=list,
        description="The subdirectories of `path`, sorted case-insensitively by name.",
    )


class ComfyuiReadResponse(BaseModel):
    """One directory ComfyUI loads one kind of model from."""

    model_config = ConfigDict(extra="allow")

    folder_id: int = Field(description="The registered folder it is in.")
    subpath: str = Field(
        description=(
            "POSIX, relative to the folder, empty for the folder itself. ComfyUI "
            "loads from everything under it."
        )
    )
    prefix: str = Field(
        default="",
        description=(
            "POSIX path of the registered folder under ComfyUI's own folder when "
            "the folder sits inside it, else empty. ComfyUI names a file by its "
            "path under its own folder, so this goes in front of it."
        ),
    )
    category: str = Field(description="ComfyUI's name for it, e.g. `loras`.")
    kind: str = Field(
        description=(
            "The shelf Type it loads: `lora`, `checkpoint`, `vae`, "
            "`text_encoder`, `upscaler` or `controlnet`."
        )
    )


class ComfyuiReadsResponse(BaseModel):
    """Body of ``GET /model-folders/comfyui-reads``."""

    model_config = ConfigDict(extra="allow")

    known: bool = Field(
        description=(
            "False when no read lands in a registered folder: no ComfyUI set, "
            "it did not answer, or it is on another machine. Nothing can then be "
            "said about where a model is misplaced."
        )
    )
    reads: list[ComfyuiReadResponse] = Field(default_factory=list)


# ComfyUI's model categories, as `/internal/folder_paths` names them, to the
# shelf Type each loads. `unet` and `diffusion_models` are `checkpoint`
# because the shelf files a bare diffusion model as one (`_ROLE_FOLDERS` in
# `utils/adapter_header.py`, `_SLOT_BY_DIRECTORY` in `workflow_templates.py`),
# so a UNet there is where ComfyUI looks for it.
COMFYUI_CATEGORY_KINDS = {
    "loras": "lora",
    "checkpoints": "checkpoint",
    "diffusion_models": "checkpoint",
    "unet": "checkpoint",
    "vae": "vae",
    "text_encoders": "text_encoder",
    "clip": "text_encoder",
    "upscale_models": "upscaler",
    "controlnet": "controlnet",
}


def comfyui_reads(folders: list[dict], folder_paths) -> list[dict]:
    """Where in the registered folders ComfyUI loads each kind from.

    A category path inside a registered folder reads from that subdirectory; a
    registered folder inside a category path is read whole. Containment is by
    realpath, so a symlinked folder still matches.

    Args:
        folders: rows with ``id`` and ``path``.
        folder_paths: ComfyUI's ``/internal/folder_paths`` answer.

    Returns:
        ``{folder_id, subpath, prefix, category, kind}`` per match, sorted.
    """
    if not isinstance(folder_paths, dict):
        return []
    roots = [(int(row["id"]), os.path.realpath(row["path"])) for row in folders]
    reads = set()
    for category, kind in COMFYUI_CATEGORY_KINDS.items():
        paths = folder_paths.get(category)
        for path in paths if isinstance(paths, list) else []:
            if not isinstance(path, str) or not path.strip():
                continue
            real = os.path.realpath(path)
            for folder_id, root in roots:
                if path_is_within(real, root):
                    sub = _posix_relpath(real, root)
                    reads.add((folder_id, sub, "", category, kind))
                elif path_is_within(root, real):
                    prefix = _posix_relpath(root, real)
                    reads.add((folder_id, "", prefix, category, kind))
    return [
        {"folder_id": f, "subpath": s, "prefix": p, "category": c, "kind": k}
        for f, s, p, c, k in sorted(reads)
    ]


def _posix_relpath(path: str, base: str) -> str:
    """*path* under *base*, POSIX, ``""`` for *base* itself."""
    rel = os.path.relpath(path, base).replace(os.sep, "/")
    return "" if rel == "." else rel


class ModelFolderDirCreateRequest(BaseModel):
    """Body of ``POST /model-folders/{folder_id}/dirs``."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        default="",
        description=(
            "The directory to create it in, POSIX and relative to the folder, "
            "empty for the folder's root. It must exist."
        ),
    )
    name: str = Field(
        description=(
            "The new directory's name, one segment. A name the drive cannot hold "
            "(a slash, a reserved Windows name such as `con`, a trailing dot) is "
            "refused with the reason, never rewritten."
        )
    )


class ModelFolderDirCreateResponse(BaseModel):
    """Body of a created directory."""

    model_config = ConfigDict(extra="allow")

    folder_id: int
    path: str = Field(
        description="The new directory's POSIX path relative to the folder."
    )


class ModelFolderOpenResponse(BaseModel):
    """Body of ``POST /model-folders/{folder_id}/open``."""

    model_config = ConfigDict(extra="allow")

    status: str = Field(
        description="`ok`: the opener was launched on the server's own desktop."
    )


_SAMPLES_SUFFIX = "_samples"


@dataclass
class _Subtree:
    """The `present` copies counted under one directory, recursively."""

    model_ids: set[int] = field(default_factory=set)
    direct_ids: set[int] = field(default_factory=set)
    bytes: int = 0


def _subpath_segments(raw: str) -> list[str]:
    """Split a ``path`` query value into segments, refusing anything not plain.

    ``""`` is the folder root. Every other value must be a relative POSIX path
    with no empty, ``.`` or ``..`` segment, so a leading or trailing slash is
    refused too. ``resolve_path_within`` still has the final word on the disk;
    this is the belt for the path string itself.
    """
    if raw == "":
        return []
    segments = raw.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise HTTPException(
            status_code=400,
            detail="path must be a relative folder path with no empty, '.' or '..' segments.",
        )
    return segments


def _is_samples_dir_of(name: str, sample_stems: set[str]) -> bool:
    """Whether *name* is the ``<stem>_samples`` directory of a model beside it."""
    return (
        name.endswith(_SAMPLES_SUFFIX) and name[: -len(_SAMPLES_SUFFIX)] in sample_stems
    )


def _offered_dir(entry: os.DirEntry, sample_stems: set[str]) -> bool:
    """Whether the tree shows *entry* as a subdirectory.

    Dot-names, symlinks and Windows junctions (``DirEntry.is_junction``, 3.12+;
    a junction is not a symlink to ``is_symlink``) are never offered. A bind
    mount is a directory to every API here and is offered, as the scanner walks
    it. A ``<stem>_samples`` directory is
    hidden beside a model whose filename stem is ``<stem>``: its previews belong
    to that model and are not a folder of their own.
    """
    if entry.name.startswith("."):
        return False
    if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
        return False
    if not entry.is_dir(follow_symlinks=False):
        return False
    return not _is_samples_dir_of(entry.name, sample_stems)


def _has_offered_dir(directory: str, sample_stems: set[str]) -> bool:
    """Whether *directory* holds at least one offered subdirectory.

    Raises:
        OSError: When the directory cannot be listed. The caller decides what
            that means for its answer.
    """
    with os.scandir(directory) as entries:
        return any(_offered_dir(entry, sample_stems) for entry in entries)


class ModelFolderDeleteResponse(BaseModel):
    """Body of ``DELETE /model-folders/{folder_id}``."""

    model_config = ConfigDict(extra="allow")

    status: str
    id: int
    tombstoned_files: int = Field(
        description=(
            "How many ``model_file`` rows were dropped. The models themselves "
            "survive with their names, triggers and attachments, so re-adding "
            "the folder re-links them."
        )
    )


class ModelFolderPickUpRequest(BaseModel):
    """Body of ``POST /model-folders/pick-up``."""

    files: list[str] = Field(
        min_length=1,
        max_length=200,
        description=(
            "Model filenames a workflow names and ComfyUI does not have, as the "
            "pre-flight's `missing_models` reports them. Matched by basename, "
            "case-insensitively: `sdxl/foo.safetensors` finds `foo.safetensors` "
            "anywhere in a scanned folder."
        ),
    )
    add: bool = Field(
        default=False,
        description=(
            "False only looks, which is a walk and nothing more. True also "
            "registers each file found, which reads its header and may hash it."
        ),
    )


class ModelFolderPickedUpFile(BaseModel):
    """One file ``POST /model-folders/pick-up`` found."""

    folder_id: int
    relpath: str
    model_id: Optional[int] = Field(
        default=None,
        description=(
            "The shelf row the file now belongs to. Null when the request only "
            "looked, or when the file could not be read (logged)."
        ),
    )


class ModelFolderPickUpResponse(BaseModel):
    """Body of ``POST /model-folders/pick-up``."""

    files: list[ModelFolderPickedUpFile]


def _settled(path: str) -> bool:
    """Whether a file found by a pick-up has finished arriving."""
    if any(os.path.exists(path + suffix) for suffix in IN_FLIGHT_SUFFIXES):
        return False
    try:
        if time.time() - os.path.getmtime(path) < PICK_UP_SETTLE_S:
            return False
    except OSError as exc:
        logger.info("Cannot stat %s for a pick-up, skipping it: %s", path, exc)
        return False
    return safetensors_complete(path)


def _normalize_optional_host_path(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    host_path = str(value).strip()
    if not host_path:
        return None
    normalized = normalize_host_path(host_path)
    if not is_absolute_host_path(normalized):
        raise HTTPException(
            status_code=400, detail="Host path must be an absolute path."
        )
    return normalized


def _validate_folder_conflicts(
    server, path: str, exclude_id: Optional[int] = None
) -> None:
    """Refuse a path that overlaps the vault or an already-registered folder.

    Containment, not just equality, for the same reason
    ``_validate_reference_folder_conflicts`` checks it: two roots over the
    same files register a ``model_file`` row per root, which double-counts
    every file in ``file_count`` and in a model's locations, and gives the
    scanner N walks of the same bytes. ``/`` is refused here rather than by a
    rule of its own, since it contains the vault by construction.

    Args:
        server: The Server instance, for ``vault`` and ``hub``.
        path: The resolved candidate path.
        exclude_id: A folder not to compare against: the one being renamed,
            whose new name may differ from its old one only in case.

    Raises:
        HTTPException: 409 if the path overlaps something already registered.
    """
    image_root = getattr(server.vault, "image_root", "") or ""
    detail = data_folder_conflict_detail(
        image_root,
        inside=path_is_within(path, image_root),
        contains=path_is_within(image_root, path),
    )
    if detail:
        raise HTTPException(status_code=409, detail=detail)
    for row in server.hub.fetchall("SELECT id, path FROM model_folder"):
        if row["id"] == exclude_id:
            continue
        other = str(row["path"])
        if path_is_within(path, other):
            raise HTTPException(
                status_code=409,
                detail=f"Path is inside a registered model folder: {other}",
            )
        if path_is_within(other, path):
            raise HTTPException(
                status_code=409,
                detail=f"A registered model folder is inside this path: {other}",
            )


def register_model_folder(
    server,
    path: str,
    kind: str,
    host_path: Optional[str] = None,
    delete_after_import: bool = False,
) -> int:
    """Validate and insert a ``model_folder`` row; does not scan.

    Shared by the create route and :func:`register_comfyui_model_folders`, so
    both refuse the same paths for the same reasons.

    Args:
        server: The Server instance, for ``hub``, ``vault`` and Docker mode.
        path: The folder to register; resolved through symlinks before storing.
        kind: One of :data:`CREATABLE_KINDS`.
        host_path: The Docker bind source, required in Docker mode.
        delete_after_import: Source folders only: remove runs once imported.

    Returns:
        The new folder's id.

    Raises:
        HTTPException: 400 for an unusable path, 409 for an overlapping one.
    """
    if kind not in CREATABLE_KINDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"kind must be one of {list(CREATABLE_KINDS)}; managed and "
                "foreign folders are registered by PixlStash itself."
            ),
        )
    path = os.path.normpath(path)
    # Lexical first, because it is the only check that can still see a
    # relative path: realpath below would silently make one absolute against
    # the server's cwd.
    error = validate_reference_folder_path(path)
    if error:
        raise HTTPException(status_code=400, detail=error)
    # That check compares strings, so one symlink defeats it:
    # ``/home/u/models-link -> /etc`` passes, and the scan then walks /etc
    # because os.walk follows the top-level link (followlinks=False only
    # governs links found inside the tree). The example is spelled absolute
    # because the lexical check above has already refused anything else, and
    # nothing here expands ``~``. Resolve, re-run the blocklist on what the
    # link actually points
    # at, and store the resolved path so the row names the directory that
    # gets walked. This is the second half of the check
    # ``create_reference_folder`` runs and this route was missing.
    path = os.path.realpath(path)
    error = validate_reference_folder_accessible(path)
    if error:
        raise HTTPException(status_code=400, detail=error)
    host_path = _normalize_optional_host_path(host_path)
    if server.running_in_docker() and host_path is None:
        raise HTTPException(
            status_code=400, detail="Host path is required in Docker mode."
        )

    movable, owner = _DERIVED_BY_KIND[kind]
    now = datetime.now(timezone.utc).isoformat()
    existing = server.hub.fetchone(
        "SELECT id FROM model_folder WHERE path = ?", (path,)
    )
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="This folder is already registered."
        )
    if kind == DOWNLOADS_FOLDER_KIND and server.hub.fetchone(
        "SELECT 1 FROM model_folder WHERE kind = ?", (DOWNLOADS_FOLDER_KIND,)
    ):
        raise HTTPException(
            status_code=409,
            detail="A Downloads folder is already set. Forget it first.",
        )
    _validate_folder_conflicts(server, path)
    try:
        with server.hub.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO model_folder (path, kind, owner, movable, host_path, "
                "delete_after_import, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    path,
                    kind,
                    owner,
                    movable,
                    host_path,
                    int(delete_after_import),
                    now,
                ),
            )
            folder_id = int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        # A concurrent registration won the race past the checks above: the
        # same path (`UNIQUE(path)`) or a second Downloads folder
        # (`ux_model_folder_single_downloads`). Either way it is a 409.
        logger.info("Model folder %s (kind=%s) not registered: %s", path, kind, exc)
        raise HTTPException(
            status_code=409,
            detail=(
                "A Downloads folder is already set. Forget it first."
                if kind == DOWNLOADS_FOLDER_KIND
                else "This folder is already registered."
            ),
        ) from exc
    logger.info("Model folder registered: %s (kind=%s)", path, kind)
    return folder_id


def queue_model_folder_scan(server, folder_id: int, path: str, kind: str) -> bool:
    """Queue the first scan of a folder just registered, ahead of other work.

    The finder would find it anyway (a folder never scanned is due at once);
    queuing it here is what lets the registration answer with the scan already
    ``pending``, so the folder reads as being looked at rather than as empty.

    Returns:
        False when it was not queued: one is already in flight, or the task
        runner refused it, in which case the finder's next check takes it.
    """
    task = claim_scan(
        server.hub,
        folder_id,
        path,
        kind,
        priority=TaskPriority.HIGH,
        notifier=server.vault.notify,
    )
    if task is None:
        return False
    try:
        task_id = server.vault.submit_task(task)
    except RuntimeError as exc:
        logger.error(
            "Could not queue the first scan of model folder %s (id=%s): the "
            "task runner refused the submission: %s. The background scan will "
            "pick it up instead.",
            path,
            folder_id,
            exc,
        )
        task_id = None
    if task_id is None:
        release_scan(task)
        return False
    logger.info(
        "First scan of model folder %s (id=%s, kind=%s) queued as task %s.",
        path,
        folder_id,
        kind,
        task_id,
    )
    return True


def register_comfyui_model_folders(server, base_url: str) -> list[int]:
    """Put a connected ComfyUI's model folders on the shelf and scan them.

    Only for a ComfyUI on this computer: the paths are ComfyUI's own, and from
    any other host they name directories on a machine this is not, which a
    hostile ComfyUI could use to make the shelf walk the whole filesystem.
    Each root from :func:`comfyui_model_roots` then goes through the same
    checks as a folder added by hand, and a filesystem root or the home folder
    itself is never registered. A refused one (already on the shelf, inside another
    folder, not on this machine, Docker without a host path) is logged and
    skipped: the ComfyUI connection is saved either way.

    Args:
        server: The Server instance.
        base_url: The ComfyUI URL, without a trailing slash.

    Returns:
        The ids of the folders newly registered.
    """
    try:
        where, pinned = comfyui_link_service.locate(base_url)
    except ValueError as exc:
        logger.info("ComfyUI model folders not read: %s", exc)
        return []
    if where != "this_computer":
        logger.info(
            "ComfyUI at %s is not on this computer; its model folders are not "
            "added to the shelf.",
            base_url,
        )
        return []
    too_broad = {os.path.realpath(os.path.expanduser("~"))}
    registered = []
    # Asked at the address `locate` vetted, so a second DNS answer cannot
    # supply the paths from somewhere else.
    for root in comfyui_model_roots(pinned.rstrip("/")):
        resolved = os.path.realpath(root)
        if os.path.dirname(resolved) == resolved or resolved in too_broad:
            logger.warning(
                "ComfyUI reported %s as a model folder; a filesystem root or the "
                "home folder is never put on the shelf whole.",
                root,
            )
            continue
        try:
            folder_id = register_model_folder(server, root, "user")
        except HTTPException as exc:
            logger.info(
                "ComfyUI model folder %s not added to the shelf: %s",
                root,
                exc.detail,
            )
            continue
        registered.append(folder_id)
        queue_model_folder_scan(server, folder_id, resolved, "user")
    return registered


def create_router(server) -> APIRouter:
    """Create the model-folder router.

    Args:
        server: The Server instance, for ``hub`` (the folder rows) and ``auth``.

    Returns:
        The configured router.
    """
    router = APIRouter()

    def _fetch_folder(folder_id: int) -> dict:
        row = server.hub.fetchone(
            "SELECT id, path, kind, owner, movable, host_path, delete_after_import, "
            "is_default, is_download_target, last_checked, created_at FROM model_folder "
            "WHERE id = ?",
            (folder_id,),
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Model folder not found.")
        return dict(row)

    def _file_counts() -> dict[int, int]:
        """Copies per folder, in one grouped query rather than one per folder."""
        rows = server.hub.fetchall(
            "SELECT model_folder_id, COUNT(*) AS n FROM model_file "
            "GROUP BY model_folder_id"
        )
        return {int(row["model_folder_id"]): int(row["n"]) for row in rows}

    def _default_folder_id() -> Optional[int]:
        row = find_default_folder(server.hub)
        return int(row["id"]) if row is not None else None

    def _download_folder_id() -> Optional[int]:
        row = find_download_folder(server.hub)
        return int(row["id"]) if row is not None else None

    def _to_response(
        row: dict,
        file_count: int = 0,
        present_bytes: int = 0,
        scan: Optional[tuple[Optional[str], Optional[str]]] = None,
        stability_matrix_roots: Optional[list[str]] = None,
        default_id: Optional[int] = None,
        download_id: Optional[int] = None,
    ) -> ModelFolderResponse:
        if default_id is None:
            default_id = _default_folder_id()
        if download_id is None:
            download_id = _download_folder_id()
        delete_after_import = row["delete_after_import"]
        scan_status, scan_error = (
            scan if scan is not None else scan_state(int(row["id"]))
        )
        return ModelFolderResponse(
            id=int(row["id"]),
            path=row["path"],
            kind=row["kind"],
            owner=row["owner"],
            tool=_folder_tool(server, row, stability_matrix_roots),
            movable=row["movable"],
            relocatable=relocatable_identity(row) is not None,
            deletable=deletes_unclaimed_files(row["kind"], row["path"]),
            host_path=row["host_path"],
            delete_after_import=(
                None if delete_after_import is None else bool(delete_after_import)
            ),
            is_default=int(row["id"]) == default_id,
            is_download_target=int(row["id"]) == download_id,
            last_checked=row["last_checked"],
            created_at=row["created_at"],
            file_count=file_count,
            present_bytes=present_bytes,
            scan_status=scan_status,
            scan_error=scan_error,
        )

    @router.get(
        "/model-folders",
        summary="List registered model folders",
        description=(
            "Every folder the shelf catalogues or takes runs from, with how many "
            "copies are registered under each."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderListResponse,
    )
    def list_model_folders(request: Request):
        server.auth.ensure_secure_when_required(request)
        rows = server.hub.fetchall(
            "SELECT id, path, kind, owner, movable, host_path, delete_after_import, "
            "is_default, is_download_target, last_checked, created_at FROM model_folder "
            "ORDER BY id"
        )
        # Scan state before the aggregates, never after. A scan's task flips to
        # completed only once it has written its rows, so reading the counts
        # first lets a scan that finishes in between be reported as completed
        # with the count from before it wrote - an empty folder that just
        # succeeded. This way round the stale pairing is "still running" with a
        # full count, which the next poll corrects.
        scans = {int(row["id"]): scan_state(int(row["id"])) for row in rows}
        counts = _file_counts()
        present_bytes = _present_bytes_by_folder()
        # Once per list, not per row: the dialog polls this every few seconds.
        stability_matrix_roots = _stability_matrix_roots()
        default_id = _default_folder_id()
        download_id = _download_folder_id()
        return ModelFolderListResponse(
            folders=[
                _to_response(
                    dict(row),
                    counts.get(int(row["id"]), 0),
                    present_bytes.get(int(row["id"]), 0),
                    scans[int(row["id"])],
                    stability_matrix_roots,
                    default_id,
                    download_id,
                )
                for row in rows
            ]
        )

    def _present_bytes_by_folder() -> dict[int, int]:
        """Registered `present` bytes per folder, in one grouped query.

        ``present`` only. A ``missing`` row names bytes that are no longer
        there, and an ``unreachable`` one names bytes we could not look at;
        counting either into a capacity meter would report space that the drive
        does not agree is in use.
        """
        rows = server.hub.fetchall(
            "SELECT mf.model_folder_id AS folder_id, "
            "SUM(COALESCE(m.file_size, 0)) AS total "
            "FROM model_file mf JOIN model m ON m.id = mf.model_id "
            "WHERE mf.state = 'present' GROUP BY mf.model_folder_id"
        )
        return {int(row["folder_id"]): int(row["total"] or 0) for row in rows}

    @router.get(
        "/model-folders/devices",
        summary="Capacity of the drives the model folders sit on",
        description=(
            "One entry per drive, with the folders on it, how full it is and "
            "how much of that the shelf accounts for. Folders are grouped by "
            "the filesystem they sit on rather than by path, so two folders on "
            "one drive share one meter and a bind mount does not read as a "
            "second drive.\n\n"
            "Separate from `GET /model-folders` on purpose: this route stats "
            "the filesystem, so an offline network mount can make it slow, "
            "while the folder list answers from the database and stays fast. A "
            "drive that cannot be measured is still returned, with null "
            "capacity, so its folders keep a band to sit in."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderDeviceListResponse,
    )
    def list_model_folder_devices(request: Request):
        server.auth.ensure_secure_when_required(request)
        shelf_bytes = _present_bytes_by_folder()
        rows = server.hub.fetchall("SELECT id, path FROM model_folder ORDER BY id")
        # Key on the measured device, never on the path: a bind mount and a
        # symlinked folder look like different drives by path and are one, and
        # two subdirectories of one root can be different drives when a mount
        # sits between them. Same reasoning as `model_mover.same_device`.
        by_device: dict[str, ModelFolderDeviceResponse] = {}
        devices: list[ModelFolderDeviceResponse] = []
        for row in rows:
            folder_id = int(row["id"])
            path = str(row["path"])
            device = describe_storage_device(path)
            if device is None:
                devices.append(
                    ModelFolderDeviceResponse(
                        mount_point=path,
                        shelf_bytes=shelf_bytes.get(folder_id, 0),
                        folder_ids=[folder_id],
                    )
                )
                continue
            existing = by_device.get(device.device_id)
            if existing is None:
                existing = ModelFolderDeviceResponse(
                    device_id=device.device_id,
                    mount_point=device.mount_point,
                    label=device.label,
                    total_bytes=device.total_bytes,
                    free_bytes=device.free_bytes,
                    kind=device.kind,
                    shelf_bytes=0,
                    folder_ids=[],
                )
                by_device[device.device_id] = existing
                devices.append(existing)
            existing.folder_ids.append(folder_id)
            existing.shelf_bytes += shelf_bytes.get(folder_id, 0)
        return ModelFolderDeviceListResponse(devices=devices)

    @router.get(
        "/model-folders/comfyui-reads",
        summary="Where ComfyUI loads each kind of model from",
        description=(
            "The directories inside the registered folders that the configured "
            "ComfyUI loads each kind of model from, read live from its "
            "`/internal/folder_paths`, so `extra_model_paths.yaml` counts.\n\n"
            "`known` is false when no read lands in a registered folder: no "
            "ComfyUI set, it did not answer, or it is not on this computer, "
            "whose paths would name another machine's disk. A model is only "
            "ever called misplaced when `known` is true."
        ),
        tags=["model_shelf"],
        response_model=ComfyuiReadsResponse,
    )
    def list_comfyui_reads(request: Request):
        server.auth.ensure_secure_when_required(request)
        user = server.auth.get_user_for_request(request)
        url = (getattr(user, "comfyui_url", None) or "").strip()
        if not url:
            return ComfyuiReadsResponse(known=False)
        try:
            where, pinned = comfyui_link_service.locate(url)
        except ValueError as exc:
            logger.info("ComfyUI reads not asked of %s: %s", url, exc)
            return ComfyuiReadsResponse(known=False)
        if where != "this_computer":
            return ComfyuiReadsResponse(known=False)
        try:
            # The vetted address, so a second DNS answer cannot supply paths.
            folder_paths = comfyui_folder_paths(pinned.rstrip("/"))
        except (requests.RequestException, ValueError) as exc:
            logger.warning(
                "Could not read where ComfyUI at %s loads models from: %s", url, exc
            )
            return ComfyuiReadsResponse(known=False)
        rows = server.hub.fetchall("SELECT id, path FROM model_folder ORDER BY id")
        reads = comfyui_reads([dict(row) for row in rows], folder_paths)
        return ComfyuiReadsResponse(known=bool(reads), reads=reads)

    def _present_copies(folder_id: int, prefix: str) -> list:
        """Every `present` copy under *prefix*, in one query.

        ``prefix`` is the listed directory's relpath with a trailing ``os.sep``,
        escaped for LIKE. ``""`` reads the whole folder, and then no LIKE is run.
        """
        sql = (
            "SELECT mf.relpath AS relpath, mf.model_id AS model_id, "
            "COALESCE(m.file_size, 0) AS file_size "
            "FROM model_file mf JOIN model m ON m.id = mf.model_id "
            "WHERE mf.model_folder_id = ? AND mf.state = 'present'"
        )
        if not prefix:
            return server.hub.fetchall(sql, (folder_id,))
        return server.hub.fetchall(
            sql + " AND mf.relpath LIKE ? ESCAPE '\\'",
            (folder_id, prefix.translate(_LIKE_ESCAPE) + "%"),
        )

    @router.get(
        "/model-folders/{folder_id}/dirs",
        summary="List one directory level of a model folder",
        description=(
            "The subdirectories of one directory inside a registered folder, one "
            "level per call, so the models tree opens lazily.\n\n"
            "`path` is POSIX and relative to the folder; leave it empty for the "
            "root. Every directory carries the `present` models under it, "
            "recursively, and those directly in it. Empty directories are listed. Dot-names, symlinks and a "
            "`<stem>_samples` directory beside its model are not.\n\n"
            "A locked folder answers `locked: true` with no `dirs`, and its disk "
            "is not read; the counts still come from the database."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderDirsResponse,
    )
    def list_model_folder_dirs(
        folder_id: int,
        request: Request,
        path: str = Query(
            "",
            description="Directory to list, relative to the folder. Empty is the root.",
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        folder = _fetch_folder(folder_id)
        segments = _subpath_segments(path)
        subpath = "/".join(segments)
        prefix = subpath.replace("/", os.sep) + os.sep if segments else ""
        locked = folder["movable"] != "per_item"

        directory = ""
        if not locked:
            try:
                directory = resolve_path_within(folder["path"], *segments)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="path must stay within the model folder.",
                ) from exc
            if not os.path.isdir(directory):
                raise HTTPException(
                    status_code=404,
                    detail="No such directory in this model folder.",
                )

        # One pass over the folder's present copies, bucketed by the first
        # segment below the listed directory. A copy with no further separator
        # is a file directly in it, which feeds the samples rule.
        totals = _Subtree()
        here_stems: set[str] = set()
        children: dict[str, _Subtree] = {}
        child_stems: dict[str, set[str]] = {}
        for row in _present_copies(folder_id, prefix):
            relpath = row["relpath"]
            # LIKE folds ASCII case, so `Alpha/` would match a listing of
            # `alpha/`; the exact prefix is checked here.
            if not relpath.startswith(prefix):
                continue
            model_id = int(row["model_id"])
            size = int(row["file_size"])
            totals.model_ids.add(model_id)
            totals.bytes += size
            head, separator, tail = relpath[len(prefix) :].partition(os.sep)
            if not separator:
                totals.direct_ids.add(model_id)
                here_stems.add(os.path.splitext(head)[0])
                continue
            child = children.setdefault(head, _Subtree())
            child.model_ids.add(model_id)
            child.bytes += size
            if os.sep not in tail:
                child.direct_ids.add(model_id)
                child_stems.setdefault(head, set()).add(os.path.splitext(tail)[0])

        dirs: list[ModelFolderDirResponse] = []
        if not locked:
            try:
                with os.scandir(directory) as entries:
                    offered = sorted(
                        (e for e in entries if _offered_dir(e, here_stems)),
                        key=lambda e: (e.name.casefold(), e.name),
                    )
            except OSError as exc:
                logger.warning(
                    "Could not list model folder %s at %r (%s): %s",
                    folder_id,
                    subpath,
                    directory,
                    exc,
                )
                raise HTTPException(
                    status_code=409,
                    detail="Could not read this directory.",
                ) from exc
            for entry in offered:
                child = children.get(entry.name, _Subtree())
                try:
                    has_dirs = _has_offered_dir(
                        entry.path, child_stems.get(entry.name, set())
                    )
                except OSError as exc:
                    logger.warning(
                        "Could not look inside %s for subdirectories (%s); "
                        "reporting has_dirs as false, so the tree offers no "
                        "expand on it.",
                        entry.path,
                        exc,
                    )
                    has_dirs = False
                dirs.append(
                    ModelFolderDirResponse(
                        name=entry.name,
                        path=f"{subpath}/{entry.name}" if subpath else entry.name,
                        model_count=len(child.model_ids),
                        direct_model_count=len(child.direct_ids),
                        bytes=child.bytes,
                        has_dirs=has_dirs,
                    )
                )

        return ModelFolderDirsResponse(
            folder_id=folder_id,
            path=subpath,
            locked=locked,
            model_count=len(totals.model_ids),
            direct_model_count=len(totals.direct_ids),
            bytes=totals.bytes,
            dirs=dirs,
        )

    @router.post(
        "/model-folders/{folder_id}/dirs",
        summary="Create a directory inside a model folder",
        description=(
            "Makes one new, empty directory inside a registered folder, so models "
            "can be moved into it. 201 with its path; 409 when the name is taken "
            "or the folder is locked; 400 with the reason for a name the drive "
            "cannot hold; 404 when the parent does not exist. Refused in Docker, "
            "as creating a host folder is."
        ),
        status_code=201,
        tags=["model_shelf"],
        response_model=ModelFolderDirCreateResponse,
    )
    def create_model_folder_dir(
        folder_id: int,
        request: Request,
        payload: ModelFolderDirCreateRequest = Body(...),
    ):
        server.auth.ensure_secure_when_required(request)
        if server.running_in_docker():
            raise HTTPException(
                status_code=403,
                detail="Creating folders is not available in Docker mode.",
            )
        folder = _fetch_folder(folder_id)
        if folder["movable"] != "per_item":
            raise HTTPException(
                status_code=409,
                detail="This folder is locked; PixlStash does not create folders in it.",
            )
        reason = refusal_for_folder_name(payload.name)
        if reason:
            raise HTTPException(status_code=400, detail=reason)
        try:
            subpath, parent = resolve_subfolder(folder["path"], payload.path)
        except MoveRefused as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        if not os.path.isdir(parent):
            raise HTTPException(
                status_code=404, detail="No such directory in this model folder."
            )
        # Joined, not resolved: the name is one plain segment by now, and a link
        # already standing at it is a taken name, never a path to create through
        # (``mkdir`` does not follow a final link; it answers EEXIST).
        target = os.path.join(parent, payload.name)
        try:
            os.mkdir(target)
        except FileExistsError as exc:
            raise HTTPException(
                status_code=409, detail=f"{payload.name!r} already exists there."
            ) from exc
        except OSError as exc:
            logger.error(
                "Could not create %s in model folder %s: %s", target, folder_id, exc
            )
            raise HTTPException(
                status_code=409, detail=f"Could not create the folder: {exc.strerror}"
            ) from exc
        logger.info("Created %s in model folder %s.", target, folder_id)
        return ModelFolderDirCreateResponse(
            folder_id=folder_id,
            path=f"{subpath}/{payload.name}" if subpath else payload.name,
        )

    @router.post(
        "/model-folders/{folder_id}/open",
        summary="Open a model folder in the file manager",
        description=(
            "Opens a registered folder, or a directory inside it, in the file "
            "manager of the machine PixlStash runs on - the folder tree's "
            "`Open in file manager`. It drives the server's own desktop, so it "
            "is `LOOPBACK_OWNER_ONLY` (§16.3.1), as "
            "`POST /models/{model_id}/open-location` is.\n\n"
            "`path` is POSIX and relative to the folder, as on "
            "`GET /model-folders/{folder_id}/dirs`; empty is the root. 404 when "
            "the folder or the directory is not there, 500 when the host has no "
            "file manager to open it in."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderOpenResponse,
    )
    def open_model_folder(
        folder_id: int,
        request: Request,
        path: str = Query(
            "",
            description="Directory to open, relative to the folder. Empty is the root.",
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        folder = _fetch_folder(folder_id)
        try:
            directory = resolve_path_within(folder["path"], *_subpath_segments(path))
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="path must stay within the model folder."
            ) from exc
        if not os.path.isdir(directory):
            raise HTTPException(
                status_code=404, detail="No such directory in this model folder."
            )
        if not open_in_file_manager(directory):
            # Named rather than swallowed: usually a headless or containerised
            # server, and the caller's only other clue is a click that did nothing.
            raise HTTPException(
                status_code=500,
                detail="Could not open that folder on the server's desktop.",
            )
        return ModelFolderOpenResponse(status="ok")

    @router.post(
        "/model-folders",
        summary="Register a model folder",
        description=(
            "Adds a folder for the shelf to catalogue (`kind=user`), to take "
            "ai-toolkit runs from (`kind=source`), or to watch for downloaded "
            "models (`kind=downloads`, one at most), which are moved into the "
            "default folder. A catalogued folder's first scan is queued at "
            "once as a background task (its `scan_status`), and it is rescanned "
            "by itself whenever a model file in it changes."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderResponse,
    )
    def create_model_folder(
        request: Request,
        payload: ModelFolderCreateRequest = Body(...),
    ):
        server.auth.ensure_secure_when_required(request)
        folder_id = register_model_folder(
            server,
            payload.path,
            payload.kind,
            host_path=payload.host_path,
            delete_after_import=payload.delete_after_import,
        )
        folder = _fetch_folder(folder_id)
        if folder["kind"] not in TAKEN_FROM_KINDS:
            queue_model_folder_scan(server, folder_id, folder["path"], folder["kind"])
            # So the background scan sets up the folder's watch now rather
            # than on its next check.
            server.vault.wake()
        return _to_response(folder)

    @router.patch(
        "/model-folders/{folder_id}",
        summary="Update a registered model folder",
        description=(
            "Changes the Docker bind source, the source-folder import "
            "behaviour, or which folder is the default destination for a new "
            "model. The path itself is not editable: moving a folder's "
            "contents is a copy-verify-repoint operation, not a field edit."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderResponse,
    )
    def update_model_folder(
        folder_id: int,
        request: Request,
        payload: ModelFolderUpdateRequest = Body(...),
    ):
        server.auth.ensure_secure_when_required(request)
        folder = _fetch_folder(folder_id)

        # Everything that can refuse runs before anything is written, so a
        # 400 never leaves half the body applied.
        assignments: list[str] = []
        params: list = []
        if "host_path" in payload.model_fields_set:
            assignments.append("host_path = ?")
            params.append(_normalize_optional_host_path(payload.host_path))
        if payload.delete_after_import is not None:
            assignments.append("delete_after_import = ?")
            params.append(int(payload.delete_after_import))
        if payload.is_default or payload.is_download_target:
            _check_default_eligible(folder)
        # One transaction for the whole body, so a write that fails part-way
        # (a locked database) leaves none of it applied.
        marks = (payload.is_default, payload.is_download_target)
        if assignments or any(mark is not None for mark in marks):
            with server.hub.transaction() as conn:
                if payload.is_default is not None:
                    _apply_default(conn, folder, payload.is_default)
                if payload.is_download_target is not None:
                    # Cleared first, so the partial unique index never sees
                    # two. Unlike the default, the mark is stored on the
                    # managed store too: the fallback is the default folder,
                    # which the owner may move somewhere else later.
                    if payload.is_download_target or folder["is_download_target"]:
                        conn.execute(
                            "UPDATE model_folder SET is_download_target = 0 "
                            "WHERE is_download_target = 1"
                        )
                    if payload.is_download_target:
                        conn.execute(
                            "UPDATE model_folder SET is_download_target = 1 "
                            "WHERE id = ?",
                            (folder_id,),
                        )
                if assignments:
                    conn.execute(
                        f"UPDATE model_folder SET {', '.join(assignments)} "
                        "WHERE id = ?",
                        (*params, folder_id),
                    )
            if payload.is_default is not None:
                logger.info(
                    "Default model folder %s: %s (id=%s).",
                    "set to" if payload.is_default else "no longer",
                    folder["path"],
                    folder_id,
                )
            if payload.is_download_target is not None:
                logger.info(
                    "Model download folder %s: %s (id=%s).",
                    "set to" if payload.is_download_target else "no longer",
                    folder["path"],
                    folder_id,
                )
        return _to_response(_fetch_folder(folder_id))

    def _check_default_eligible(folder: dict) -> None:
        if folder["kind"] not in DEFAULT_ELIGIBLE_KINDS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Only a folder the shelf lists and writes into can take "
                    "new models: one you added, or the managed store."
                ),
            )

    def _apply_default(conn, folder: dict, is_default: bool) -> None:
        """Make *folder* the default destination, or hand the role back.

        Run on the caller's transaction: clearing every flag and setting one
        commit together, so the partial unique index never sees two. Marking
        the managed store and unmarking any folder both leave no flag at all,
        which is what makes the managed store the default again.
        """
        if is_default or folder["is_default"]:
            conn.execute("UPDATE model_folder SET is_default = 0 WHERE is_default = 1")
        if is_default and folder["kind"] != MANAGED_KIND:
            conn.execute(
                "UPDATE model_folder SET is_default = 1 WHERE id = ?",
                (folder["id"],),
            )

    @router.post(
        "/model-folders/{folder_id}/rename",
        summary="Rename a registered model folder on disk",
        description=(
            "Renames a folder you added, in place, and points its registration "
            "at the new name. Everything in PixlStash refers to the folder by "
            "its id and to its files by their path inside it, so the default "
            "and download marks, the shelf's copies and their curation follow "
            "without a rescan. 409 for a locked folder, a name already taken or "
            "a registered path, and while a move, an import or a scan of this "
            "folder or a download into it runs; 400 with the reason for a name the drive cannot hold; "
            "404 when the folder is not on disk. Refused in Docker, as creating "
            "a host folder is. Tools outside PixlStash that name the old path "
            "(ComfyUI, StabilityMatrix) are not told."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderResponse,
    )
    def rename_model_folder(
        folder_id: int,
        request: Request,
        payload: ModelFolderRenameRequest = Body(...),
    ):
        server.auth.ensure_secure_when_required(request)
        if server.running_in_docker():
            raise HTTPException(
                status_code=403,
                detail="Renaming folders is not available in Docker mode.",
            )
        folder = _fetch_folder(folder_id)
        # An owner is a subsystem that records where the folder is (the engine
        # download folder's pointer file), and a rename would strand it.
        if folder["movable"] != "per_item" or folder["owner"] is not None:
            raise HTTPException(
                status_code=409,
                detail="This folder is locked; PixlStash does not rename it.",
            )
        reason = refusal_for_folder_name(payload.name)
        if reason:
            raise HTTPException(status_code=400, detail=reason)
        old_path = folder["path"]
        new_path = os.path.join(os.path.dirname(old_path), payload.name)
        if new_path == old_path:
            return _to_response(folder)
        if not os.path.isdir(old_path):
            raise HTTPException(
                status_code=404,
                detail="The folder is not on disk; plug its drive in and try again.",
            )
        # A row naming the new path, or a folder inside it, may be registered
        # while its drive is unplugged, so the disk alone cannot rule it out.
        _validate_folder_conflicts(server, new_path, exclude_id=folder_id)
        # The slot a move or an import takes: either may be writing a path
        # under the old name.
        if not SHELF_IO_LOCK.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail="A move or an import is running. Try again when it is done.",
            )
        try:
            # A download writes a partial under the old path and publishes to
            # it by name, so a rename under it would strand the partial.
            if any(
                entry["status"] == model_downloads.STATUS_RUNNING
                and entry["folder_path"] == old_path
                for entry in model_downloads.live_downloads()
            ):
                raise HTTPException(
                    status_code=409,
                    detail="A model is downloading into this folder. Try again when it is done.",
                )
            # Holding the folder's scan slot keeps the finder from handing out a
            # scan of the old path while it is renamed: that scan would find the
            # folder gone and mark every model in it unreachable.
            hold = claim_scan(server.hub, folder_id, old_path, folder["kind"])
            if hold is None:
                raise HTTPException(
                    status_code=409,
                    detail="This folder is being scanned. Try again in a moment.",
                )
            try:
                _rename_on_disk_and_hub(folder_id, old_path, new_path)
            finally:
                release_scan(hold)
        finally:
            SHELF_IO_LOCK.release()
        logger.info(
            "Model folder %s (id=%s) renamed to %s.", old_path, folder_id, new_path
        )
        return _to_response(_fetch_folder(folder_id))

    def _rename_on_disk_and_hub(folder_id: int, old_path: str, new_path: str) -> None:
        """Rename the directory, then the row; put the directory back if the row
        cannot follow, so the registration never names a folder that is gone."""
        # Checked, not left to the rename: on POSIX a rename onto an empty
        # directory replaces it. A case-only rename on a case-insensitive drive
        # "exists" but is the same directory. The window between the check and
        # the rename is the one `model_dir_mover` accepts too.
        if os.path.lexists(new_path) and not os.path.samefile(old_path, new_path):
            raise HTTPException(
                status_code=409,
                detail=f"{os.path.basename(new_path)!r} already exists there.",
            )
        try:
            os.rename(old_path, new_path)
        except OSError as exc:
            logger.error(
                "Could not rename model folder %s (id=%s) to %s: %s",
                old_path,
                folder_id,
                new_path,
                exc,
            )
            raise HTTPException(
                status_code=409, detail=f"Could not rename the folder: {exc.strerror}"
            ) from exc
        try:
            with server.hub.transaction() as conn:
                conn.execute(
                    "UPDATE model_folder SET path = ? WHERE id = ?",
                    (new_path, folder_id),
                )
        except sqlite3.Error as exc:
            logger.error(
                "Renamed model folder %s (id=%s) to %s but could not record it "
                "(%s); renaming it back.",
                old_path,
                folder_id,
                new_path,
                exc,
            )
            try:
                os.rename(new_path, old_path)
            except OSError as undo_exc:
                logger.error(
                    "Could not rename %s back to %s (%s). The registration "
                    "still names %s; rename the folder back by hand or add it "
                    "again at its new name.",
                    new_path,
                    old_path,
                    undo_exc,
                    old_path,
                )
            raise HTTPException(
                status_code=500, detail="Could not record the new name."
            ) from exc

    @router.delete(
        "/model-folders/{folder_id}",
        summary="Forget a registered model folder",
        description=(
            "Drops the folder and its location rows. The models themselves "
            "survive with their names, triggers, corrected kinds and "
            "attachments, so re-adding the folder re-links them by content. "
            "Nothing on disk is touched. The managed store cannot be forgotten: "
            "it is PixlStash's own storage rather than a folder the owner "
            "associated, so there is nothing to disassociate. Answers 409 while "
            "a move or an import is running: those write the very location rows "
            "this deletes."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderDeleteResponse,
    )
    def delete_model_folder(folder_id: int, request: Request):
        server.auth.ensure_secure_when_required(request)
        folder = _fetch_folder(folder_id)
        if folder["owner"] == BUILTIN_OWNER:
            # Same 409 reasoning as the managed store one line down: the caller
            # is authorized and the request is well formed, and what refuses it
            # is that this folder is PixlStash's own. Forgetting it would only
            # make the next start-up declare it again.
            raise HTTPException(
                status_code=409,
                detail=(
                    "This folder holds the engines PixlStash downloaded for "
                    "itself. It is registered so you can see what is on your "
                    "disk, and re-registers itself on the next start."
                ),
            )
        if folder["kind"] == MANAGED_KIND:
            # 409, not 403: the caller is fully authorized and the request is
            # well formed. What refuses it is the state of the target - this row
            # is PixlStash's own storage, and exactly one of it always exists.
            # A 403 would say "you may not", which is wrong and would send an
            # operator hunting through the authz tiers for a permission that
            # does not exist.
            raise HTTPException(
                status_code=409,
                detail=(
                    "The managed model store cannot be forgotten: it is where "
                    "PixlStash keeps models it was given, not a folder you "
                    "associated. Move it instead."
                ),
            )
        # The same machine-wide slot a move and an import take, and for the
        # reason at ``SHELF_IO_LOCK``'s own note: this deletes the very
        # ``model_file`` rows a running move is about to repoint. Forgetting a
        # source folder mid-move used to leave the move's UPDATE matching
        # nothing, the source unlinked anyway and the destination bytes
        # registered nowhere (#1017). The mover now refuses a repoint that does
        # not move exactly one row - that is the guarantee - and this is what
        # turns it into a clean 4xx before the batch starts rather than a file
        # failed halfway through forty.
        if not SHELF_IO_LOCK.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A move or an import is running. Forgetting a folder now "
                    "would delete the location rows it is writing. Wait for it "
                    "to finish and try again."
                ),
            )
        try:
            with server.hub.transaction() as conn:
                cursor = conn.execute(
                    "DELETE FROM model_file WHERE model_folder_id = ?", (folder_id,)
                )
                tombstoned = int(cursor.rowcount or 0)
                conn.execute("DELETE FROM model_folder WHERE id = ?", (folder_id,))
            forget_scan(folder_id)
        finally:
            SHELF_IO_LOCK.release()
        logger.info(
            "Model folder %s (id=%s) forgotten; %d location row(s) tombstoned, "
            "model rows and their curation kept.",
            folder["path"],
            folder_id,
            tombstoned,
        )
        return ModelFolderDeleteResponse(
            status="success", id=folder_id, tombstoned_files=tombstoned
        )

    @router.post(
        "/model-folders/pick-up",
        summary="Find and register model files that landed after the last scan",
        description=(
            "Looks through every scanned folder for files named one of `files` "
            "that the shelf has no `present` row for, and that have finished "
            "arriving (unchanged for 10 s, and a whole safetensors file): a model downloaded after its "
            "folder's last scan, which a workflow then reports missing (#1985). "
            "With `add` false it only walks, nothing is opened or hashed, "
            "so it is cheap enough to ask whenever a file is missing. With "
            "`add` true each file found is registered as a scan would, "
            "without sweeping anything else in its folder."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderPickUpResponse,
    )
    def pick_up_model_files(body: ModelFolderPickUpRequest, request: Request):
        server.auth.ensure_secure_when_required(request)
        names = {
            name.replace("\\", "/").rsplit("/", 1)[-1].lower()
            for name in body.files
            if name and name.lower().endswith(SHELF_MODEL_SUFFIXES)
        }
        if not names:
            return ModelFolderPickUpResponse(files=[])
        folders = [(int(row["id"]), row["path"]) for row in scanned_folders(server.hub)]
        scanner = ModelFolderScanner(server.hub)
        found = [
            hit for hit in scanner.find_unregistered(folders, names) if _settled(hit[1])
        ]
        files = [
            ModelFolderPickedUpFile(folder_id=folder_id, relpath=relpath)
            for folder_id, _abs_path, relpath in found
        ]
        if not body.add or not found:
            return ModelFolderPickUpResponse(files=files)
        # A move publishes its destination before it repoints the row, so a
        # file registered in between would fail the move. Busy is "not now":
        # the next look, 15 s on, tries again.
        if not SHELF_IO_LOCK.acquire(blocking=False):
            logger.info(
                "Not picking up %s yet: a model move or import is running.",
                ", ".join(relpath for _folder_id, _abs_path, relpath in found),
            )
            return ModelFolderPickUpResponse(files=files)
        try:
            for file, (folder_id, abs_path, relpath) in zip(files, found):
                try:
                    file.model_id = scanner.register_file(folder_id, abs_path, relpath)
                except sqlite3.IntegrityError as exc:
                    # The folder was forgotten while this ran (see `scan_folder`).
                    logger.warning(
                        "Could not pick up %s in model folder %s: %s. It stays "
                        "missing until the folder is scanned.",
                        abs_path,
                        folder_id,
                        exc,
                    )
                    continue
                if file.model_id is None:
                    logger.warning(
                        "Found %s in model folder %s but could not register it "
                        "(see above); it stays missing.",
                        abs_path,
                        folder_id,
                    )
                else:
                    logger.info(
                        "Picked up %s in model folder %s as model %s: it landed "
                        "after the folder's last scan.",
                        relpath,
                        folder_id,
                        file.model_id,
                    )
        finally:
            SHELF_IO_LOCK.release()
        return ModelFolderPickUpResponse(files=files)

    return router
