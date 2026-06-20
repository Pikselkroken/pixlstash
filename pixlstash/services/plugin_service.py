"""Service layer for image plugin operations.

Extracted from pixlstash/routes/pictures.py to keep route handlers thin.
Provides the async orchestration for running image plugins with progress tracking.
"""

import asyncio
import uuid
from typing import TYPE_CHECKING

from pixlstash.event_types import EventType
from pixlstash.image_plugins.registry import get_image_plugin_manager
from pixlstash.image_plugins.service import apply_plugin_to_pictures
from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.image_plugins.base import ImagePlugin
    from pixlstash.vault import Vault

logger = get_logger(__name__)


def list_plugins(vault: "Vault") -> dict:
    """Return available image plugins, errors, and plugin directories.

    Args:
        vault: Application vault (unused currently; reserved for future
            vault-scoped plugin discovery).

    Returns:
        Dict with keys: ``plugins``, ``plugin_errors``, ``plugin_dirs``.
    """
    manager = get_image_plugin_manager()
    manager.reload()
    return {
        "plugins": manager.list_plugins(),
        "plugin_errors": manager.list_errors(),
        "plugin_dirs": {
            "built_in": manager.built_in_dir,
            "user": manager.user_dir,
        },
    }


async def run_plugin_on_pictures(
    server,
    name: str,
    picture_ids: list[int],
    parameters: dict,
    captions: list[str] | None = None,
    origin_client_id: str | None = None,
    stack: bool = True,
) -> dict:
    """Run a named image plugin on a list of pictures with progress tracking.

    Emits PLUGIN_PROGRESS WebSocket events throughout the run.  Returns the
    result dict from the plugin, enriched with ``status: "success"``.

    Args:
        server: Application server (provides vault for event notification).
        name: Plugin name to run.
        picture_ids: List of picture IDs to process.
        parameters: Plugin-specific parameter dict.
        captions: Optional per-picture captions, must match ``picture_ids`` length.
        origin_client_id: Opaque ``X-Client-Id`` of the originating tab, captured
            at request entry. Plugin output is a UI-initiated import, so it is
            echoed on the PICTURE_IMPORTED event so the originating tab can do a
            targeted grid insert instead of a full reload. Echo-matching only.
        stack: When ``True`` (default), plugin outputs are placed in the source
            picture's stack. When ``False``, the physical stacking is skipped but
            all source associations (set/project/face) are still copied.

    Raises:
        ValueError: If the plugin name is not found.
        RuntimeError: If plugin execution fails with an unexpected error.
    """
    manager = get_image_plugin_manager()
    manager.reload()
    plugin: "ImagePlugin | None" = manager.get_plugin(name)
    if plugin is None:
        raise ValueError(f"Plugin not found: {name!r}")

    vault: "Vault" = server.vault
    plugin_run_id = str(uuid.uuid4())

    def _emit_progress(progress_payload: dict) -> None:
        if not isinstance(progress_payload, dict):
            return
        vault.notify(
            EventType.PLUGIN_PROGRESS,
            {
                "run_id": plugin_run_id,
                "plugin": str(progress_payload.get("plugin") or name),
                "status": "running",
                **progress_payload,
            },
        )

    def _emit_error(error_payload: dict) -> None:
        if not isinstance(error_payload, dict):
            return
        vault.notify(
            EventType.PLUGIN_PROGRESS,
            {
                "run_id": plugin_run_id,
                "plugin": str(error_payload.get("plugin") or name),
                "status": "error",
                "message": str(error_payload.get("message") or "Plugin error"),
                "error": error_payload,
            },
        )

    vault.notify(
        EventType.PLUGIN_PROGRESS,
        {
            "run_id": plugin_run_id,
            "plugin": name,
            "status": "started",
            "current": 0,
            "total": len(picture_ids),
            "progress": 0.0,
            "message": f"Starting plugin: {name}",
        },
    )

    try:
        result = await asyncio.to_thread(
            apply_plugin_to_pictures,
            server,
            plugin,
            picture_ids,
            parameters,
            captions,
            progress_reporter=_emit_progress,
            error_reporter=_emit_error,
            stack=stack,
        )
    except ValueError as exc:
        vault.notify(
            EventType.PLUGIN_PROGRESS,
            {
                "run_id": plugin_run_id,
                "plugin": name,
                "status": "failed",
                "message": str(exc),
            },
        )
        raise
    except Exception as exc:
        logger.warning("Plugin run failed for %r: %s", name, exc)
        vault.notify(
            EventType.PLUGIN_PROGRESS,
            {
                "run_id": plugin_run_id,
                "plugin": name,
                "status": "failed",
                "message": str(exc),
            },
        )
        raise RuntimeError(str(exc)) from exc

    vault.notify(
        EventType.PLUGIN_PROGRESS,
        {
            "run_id": plugin_run_id,
            "plugin": name,
            "status": "completed",
            "current": len(picture_ids),
            "total": len(picture_ids),
            "progress": 100.0,
            "message": f"Completed plugin: {name}",
        },
    )

    created_ids = result.get("created_picture_ids") or []
    output_ids = result.get("output_picture_ids") or []
    if created_ids:
        vault.notify(
            EventType.PICTURE_IMPORTED,
            {
                "ids": created_ids,
                "source": "ui",
                "origin_client_id": origin_client_id,
                "change_kind": "added",
            },
        )
    if output_ids:
        vault.notify(EventType.CHANGED_PICTURES, output_ids)

    return {"status": "success", **result}
