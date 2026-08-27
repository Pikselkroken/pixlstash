"""Look at what a training run produced, then take it onto the shelf.

Two routes, and the split between them is the whole point: **listing a run costs
nothing and changes nothing.** ``GET /model-folders/{id}/runs`` reads filenames
and one ``config.yaml`` per run - it does not hash, copy, move or write anything,
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

**A run's previews come with it**, into ``<stem>_samples/`` beside each imported
checkpoint, and ``GET /models/{model_id}/samples`` reads them back off the shelf.
That pair is addressed by ``model.id`` rather than by sha256, because a
checkpoint nobody has hashed has no sha256 to be addressed by.

Authorization: every route here is `LOCAL_OWNER_ONLY`, declared in
``pixlstash/authz/registry.py``. The listing walks a registered host path and
reads every run under it, which is the same authority as
``model-folders/{id}/rescan``; the import writes files into one registered folder
and may unlink them from another, which is the ``model-moves`` authority; and
both sample routes read inside a registered folder, which is the authority
``GET /adapters/{sha256}/file`` sits on for the same reason - bytes out of a
registered root are a capability of their own, and "no host path crosses the
wire" is not the argument (recorded against that route on 2026-08-11).
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_folder_scanner import STATE_PRESENT
from pixlstash.services.model_mover import SHELF_IO_LOCK, MoveRefused, samples_relpath
from pixlstash.services.run_importer import RunImporter
from pixlstash.utils.aitoolkit_run import (
    SAMPLES_DIRNAME,
    is_sample_filename,
    read_output_root,
)
from pixlstash.utils.path_utils import resolve_path_within

logger = get_logger(__name__)

SOURCE_FOLDER_KIND = "source"

# What a run's ``samples/`` directory may be served as. An allowlist rather than
# ``mimetypes.guess_type``: the directory is on the owner's disk and anything
# could have been dropped into it, and guessing would let an ``.html`` be served
# from our own origin. ai-toolkit writes JPEG and PNG; WebP is here because a
# run configured for it writes that instead.
SAMPLE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


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


class ModelSamplesResponse(BaseModel):
    """Body of ``GET /models/{model_id}/samples``."""

    model_config = ConfigDict(extra="allow")

    samples: list[str] = Field(
        default_factory=list,
        description=(
            "Filenames in this checkpoint's `<stem>_samples/` directory, sorted, "
            "carrying the trainer's own names. Empty for a model that was not "
            "imported from a run, or whose run had no previews."
        ),
    )


class ImportRequest(BaseModel):
    """Body of ``POST /model-imports``."""

    model_config = ConfigDict(extra="forbid")

    source_folder_id: int = Field(
        description="A registered `source` folder - an ai-toolkit output root."
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
    detail: Optional[str] = Field(
        default=None,
        description=(
            "Why, when it failed - and, on a file that imported, why its "
            "previews did not come with it."
        ),
    )
    sample_count: int = Field(
        default=0,
        description=(
            "Previews copied into this checkpoint's `<stem>_samples/` directory. "
            "Zero for a run with no samples, and for a copy that failed - "
            "`detail` says which."
        ),
    )


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


def sample_path_within(run_dir: str, filename: str) -> str:
    """Resolve one sample filename inside a run, refusing anything that escapes.

    A named function rather than two lines inside the handler, because it is the
    only place on the shelf where a caller-supplied name becomes a path whose
    *bytes* are served - and because over HTTP it is nearly untestable. Both
    route segments are single URL path segments and Starlette percent-decodes
    before matching, so ``{filename}`` is structurally incapable of carrying a
    ``/``: on POSIX there is no reachable traversal through the route at all.
    On Windows, where a backslash is an ordinary URL character and a path
    separator, there is. Exposing the join lets the refusal be asserted on every
    platform instead of only on the one where an attacker could reach it.

    **Two calls, and neither collapses into the other.**

    The second contains the filename against the SAMPLES DIRECTORY rather than
    against the run, because a single run-level join would let
    ``samples/../config.yaml`` through: it lands inside the run, so a run-level
    check passes it, and it is a file this route has no business serving.

    The first contains the samples directory itself, and it exists because
    ``resolve_path_within`` derives its safe base by ``realpath``-ing the base it
    is *handed*. Passing ``run_dir/samples`` directly therefore makes a
    **symlinked** ``samples`` its own safe base, and every file under the link
    target passes containment - an arbitrary-image reader for any allowlisted
    extension. That is not hypothetical for a ``source`` folder: unlike every
    other registered path, its contents are third-party tool output the owner
    merely pointed at, and both tarballs and git repositories carry symlinks. A
    directory symlink or an NTFS junction named ``samples`` does the same on
    Windows. Found by the adversarial review of this PR and reproduced
    end-to-end; the sibling joins were already safe (a symlinked *run* is caught
    here, a symlinked *file* inside a real ``samples/`` by the second call).

    Args:
        run_dir: The run's directory, itself already contained within a
            registered output root.
        filename: The caller-supplied name, from the path.

    Returns:
        The resolved absolute path to the sample.

    Raises:
        ValueError: If ``samples/`` or the name resolves outside the run.
    """
    samples_dir = resolve_path_within(run_dir, SAMPLES_DIRNAME)
    return resolve_path_within(samples_dir, filename)


def model_sample_path_within(folder_path: str, relpath: str, filename: str) -> str:
    """Resolve one imported sample, refusing anything that escapes its folder.

    The shelf-side twin of :func:`sample_path_within`, and it needs **both** of
    that function's joins for the same two reasons. The samples directory is
    contained against the registered ``model_folder.path`` first, because
    ``resolve_path_within`` realpaths the base it is handed and passing the
    derived directory straight in would make a *symlinked* ``<stem>_samples``
    its own safe base - an arbitrary-image reader for any allowlisted extension.
    The filename is then contained against that resolved directory rather than
    against the folder, because a single folder-level join would let
    ``../alice.safetensors`` through: it lands inside the registered folder, so
    a folder-level check passes it, and it is not a file this route serves.

    Args:
        folder_path: The registered model folder holding the checkpoint.
        relpath: The checkpoint's ``model_file.relpath`` in that folder.
        filename: The caller-supplied name, from the URL path.

    Returns:
        The resolved absolute path to the sample.

    Raises:
        ValueError: If the samples directory or the name resolves outside the
            registered folder.
    """
    samples_dir = resolve_path_within(folder_path, samples_relpath(relpath))
    return resolve_path_within(samples_dir, filename)


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
            "**Nothing is hashed, copied, moved or written** - the whole card "
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

    @router.get(
        "/model-folders/{folder_id}/runs/{run_name}/samples/{filename}",
        summary="One preview image from a training run",
        description=(
            "Serves a sample ai-toolkit rendered during the run, so a step can "
            "be judged before it is imported. Reads and changes nothing else - "
            "the same promise the run listing makes.\n\n"
            "**Both path segments are names, never paths.** The run name is "
            "joined to the registered output root and the filename to that "
            "run's `samples/` directory, and each join is contained: a segment "
            "that resolves outside is refused rather than read. The caller "
            "therefore cannot address a file outside a folder the owner "
            "registered, which is what keeps this from being an arbitrary-file "
            "reader."
        ),
        tags=["model_shelf"],
        response_class=FileResponse,
        responses={200: {"content": {"image/*": {}}}},
    )
    def get_run_sample(folder_id: int, run_name: str, filename: str, request: Request):
        server.auth.ensure_secure_when_required(request)
        folder = _source_folder(folder_id)
        try:
            run_dir = resolve_path_within(folder["path"], run_name)
            sample_path = sample_path_within(run_dir, filename)
        except ValueError as exc:
            logger.error(
                "Refusing to serve sample %r of run %r under folder %s: it "
                "resolves outside the registered output root (%s).",
                filename,
                run_name,
                folder_id,
                exc,
            )
            raise HTTPException(status_code=400, detail="No such sample.") from exc

        media_type = SAMPLE_MEDIA_TYPES.get(os.path.splitext(sample_path)[1].lower())
        if media_type is None:
            # An allowlist, not a guess. `mimetypes` would happily label an
            # `.html` dropped in `samples/` as text/html and serve it from our
            # origin; the run only ever writes the three formats below.
            logger.warning(
                "Refusing to serve %s: %r is not one of the image formats a "
                "run's samples directory holds.",
                sample_path,
                os.path.splitext(sample_path)[1],
            )
            raise HTTPException(status_code=400, detail="No such sample.")
        if not os.path.isfile(sample_path):
            raise HTTPException(status_code=404, detail="No such sample.")
        return FileResponse(sample_path, media_type=media_type)

    def _samples_location(model_id: int) -> Optional[dict]:
        """The first present copy of a model whose samples directory is there.

        A model can hold several ``model_file`` rows - the shelf's whole
        content/location split - and only one of them need have travelled with
        its previews. Ordered by ``(model_folder_id, relpath)``, which **is**
        ``model_file``'s primary key (``hub/schema.py``) - the table has no
        ``id`` column - so the answer is stable rather than whatever SQLite
        hands back first. Spelled out because a reviewer read "the location
        primary key" as ``mf.id`` and called the ordering a mismatch.

        Raises:
            HTTPException: 404 when no such model row exists. A model that
                exists and has no samples is not an error; it is an empty list.
        """
        if server.hub.fetchone("SELECT 1 FROM model WHERE id = ?", (model_id,)) is None:
            raise HTTPException(status_code=404, detail="No such model.")
        for row in server.hub.fetchall(
            "SELECT mf.relpath, f.path AS folder_path FROM model_file mf "
            "JOIN model_folder f ON f.id = mf.model_folder_id "
            "WHERE mf.model_id = ? AND mf.state = ? "
            "ORDER BY mf.model_folder_id, mf.relpath",
            (model_id, STATE_PRESENT),
        ):
            try:
                directory = resolve_path_within(
                    row["folder_path"], samples_relpath(row["relpath"])
                )
            except ValueError as exc:
                # A relpath that escapes its registered folder is a broken row,
                # not a request to read outside the shelf.
                logger.error(
                    "Refusing the samples of model %s at %r in %s: the derived "
                    "directory resolves outside the registered folder (%s).",
                    model_id,
                    row["relpath"],
                    row["folder_path"],
                    exc,
                )
                continue
            if os.path.isdir(directory):
                return {**dict(row), "directory": directory}
        return None

    @router.get(
        "/models/{model_id}/samples",
        summary="The training previews stored beside one imported checkpoint",
        description=(
            "Lists the filenames in that model's `<stem>_samples/` directory - "
            "the previews its training run rendered, copied in with the "
            "checkpoint and carried along by a later move. Filtered to the "
            "image extensions the byte route serves, so an unrelated file "
            "dropped into the directory is not advertised - the filter is on "
            "the name, so a symlink whose target carries a different extension "
            "can still be listed and then refused. An empty list for a model "
            "that was not imported from a run."
        ),
        tags=["model_shelf"],
        response_model=ModelSamplesResponse,
    )
    def list_model_samples(model_id: int, request: Request):
        server.auth.ensure_secure_when_required(request)
        location = _samples_location(model_id)
        if location is None:
            return ModelSamplesResponse(samples=[])
        try:
            names = os.listdir(location["directory"])
        except OSError as exc:
            logger.warning(
                "Could not list the samples of model %s in %s: %s",
                model_id,
                location["directory"],
                exc,
            )
            raise HTTPException(
                status_code=409, detail="Could not read this model's samples."
            ) from exc
        # `is_sample_filename`, not merely an image extension: it is the same
        # test the delete verb uses to decide the directory is the model's, so
        # all three verbs agree on what a sample is. Without it this route
        # serves any image the owner happened to leave in a directory whose
        # name matched, and the response would be describing something other
        # than the run's previews.
        return ModelSamplesResponse(
            samples=sorted(name for name in names if is_sample_filename(name))
        )

    @router.get(
        "/models/{model_id}/samples/{filename}",
        summary="One training preview stored beside an imported checkpoint",
        description=(
            "Serves one image out of that model's `<stem>_samples/` directory.\n\n"
            "**`filename` is a name, never a path.** It is joined to the "
            "directory derived from the model's own registered location and "
            "contained against it, so a name that resolves outside is refused "
            "rather than read, and the extension is checked against an allowlist "
            "so nothing but an image is served from our origin."
        ),
        tags=["model_shelf"],
        response_class=FileResponse,
        responses={200: {"content": {"image/*": {}}}},
    )
    def get_model_sample(model_id: int, filename: str, request: Request):
        server.auth.ensure_secure_when_required(request)
        location = _samples_location(model_id)
        if location is None:
            raise HTTPException(status_code=404, detail="No such sample.")
        try:
            sample_path = model_sample_path_within(
                location["folder_path"], location["relpath"], filename
            )
        except ValueError as exc:
            logger.error(
                "Refusing to serve sample %r of model %s: it resolves outside "
                "the registered model folder (%s).",
                filename,
                model_id,
                exc,
            )
            raise HTTPException(status_code=400, detail="No such sample.") from exc

        media_type = SAMPLE_MEDIA_TYPES.get(os.path.splitext(sample_path)[1].lower())
        if media_type is None or not is_sample_filename(os.path.basename(sample_path)):
            # Two tests, and the second is why this route cannot be used to read
            # the owner's own pictures out of a directory whose name happened to
            # match: it serves what the listing lists and nothing else.
            logger.warning(
                "Refusing to serve %s: it is not one of the previews a training "
                "run writes.",
                sample_path,
            )
            raise HTTPException(status_code=400, detail="No such sample.")
        if not os.path.isfile(sample_path):
            raise HTTPException(status_code=404, detail="No such sample.")
        return FileResponse(sample_path, media_type=media_type)

    @router.post(
        "/model-imports",
        summary="Import a training run onto the shelf",
        description=(
            "Copies the selected checkpoints into a folder the shelf catalogues "
            "and registers them as one stack. Per file the order is copy, verify "
            "by SHA-256, register the row and commit, and only then unlink - so "
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
                    sample_count=outcome.sample_count,
                )
                for outcome in report.outcomes
            ],
        )

    return router
