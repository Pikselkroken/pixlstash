import json
import os
import threading

from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture
from pixlstash.hub.workflows import record_api_graph
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import HASH_VERSION, WorkflowGraphError
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.utils.comfyui_utilities import (
    extract_comfy_workflow_info,
    find_comfy_api_prompt,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils


logger = get_logger(__name__)


class ComfyUIExtractionTask(BaseTask):
    """Backfill task: read ComfyUI workflow metadata from existing picture files and store in DB.

    Runs once per picture on first startup after the 0005 migration.  Any picture
    where new ComfyUI data is found has its text_embedding cleared so that the
    TextEmbeddingTask will regenerate it with the full workflow context.

    It also files the picture's workflow in the **hub** (plan §B3). That happens
    here rather than in a backfill of its own because this task has already
    opened the file and parsed its chunks: a second pass would re-read every
    image in the library to parse what this one just parsed, and would have to
    reinvent the resumability, cancellation, progress and finder it inherits by
    being here. The hub rows are content-addressed and deliberately outlive the
    picture, which is the point of the whole feature -- otherwise dehydrating a
    stack would delete the graph its own rehydrate promise depends on.

    Without a hub (the CLI tools, most tests) the workflow columns are left
    alone, so a vault that later gains one is scanned then rather than being
    marked scanned with nothing recorded.
    """

    BATCH_SIZE = 32

    def __init__(
        self,
        database,
        image_root: str,
        pictures: list[Picture],
        hub=None,
        on_hub_failure=None,
    ):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="ComfyUIExtractionTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._image_root = image_root
        self._pictures = pictures or []
        self._hub = hub
        self._on_hub_failure = on_hub_failure
        self._stop_event = threading.Event()

    def on_cancel(self) -> None:
        self._stop_event.set()

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def _run_task(self):
        if not self._pictures:
            return {"checked": 0, "found_comfyui": 0, "found_workflow": 0}

        picture_ids = [pic.id for pic in self._pictures]

        def fetch_fresh(session: Session, ids: list[int]) -> list[Picture]:
            return session.exec(
                select(Picture)
                .where(Picture.id.in_(ids))
                .options(
                    load_only(
                        Picture.id,
                        Picture.file_path,
                        Picture.comfyui_models,
                    )
                )
            ).all()

        fresh_pictures = self._db.run_immediate_read_task(fetch_fresh, picture_ids)

        # (picture_id, positive_prompt, models_json, loras_json, clear_embedding,
        #  write_comfyui)
        #
        # `write_comfyui` is False for a picture a PRE-B3 run already extracted
        # and which is back here only for the workflow scan. Widening the
        # finder's predicate to `workflow_hash_version IS NULL` re-queues every
        # such picture on a library's first B3 start, and rewriting its ComfyUI
        # columns from a second read is not a no-op: a file that has since moved
        # or been stripped replaces real stored models and LoRAs with the "[]"
        # sentinel. The extraction happened once; the revisit only adds keys.
        updates: list[tuple] = []
        # (picture_id, topology_hash, structural_hash, instance_hash). Absent from
        # this list when it was NOT scanned for a workflow -- no hub attached, or
        # a hub write that failed -- so `workflow_hash_version IS NULL` keeps
        # meaning "never scanned" and the finder hands it back later.
        workflow_updates: list[tuple] = []
        checked = 0
        found_comfyui = 0

        for pic in fresh_pictures:
            if self._stop_event.is_set():
                logger.debug(
                    "ComfyUIExtractionTask cancelled, stopping early at task %s",
                    self.id,
                )
                break
            resolved = ImageUtils.resolve_picture_path(self._image_root, pic.file_path)

            # Already extracted by a pre-B3 run: this visit is for the workflow
            # keys alone and must leave the ComfyUI columns exactly as they are.
            write_comfyui = pic.comfyui_models is None

            if not resolved or not os.path.exists(resolved):
                # Write the sentinel so the finder never re-queues this picture.
                updates.append((pic.id, None, "[]", "[]", False, write_comfyui))
                self._record(workflow_updates, pic.id, None)
                checked += 1
                continue

            if VideoUtils.is_video_file(resolved):
                # Videos cannot contain ComfyUI metadata; mark as done.
                updates.append((pic.id, None, "[]", "[]", False, write_comfyui))
                self._record(workflow_updates, pic.id, None)
                checked += 1
                continue

            positive_prompt = None
            models = []
            loras = []
            embedded_metadata = None
            try:
                embedded_metadata = ImageUtils.extract_embedded_metadata(resolved)
                # Skipped on a revisit: the answer is already in the row, and
                # this is the expensive half of the parse.
                workflow_info = (
                    extract_comfy_workflow_info(embedded_metadata)
                    if write_comfyui
                    else None
                )
                if workflow_info:
                    positive_prompt = workflow_info.get("positive_prompt") or None
                    models = workflow_info.get("models") or []
                    loras = workflow_info.get("loras") or []
            except Exception as exc:
                logger.debug(
                    "ComfyUIExtractionTask: extraction failed for picture %s (%s): %s",
                    pic.id,
                    resolved,
                    exc,
                )

            # Always write at least "[]" so comfyui_models IS NULL remains the
            # "not yet checked" sentinel and this picture is never revisited.
            models_json = json.dumps(models)
            loras_json = json.dumps(loras)

            had_comfyui = bool(positive_prompt or models or loras)
            if had_comfyui:
                found_comfyui += 1

            updates.append(
                (
                    pic.id,
                    positive_prompt,
                    models_json,
                    loras_json,
                    had_comfyui,
                    write_comfyui,
                )
            )
            # Same file read, same parsed chunks: the hub write is the only
            # extra work, and it is a no-op for a graph already filed.
            self._record(workflow_updates, pic.id, embedded_metadata)
            checked += 1

        if not updates:
            return {"checked": 0, "found_comfyui": 0, "found_workflow": 0}

        scanned_workflows = {
            pid: (topology, structural, instance)
            for pid, topology, structural, instance in workflow_updates
        }

        def persist(session: Session, rows: list[tuple]):
            for (
                pid,
                pos_prompt,
                models_json,
                loras_json,
                clear_embedding,
                write_comfyui,
            ) in rows:
                db_pic = session.get(Picture, pid)
                if db_pic is None:
                    continue
                if write_comfyui:
                    if pos_prompt is not None:
                        db_pic.comfyui_positive_prompt = pos_prompt
                    # Always write the sentinel ("[]" at minimum) so this picture
                    # is never re-queued by the finder.
                    db_pic.comfyui_models = models_json
                    db_pic.comfyui_loras = loras_json
                    # Clear the existing embedding so TextEmbeddingTask
                    # regenerates it with the newly stored ComfyUI context.
                    if clear_embedding:
                        db_pic.text_embedding = None
                if pid in scanned_workflows:
                    topology, structural, instance = scanned_workflows[pid]
                    db_pic.workflow_topology_hash = topology
                    db_pic.workflow_structural_hash = structural
                    db_pic.workflow_instance_hash = instance
                    # Set last: it is the marker that the other three are final.
                    db_pic.workflow_hash_version = HASH_VERSION
                session.add(db_pic)
            session.commit()

        self._db.run_task(persist, updates, priority=DBPriority.LOW)
        found_workflow = sum(
            1
            for topology, _structural, _instance in scanned_workflows.values()
            if topology
        )
        logger.debug(
            "ComfyUIExtractionTask: checked=%s, found_comfyui=%s, found_workflow=%s",
            checked,
            found_comfyui,
            found_workflow,
        )
        return {
            "checked": checked,
            "found_comfyui": found_comfyui,
            "found_workflow": found_workflow,
        }

    def _record(self, workflow_updates: list, picture_id: int, embedded_metadata):
        """File the picture's embedded API graph in the hub, if there is one.

        The rule the two outcomes turn on: **a property of the picture marks it
        scanned; a failure of our own machinery does not.** No graph, an
        unreadable file, a video and a graph the hash layer refuses are all
        facts about the picture and will not change on a re-read, so the marker
        goes down and the finder stops offering it. A hub that could not be
        written is neither, so the picture is left unmarked -- "this could not
        be filed" and "this picture has no workflow" must never be the same row.

        Appends ``(picture_id, topology_hash, structural_hash, instance_hash)``
        when the picture was scanned, and appends nothing when it was not.

        Args:
            workflow_updates: Accumulator for the batch's persist step.
            picture_id: The picture being scanned, for the log lines.
            embedded_metadata: What ``extract_embedded_metadata`` returned, or
                ``None`` for a picture whose file was never opened.
        """
        if self._hub is None:
            return
        try:
            api_graph = find_comfy_api_prompt(embedded_metadata)
            if api_graph is None:
                # No executable `prompt` chunk: an imported JPEG, an A1111 PNG,
                # or a file whose metadata was stripped. Normal, not a failure,
                # and roughly a third of a real library.
                workflow_updates.append((picture_id, None, None, None))
                return
            keys = record_api_graph(self._hub, api_graph)
        except WorkflowGraphError as exc:
            # A graph WAS embedded and the hash layer refused it -- malformed
            # `class_type`, non-mapping `inputs`, a cycle. That is permanent, so
            # the picture is marked rather than handed back forever, but it is
            # NOT the same event as carrying no graph and does not get the same
            # silence: the library under-counts by one and somebody has to be
            # able to see why.
            logger.warning(
                "ComfyUIExtractionTask: picture %s carries a workflow the hash "
                "layer refused, so it is filed as having none: %s",
                picture_id,
                exc,
            )
            workflow_updates.append((picture_id, None, None, None))
            return
        except Exception as exc:
            self._stand_down(picture_id, exc)
            return
        workflow_updates.append(
            (
                picture_id,
                keys.topology_hash,
                keys.structural_hash,
                keys.instance_hash,
            )
        )

    def _stand_down(self, picture_id: int, exc: Exception) -> None:
        """Give up on the workflow scan for the rest of this process.

        A hub that cannot be written is not a per-picture problem, and leaving
        the pictures unmarked would otherwise make the finder re-open, re-decode
        and re-parse every image in the library on every planning cycle, forever
        -- reproduced against a read-only hub. So the first failure tells the
        finder to fall back to its pre-B3 predicate, which drains and goes quiet
        exactly as it did before this task learned to hash. One log line, not
        one per picture per cycle. A restart is what tries again, because a
        restart is what proves the hub is writable.
        """
        self._hub = None
        logger.error(
            "ComfyUIExtractionTask: the hub refused picture %s's workflow, so "
            "the workflow scan is standing down for this process and those "
            "pictures stay unscanned. Restart once the hub is writable: %s",
            picture_id,
            exc,
        )
        if self._on_hub_failure is not None:
            self._on_hub_failure()
