"""Look at what a training run produced, then take it onto the shelf.

Two routes, and the split between them is the whole point: **listing a run costs
nothing and changes nothing.** ``GET /model-folders/{id}/runs`` reads filenames
and one ``config.yaml`` per run — it does not hash, copy, move or write anything,
so the card grid can be drawn for an entire output root before the user has
decided about any of it. That property is what keeps face recognition and
hashing out of the browsing path, and it must not be eroded by adding "just one"
cheap-looking computation here.

``POST /model-imports`` is the committing half, and it runs the same ordering as
a move because it *is* a move with a row created rather than repointed: copy →
verify by SHA-256 → register the row and commit → then unlink. The unlink only
happens at all when the source folder carries ``delete_after_import``.

**A run is addressed by name, not by path.** The body names a registered
``source`` folder and a run *inside* it, and the server joins them, so no host
path is ever taken from the caller. The join is contained: a run name that
resolves outside its registered output root is refused rather than read.

Authorization: both routes are `LOCAL_OWNER_ONLY`, declared in
``pixlstash/authz/registry.py``. The listing walks a registered host path and
reads every run under it, which is the same authority as
``model-folders/{id}/rescan``; the import writes files into one registered folder
and may unlink them from another, which is the ``model-moves`` authority.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_mover import SHELF_IO_LOCK, MoveRefused
from pixlstash.services.run_importer import RunImporter
from pixlstash.utils.aitoolkit_run import read_output_root
from pixlstash.utils.path_utils import resolve_path_within

logger = get_logger(__name__)

SOURCE_FOLDER_KIND = "source"


class RunSample(BaseModel):
    """One preview image ai-toolkit rendered during the run."""

    model_config = ConfigDict(extra="allow")

    filename: str = Field(description="The sample's filename inside `samples/`.")
    step: int = Field(description="Training step it was rendered at.")
    index: int = Field(description="Which prompt, in the order ai-toolkit rendered.")


class RunCheckpoint(BaseModel):
    """One saved adapter file from the run."""

    model_config = ConfigDict(extra="allow")

    filename: str = Field(description="Filename inside the run folder.")
    step: Optional[int] = Field(
        default=None,
        description=(
            "Training step, or null for the bare final file that carries no step "
            "in its name. A run with no bare final has an **unconfirmed cover**: "
            "the highest step is the best available answer, not a certain one."
        ),
    )
    size: Optional[int] = Field(default=None, description="Bytes on disk.")


class RunResponse(BaseModel):
    """One training run, described without importing, hashing or moving it."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(description="The run folder's own name.")
    checkpoints: list[RunCheckpoint] = Field(default_factory=list)
    samples: list[RunSample] = Field(
        default_factory=list,
        description="Previews, so a step can be judged before it is imported.",
    )
    base_model: Optional[str] = Field(
        default=None,
        description="`name_or_path` from the run's `config.yaml`, verbatim.",
    )
    trigger_words: list[str] = Field(default_factory=list)
    rank: Optional[int] = Field(
        default=None, description="`linear` from the config, when it records one."
    )
    config_error: Optional[str] = Field(
        default=None,
        description=(
            "Why the config could not be read. The run is still importable "
            "without it: steps and samples come from filenames."
        ),
    )


class RunListResponse(BaseModel):
    """Body of ``GET /model-folders/{folder_id}/runs``."""

    model_config = ConfigDict(extra="allow")

    runs: list[RunResponse]


class ImportRequest(BaseModel):
    """Body of ``POST /model-imports``."""

    model_config = ConfigDict(extra="forbid")

    source_folder_id: int = Field(
        description="A registered `source` folder — an ai-toolkit output root."
    )
    run_name: str = Field(
        description=(
            "A run inside that folder, as `GET .../runs` named it. A name, never "
            "a path: the server joins it to the registered root and refuses "
            "anything that resolves outside."
        )
    )
    destination_folder_id: int = Field(
        description="A registered folder the shelf catalogues. Never a `source` one."
    )
    steps: Optional[list[Optional[int]]] = Field(
        default=None,
        description=(
            "Which checkpoints to take, by step, with `null` for the bare final. "
            "Omit for the whole run."
        ),
    )


class ImportedFile(BaseModel):
    """What happened to one checkpoint."""

    model_config = ConfigDict(extra="allow")

    filename: str
    step: Optional[int] = None
    status: str = Field(description="`imported`, `failed`, or `cancelled`.")
    model_id: Optional[int] = Field(
        default=None, description="The hub `model.id` the file landed on."
    )
    detail: Optional[str] = Field(default=None, description="Why, when it failed.")


class ImportResponse(BaseModel):
    """Body of ``POST /model-imports``."""

    model_config = ConfigDict(extra="allow")

    run_name: str
    stack_id: Optional[int] = Field(
        default=None,
        description=(
            "The `adapter_stack` the run's steps landed in, so the whole run "
            "reads as one shelf row with an expandable step strip."
        ),
    )
    deleted_source: bool = Field(
        description=(
            "Whether the run's own files were removed after each row was "
            "committed. Follows the source folder's `delete_after_import`; the "
            "unlink is always the last step, never the first."
        )
    )
    files: list[ImportedFile] = Field(default_factory=list)


def _to_run_response(run) -> RunResponse:
    return RunResponse(
        name=run.name,
        checkpoints=[
            RunCheckpoint(
                filename=checkpoint.filename,
                step=checkpoint.step,
                size=_size_of(checkpoint.path),
            )
            for checkpoint in run.checkpoints
        ],
        samples=[
            RunSample(filename=sample.filename, step=sample.step, index=sample.index)
            for sample in run.samples
        ],
        base_model=run.base_model,
        trigger_words=list(run.trigger_words),
        rank=run.rank,
        config_error=run.config_error,
    )


def _size_of(path: str) -> Optional[int]:
    try:
        return os.path.getsize(path)
    except OSError as exc:
        logger.warning(
            "Could not stat run checkpoint %s: %s. Reporting it with no size "
            "rather than dropping it from the run.",
            path,
            exc,
        )
        return None


def create_router(server) -> APIRouter:
    """Create the ai-toolkit import router.

    Args:
        server: The Server instance, for ``hub`` (the shelf tables) and ``auth``.

    Returns:
        The configured router.
    """
    router = APIRouter()

    def _source_folder(folder_id: int) -> dict:
        row = server.hub.fetchone(
            "SELECT id, path, kind, delete_after_import FROM model_folder WHERE id = ?",
            (folder_id,),
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Model folder not found.")
        if row["kind"] != SOURCE_FOLDER_KIND:
            raise HTTPException(
                status_code=400,
                detail=(
                    "That folder is catalogued in place, not taken from. Only a "
                    "`source` folder holds importable runs."
                ),
            )
        return dict(row)

    @router.get(
        "/model-folders/{folder_id}/runs",
        summary="List the training runs in an ai-toolkit output folder",
        description=(
            "Describes every run under a registered `source` folder: its steps, "
            "its previews, and what its config says it was trained against. "
            "**Nothing is hashed, copied, moved or written** — the whole card "
            "grid can be drawn before the user decides about any of it."
        ),
        tags=["model_shelf"],
        response_model=RunListResponse,
    )
    def list_runs(folder_id: int, request: Request):
        server.auth.ensure_secure_when_required(request)
        folder = _source_folder(folder_id)
        try:
            runs = read_output_root(folder["path"])
        except (NotADirectoryError, OSError) as exc:
            logger.warning(
                "Could not list ai-toolkit output root %s (folder id=%s): %s",
                folder["path"],
                folder_id,
                exc,
            )
            raise HTTPException(
                status_code=409,
                detail=f"Could not read {folder['path']}: {exc}",
            ) from exc
        return RunListResponse(runs=[_to_run_response(run) for run in runs])

    @router.post(
        "/model-imports",
        summary="Import a training run onto the shelf",
        description=(
            "Copies the selected checkpoints into a folder the shelf catalogues "
            "and registers them as one stack. Per file the order is copy, verify "
            "by SHA-256, register the row and commit, and only then unlink — so "
            "an interruption leaves a duplicate, never a row naming a file that "
            "is gone. The run's own files are removed only when the source "
            "folder carries `delete_after_import`."
        ),
        tags=["model_shelf"],
        response_model=ImportResponse,
    )
    def import_run(request: Request, payload: ImportRequest = Body(...)):
        server.auth.ensure_secure_when_required(request)
        folder = _source_folder(payload.source_folder_id)
        try:
            # A name, joined to the registered root and contained: the caller
            # never supplies a host path, and `../..` in a run name reads a
            # folder nobody registered.
            run_dir = resolve_path_within(folder["path"], payload.run_name)
        except ValueError as exc:
            logger.error(
                "Refusing to import %r from %s: it resolves outside the "
                "registered output root (%s).",
                payload.run_name,
                folder["path"],
                exc,
            )
            raise HTTPException(
                status_code=400,
                detail=f"{payload.run_name!r} is not a run inside that folder.",
            ) from exc

        delete_source = bool(folder["delete_after_import"])
        # The *same* slot a move takes, not an import-only one. Two separate
        # locks serialized each operation against itself and neither against the
        # other, so a move and an import could both find one destination
        # filename free and whichever wrote second won in silence.
        if not SHELF_IO_LOCK.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A move or an import is already running. Two at once would "
                    "race for the free space and the filenames each of them "
                    "checked before starting."
                ),
            )
        try:
            report = RunImporter(server.hub).import_run(
                run_dir,
                payload.destination_folder_id,
                steps=payload.steps,
                delete_source=delete_source,
            )
        except NotADirectoryError as exc:
            raise HTTPException(
                status_code=404, detail=f"No run named {payload.run_name!r}."
            ) from exc
        except MoveRefused as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        finally:
            SHELF_IO_LOCK.release()

        return ImportResponse(
            run_name=report.run_name,
            stack_id=report.stack_id,
            deleted_source=delete_source,
            files=[
                ImportedFile(
                    filename=outcome.filename,
                    step=outcome.step,
                    status=outcome.status,
                    model_id=outcome.model_id,
                    detail=outcome.detail,
                )
                for outcome in report.outcomes
            ],
        )

    return router
