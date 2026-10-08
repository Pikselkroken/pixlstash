"""The one pull of ComfyUI's saved workflows a server runs at a time.

Three things start a pull: the minute poll
(:class:`~pixlstash.tasks.comfyui_workflow_poll_finder.ComfyUIWorkflowPollFinder`),
the last step of a Link, and ``POST /comfyui/workflows/pull``. They share this
gate, so a second is refused while one is queued or running, and
``GET /comfyui/workflows/pull`` reads whichever ran last. Run and Open check one
file (:meth:`WorkflowPulls.freshen`) outside the gate: that is one entry, under
the same ``INBOX_LOCK`` every store takes.

One instance per server (``server.workflow_pulls``), built with the store the
import route uses, which this module cannot import (it lives in a route).
"""

from __future__ import annotations

import functools
import sqlite3
import threading
import time
from typing import Callable, Optional

from pixlstash.hub import workflow_origin
from pixlstash.pixl_logging import get_logger
from pixlstash.services import comfyui_userdata, workflow_inbox
from pixlstash.services.workflow_events import announce_changed_workflows
from pixlstash.tasks.base_task import TaskStatus
from pixlstash.tasks.comfyui_workflow_pull_task import (
    ComfyUIWorkflowPullTask,
    pull_entry,
)

logger = get_logger(__name__)

# A pull still holding the gate.
IN_FLIGHT = (TaskStatus.PENDING, TaskStatus.RUNNING)

# How long Run and Open wait on ComfyUI for one file, per request. Short: the
# stored version runs if ComfyUI is slow, and the poll catches up.
FRESHEN_TIMEOUT_S = 2.0


def _left(deadline: float) -> float:
    """What is left of *deadline*, never less than a tenth of a second."""
    return max(0.1, deadline - time.monotonic())


class WorkflowPulls:
    """The pull gate, and the factory every way in builds its task with."""

    def __init__(self, server, store: Callable, automatic: bool = True):
        """Bind the gate to one server.

        Args:
            server: The Server: its ``hub``, its owner (``auth.user``, kept
                current by the config route for background work) and its
                ``vault``, where an asked-for pull is submitted.
            store: ``routes/comfyui.store_pulled_workflow``, called as
                ``store(hub, name, document, record)``.
            automatic: Whether pulls nobody asked for (the poll, the one after
                a Link) happen at all: ``Server.DEFAULT_POLL_COMFYUI_WORKFLOWS``,
                off in the test suite.
        """
        self._server = server
        self._store = store
        self.automatic = automatic
        self._lock = threading.Lock()
        self._task: Optional[ComfyUIWorkflowPullTask] = None

    @property
    def last(self) -> Optional[ComfyUIWorkflowPullTask]:
        """The pull that ran or runs last, or ``None`` since start-up."""
        with self._lock:
            return self._task

    def owner_wants_pulls(self) -> Optional[str]:
        """The saved ComfyUI address when the owner's setting asks for pulls.

        ``None`` when no address is saved (the poll never guesses one), the
        "Pull workflows from ComfyUI" setting is off, or this server makes no
        automatic pulls.
        """
        if not self.automatic:
            return None
        user = getattr(getattr(self._server, "auth", None), "user", None)
        if user is None or not getattr(user, "comfyui_url", None):
            return None
        if not getattr(user, "pull_comfyui_workflows", True):
            return None
        # `routes/comfyui._comfyui_url`'s spelling, which the rows are under.
        return user.comfyui_url.rstrip("/")

    def claim(
        self,
        comfyui_url: str,
        *,
        background: bool = False,
        origin_client_id: Optional[str] = None,
    ) -> tuple[Optional[ComfyUIWorkflowPullTask], bool]:
        """A new pull of *comfyui_url*, holding the gate, or the one in flight.

        Returns:
            ``(task, True)`` for a new task the caller must submit (or
            :meth:`release` if it cannot), ``(running, False)`` when one is
            already queued or running, ``(None, False)`` without a hub.
        """
        hub = getattr(self._server, "hub", None)
        if hub is None:
            return None, False

        def announce(keys: list[str]) -> None:
            announce_changed_workflows(
                self._server, keys, "imported", origin_client_id=origin_client_id
            )

        with self._lock:
            running = self._task
            if running is not None and running.status in IN_FLIGHT:
                return running, False
            task = ComfyUIWorkflowPullTask(
                hub,
                comfyui_url,
                store=functools.partial(self._store, hub),
                lock=workflow_inbox.INBOX_LOCK,
                announce=announce,
                background=background,
            )
            # Claimed before submission, so the gate covers the queued window.
            self._task = task
            return task, True

    def release(self, task: ComfyUIWorkflowPullTask) -> None:
        """Give the gate back for a task that was never submitted."""
        with self._lock:
            if self._task is task:
                self._task = None

    def start(
        self, comfyui_url: str, origin_client_id: Optional[str] = None
    ) -> tuple[str, Optional[str]]:
        """Queue an asked-for pull: ``("started" | "already_running", task id)``.

        ``("unavailable", None)`` without a hub or a task runner; the gate is
        given back then.
        """
        task, new = self.claim(comfyui_url, origin_client_id=origin_client_id)
        if task is None:
            return "unavailable", None
        if not new:
            return "already_running", task.id
        try:
            task_id = self._server.vault.submit_task(task)
        except RuntimeError as exc:
            logger.error(
                "Could not queue a pull of ComfyUI workflows from %s: %s",
                comfyui_url,
                exc,
            )
            task_id = None
        except Exception:
            # Anything else is a bug, so it is raised; the gate is still given
            # back, or every later pull answers "already_running".
            self.release(task)
            raise
        if task_id is None:
            self.release(task)
            return "unavailable", None
        logger.info(
            "Pull of ComfyUI workflows from %s queued as task %s.", comfyui_url, task_id
        )
        return "started", task_id

    def freshen(self, comfyui_url: str, workflow_id: str) -> Optional[int]:
        """Pull *workflow_id*'s ComfyUI file now if it changed since it was read.

        Run and Open call this before resolving a manual workflow: one listing
        of the file's folder, and only a file whose ``modified`` differs from
        the one its origin row holds is read and stored, as a new version,
        through the pull's own store. **Never raises**: ComfyUI not answering,
        the file gone or any failure leaves the stored version to run, logged
        at info; the poll catches up.

        **:data:`FRESHEN_TIMEOUT_S` bounds the whole check**, both requests and
        the store, not each phase: the check runs on a worker and is waited for
        that long. ``requests`` timeouts are per socket operation and DNS has
        none, so nothing short of this bounds a stalling ComfyUI. A check that
        overruns finishes on its thread (each request bounded by what is left
        of the deadline) and a version it stores late is used by the next Run.

        Returns:
            The version it stored, or ``None`` when it stored none.
        """
        hub = getattr(self._server, "hub", None)
        if hub is None:
            return None
        try:
            link = workflow_origin.live_file(hub, comfyui_url, workflow_id)
        except sqlite3.Error as exc:
            logger.info(
                "Could not read workflow %s's ComfyUI link, so its stored "
                "version is used: %s",
                workflow_id,
                exc,
            )
            return None
        if link is None:
            # Not pulled, gone, or pulled from another address: nothing to ask.
            return None
        deadline = time.monotonic() + FRESHEN_TIMEOUT_S
        answer: list[Optional[int]] = []
        # A thread per check, so the caller can stop waiting at the deadline;
        # daemon, and ended by its own requests' deadline soon after.
        worker = threading.Thread(
            target=lambda: answer.append(
                self._freshen(hub, comfyui_url, workflow_id, link, deadline)
            ),
            name="comfyui-check",
            daemon=True,
        )
        worker.start()
        worker.join(FRESHEN_TIMEOUT_S)
        if worker.is_alive():
            logger.info(
                "ComfyUI at %s did not answer for workflow %s within %.0f s, so "
                "its stored version is used.",
                comfyui_url,
                workflow_id,
                FRESHEN_TIMEOUT_S,
            )
            return None
        return answer[0] if answer else None

    def _freshen(
        self, hub, comfyui_url: str, workflow_id: str, link: dict, deadline: float
    ) -> Optional[int]:
        """:meth:`freshen`'s work, each request given what is left of *deadline*."""
        try:
            entry = comfyui_userdata.saved_workflow_info(
                comfyui_url, link["remote_path"], _left(deadline)
            )
            if entry is None or entry.modified_ms is None:
                # Gone, or a ComfyUI without times: the poll decides.
                return None
            if entry.modified_ms == link["remote_modified"]:
                return None
            if time.monotonic() >= deadline:
                logger.info(
                    "Workflow %s changed in ComfyUI, but the check ran out of "
                    "time; its stored version is used and the poll takes it.",
                    workflow_id,
                )
                return None
            pulled = pull_entry(
                hub,
                comfyui_url,
                entry,
                functools.partial(self._store, hub),
                workflow_inbox.INBOX_LOCK,
                timeout_s=_left(deadline),
            )
        except Exception as exc:
            # Broad on purpose: whatever went wrong, the stored version runs.
            logger.info(
                "Could not check workflow %s against ComfyUI at %s, so its "
                "stored version is used: %s: %s",
                workflow_id,
                comfyui_url,
                type(exc).__name__,
                exc,
            )
            return None
        if not isinstance(pulled, tuple):
            return None
        _document, outcome = pulled
        changed = outcome.get("workflow_id")
        if changed:
            announce_changed_workflows(self._server, [changed], "changed")
        if outcome.get("versioned") and changed == workflow_id:
            logger.info(
                "Workflow %s changed in ComfyUI; version %s is used.",
                workflow_id,
                outcome.get("version"),
            )
            return outcome.get("version")
        return None
