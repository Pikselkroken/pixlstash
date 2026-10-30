"""The e2e backend's workflow seed (#2314): workflows, pictures and recipes.

The Workflows specs run against the real API, so the e2e library needs
workflow data. ``frontend/e2e/serve_e2e_backend.py`` writes the seed under its
throwaway work dir and names it as ``e2e_workflow_seed``; the server calls
:func:`seed_workflows` at start-up, only when ``enable_test_hooks`` is on.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

from pixlstash.hub.workflow_cards import unidentified_variants
from pixlstash.hub.workflow_group_writes import set_group_attributes
from pixlstash.pixl_logging import get_logger
from pixlstash.routes.comfyui import store_manual_workflow
from pixlstash.routes.pictures._helpers import _create_picture_imports
from pixlstash.services import saved_recipe_service
from pixlstash.tasks.comfyui_extraction_task import ComfyUIExtractionTask
from pixlstash.tasks.workflow_card_backfill_task import WorkflowCardBackfillTask

logger = get_logger(__name__)


def add_pictures_in_session(session, pictures: list) -> list[int]:
    """Insert the seeded pictures; return their ids.

    No pending-tag marker, unlike an upload: with the workers off nothing
    would ever clear it.
    """
    session.add_all(pictures)
    session.commit()
    return [picture.id for picture in pictures]


def seed_workflows(server, path: str) -> None:
    """Import the e2e launcher's workflow seed: workflows, pictures, recipes.

    *path* holds ``seed.json`` and the PNGs it names
    (``serve_e2e_backend._seed_workflows``). Each piece goes through the path
    the app's own takes: ``store_manual_workflow`` as an import does, the
    picture importer's ``_create_picture_imports``, then the ComfyUI extraction
    and the card backfill that file the pictures under a workflow - run here
    synchronously because the e2e backend's background workers are off - and
    the recipe service ``POST /recipes`` saves through. Called at start-up,
    only when ``enable_test_hooks`` is on; a failure is logged rather than
    raised, like ``test_hooks.seed_model_folder``.
    """
    try:
        with open(os.path.join(path, "seed.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        library_uuid = server.vault.library_uuid
        ids = {}
        for entry in manifest["workflows"]:
            ids[entry["name"]] = store_manual_workflow(
                server.hub, entry["name"], entry["graph"], "import"
            )
            if entry.get("hidden"):
                set_group_attributes(
                    server.hub,
                    ids[entry["name"]],
                    hidden=True,
                    library_uuid=library_uuid,
                )

        files = []
        for name in manifest["pictures"]:
            with open(os.path.join(path, name), "rb") as handle:
                files.append((handle.read(), os.path.splitext(name)[1], name))
        _, _, _, new_pictures = _create_picture_imports(
            server, files, server.vault.image_root
        )
        pictures = list(new_pictures.values())
        if len(pictures) != len(files):
            logger.warning(
                "[test-hooks] %d of the %d seeded pictures were already in the "
                "vault and are not seeded again",
                len(files) - len(pictures),
                len(files),
            )
        # The fixture's median date (the launcher reads it off the vault), so
        # the seeded pictures sit mid-grid in either date order and the specs
        # that open the first or the last pictures still get fixture ones.
        dated = datetime.fromisoformat(manifest["picture_date"])
        if dated.tzinfo is None:
            dated = dated.replace(tzinfo=timezone.utc)
        for picture in pictures:
            picture.created_at = picture.imported_at = dated

        picture_ids = server.vault.db.run_task(add_pictures_in_session, pictures)
        ComfyUIExtractionTask(
            database=server.vault.db,
            image_root=server.vault.image_root,
            pictures=[SimpleNamespace(id=picture_id) for picture_id in picture_ids],
            hub=server.hub,
            library_uuid=library_uuid,
        )._run_task()
        WorkflowCardBackfillTask(
            hub=server.hub,
            structural_hashes=unidentified_variants(
                server.hub, WorkflowCardBackfillTask.BATCH_SIZE
            ),
        )._run_task()

        for entry in manifest["recipes"]:
            saved_on = ids[entry["workflow"]]
            saved_recipe_service.create_recipe(
                server.vault,
                {
                    "name": entry["name"],
                    "prompt": entry.get("prompt", ""),
                    "loras": entry.get("loras", []),
                    # A manual workflow's id is its card's key. Unfiled is a
                    # recipe on no workflow, still naming the card it came
                    # from, so it can be extracted.
                    "workflow_key": saved_on,
                    "workflow_id": None if entry.get("unfiled") else saved_on,
                },
            )
    except Exception as exc:
        logger.error(
            "[test-hooks] could not seed the e2e workflows from %s: %s",
            path,
            exc,
            exc_info=True,
        )
        return
    logger.info(
        "[test-hooks] seeded e2e workflows from %s: %d workflows, %d pictures, "
        "%d recipes",
        path,
        len(ids),
        len(picture_ids),
        len(manifest["recipes"]),
    )
