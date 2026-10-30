"""Seed the Playwright e2e backend's reference folder (#2320).

Called by ``Server`` at start-up, and only when ``enable_test_hooks`` is on;
nothing else imports it. Lives here rather than in ``routes/test_hooks.py``
because it writes the vault directly.
"""

import os
import time

from sqlmodel import Session, select

from pixlstash.db_models.project import Project
from pixlstash.db_models.reference_folder import ReferenceFolder, ReferenceFolderStatus
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.reference_folder_scan_task import ReferenceFolderScanTask
from pixlstash.utils.library_layout import DEFAULT_LAYOUT, format_layout

logger = get_logger(__name__)


def register_seed_folder_in_session(
    session: Session, path: str
) -> tuple[int, str | None]:
    """Insert the laid-out reference folder; return its id and the first project.

    The project name is ``None`` when the library has no project.
    """
    rf = ReferenceFolder(
        folder=path,
        label=os.path.basename(path),
        status=ReferenceFolderStatus.ACTIVE,
        # Outside the finder's rescan interval, so the background scan does not
        # race the seed's own two scans and import every file twice.
        last_scanned=time.time(),
        layout=format_layout(DEFAULT_LAYOUT),
    )
    session.add(rf)
    session.commit()
    session.refresh(rf)
    project = session.exec(select(Project).order_by(Project.id)).first()
    return rf.id, project.name if project is not None else None


def seed_reference_folder(server, seed: dict) -> None:
    """Register the e2e launcher's reference folder and leave one move pending.

    *seed* is ``{"path": <folder>, "move": <file relative to it>}``. The folder
    gets the default layout (a layout cannot be set over HTTP, and only a
    laid-out folder queues a move for review) and is scanned synchronously.
    The ``move`` file is then renamed into a folder named after the library's
    first project and scanned again, so ``GET /moves/pending`` lists one
    unambiguous review that adds no entity to the fixture. Called at start-up,
    only when ``enable_test_hooks`` is on. A failure is logged rather than
    raised: the Media fixture spec then fails on its own assertions, and every
    other spec still gets a server.
    """
    path = os.path.realpath(seed["path"])
    try:
        folder_id, project_name = server.vault.db.run_task(
            register_seed_folder_in_session, path
        )
        if project_name is None:
            raise ValueError("the fixture has no project to move a picture into")

        def scan():
            return ReferenceFolderScanTask(
                database=server.vault.db,
                folder_id=folder_id,
                folder_path=path,
                resolved_path=path,
            )._run_task()

        imported = scan()["imported_picture_ids"]
        source = os.path.join(path, seed["move"])
        target = os.path.join(path, project_name, os.path.basename(source))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.rename(source, target)
        queued = scan()["external_moves_queued_for_review"]
        # Watched only now, so the seed's own rename is not picked up twice.
        server.vault.watch_reference_folder(folder_id, path)
    except Exception as exc:
        logger.error(
            "[test-hooks] could not seed the e2e reference folder %s: %s",
            path,
            exc,
            exc_info=True,
        )
        return
    logger.info(
        "[test-hooks] seeded e2e reference folder %s (id=%s): %d pictures, "
        "%d moves pending review",
        path,
        folder_id,
        len(imported),
        len(queued),
    )
