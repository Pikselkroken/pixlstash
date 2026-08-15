"""Routes for the tagger plugin system.

Provides:
    GET  /taggers                          — list all registered plugins + current settings
    GET  /taggers/plugin-dirs              — the scanned host folders (local owner only)
    POST /taggers/{name}/download          — kick off an artifact download for a plugin
    DELETE /taggers/{name}/artifacts/{id}  — remove a downloaded artifact
"""

from __future__ import annotations

import json
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from pixlstash.pixl_logging import get_logger
from pixlstash.tagger_plugins.registry import get_tagger_plugin_manager

logger = get_logger(__name__)


class TaggerPluginResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    supports_tags: bool = False
    supports_descriptions: bool = False
    requires_download: bool = False
    default_enabled: bool = False
    parameter_schema: list[dict] = []
    downloaded_artifacts: list[dict] = []
    is_loaded: bool = False
    load_error: Optional[str] = None


class TaggerListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    plugins: list[TaggerPluginResponse] = []
    settings: dict = {}


class TaggerPluginDirsResponse(BaseModel):
    """Host folders scanned for user plugins — a §16.3 host-path disclosure.

    Deliberately its own route rather than a field on ``GET /taggers``: that
    one is ANY_TOKEN, so a share-link holder would otherwise be handed the
    owner's home directory. This one is LOCAL_OWNER_ONLY.
    """

    model_config = ConfigDict(extra="allow")

    plugin_dirs: dict = {}


class TaggerDownloadResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str


class TaggerArtifactDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str


def create_router(server) -> APIRouter:
    """Return the taggers router bound to *server*."""
    router = APIRouter()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _current_settings(request: Request) -> dict:
        """Return the current user's parsed tagger_settings dict."""
        server.auth.ensure_secure_when_required(request)
        user = server.auth.get_user_for_request(request)
        raw = user.tagger_settings or "{}"
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            parsed = {}
        mgr = get_tagger_plugin_manager()
        return mgr.fill_defaults(parsed)

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @router.get(
        "/taggers",
        summary="List tagger plugins and current settings",
        response_model=TaggerListResponse,
    )
    def list_taggers(request: Request):
        """Return every registered tagger/captioner plugin together with
        the current user's ``tagger_settings``.

        Response shape::

            {
              "plugins": [
                {
                  "name": "pixlstash_tagger",
                  "display_name": "PixlStash Tagger",
                  "description": "...",
                  "supports_tags": true,
                  "supports_descriptions": false,
                  "requires_download": true,
                  "default_enabled": true,
                  "parameter_schema": [...],
                  "downloaded_artifacts": [],
                  "is_loaded": false,
                  "load_error": null
                },
                ...
              ],
              "settings": { ... }
            }

        The scanned plugin folders are **not** here — they are host paths, and
        this route is reachable by any token. See ``GET /taggers/plugin-dirs``.
        """
        mgr = get_tagger_plugin_manager()

        plugins_out = []
        for name in mgr.plugin_names():
            plugin = mgr.get_plugin(name)
            plugins_out.append(
                {
                    "name": plugin.name,
                    "display_name": plugin.display_name,
                    "description": plugin.description,
                    "supports_tags": bool(plugin.supports_tags),
                    "supports_descriptions": bool(plugin.supports_descriptions),
                    "requires_download": bool(plugin.requires_download),
                    "default_enabled": bool(plugin.default_enabled),
                    "parameter_schema": plugin.parameter_schema(),
                    "downloaded_artifacts": plugin.list_downloaded_artifacts(),
                    "is_loaded": bool(plugin.is_loaded()),
                    "load_error": None,
                }
            )

        # Surface plugins that failed to import.
        for err in mgr.list_errors():
            plugins_out.append(
                {
                    "name": err["name"],
                    "display_name": err["name"],
                    "description": "",
                    "supports_tags": False,
                    "supports_descriptions": False,
                    "requires_download": False,
                    "default_enabled": False,
                    "parameter_schema": [],
                    "downloaded_artifacts": [],
                    "is_loaded": False,
                    "load_error": err["message"],
                }
            )

        return {
            "plugins": plugins_out,
            "settings": _current_settings(request),
        }

    @router.get(
        "/taggers/plugin-dirs",
        summary="Host folders scanned for user tagger plugins",
        response_model=TaggerPluginDirsResponse,
    )
    def list_plugin_dirs(request: Request):
        """Return ``{"plugin_dirs": {"user": "/host/path"}}``.

        LOCAL_OWNER_ONLY: it names a folder on the server's disk, which is the
        §16.3 host-path class. Nothing else about a plugin is gated by it —
        installing one means writing to that folder, which a remote caller
        cannot do anyway.
        """
        server.auth.ensure_secure_when_required(request)
        return {"plugin_dirs": get_tagger_plugin_manager().plugin_dirs()}

    @router.post(
        "/taggers/{name}/download",
        summary="Start artifact download for a tagger plugin",
        status_code=202,
        response_model=TaggerDownloadResponse,
    )
    def download_plugin(name: str, request: Request):
        """Kick off a background download for the named plugin.

        Returns immediately with ``{"status": "started"}`` or
        ``{"status": "not_required"}`` when no download is needed.
        Download progress is logged to the server console.

        Raises 404 if the plugin is not registered.
        """
        server.auth.ensure_secure_when_required(request)
        mgr = get_tagger_plugin_manager()
        plugin = mgr.get_plugin(name)
        if plugin is None:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")

        if not plugin.needs_download():
            return {"status": "not_required"}

        def _run():
            try:
                plugin.download()
            except Exception as exc:
                logger.error("Download failed for plugin '%s': %s", name, exc)

        thread = threading.Thread(target=_run, daemon=True, name=f"download-{name}")
        thread.start()
        return {"status": "started"}

    @router.delete(
        "/taggers/{name}/artifacts/{artifact_id}",
        summary="Delete a downloaded artifact for a tagger plugin",
        response_model=TaggerArtifactDeleteResponse,
    )
    def delete_artifact(name: str, artifact_id: str, request: Request):
        """Remove a downloaded artifact and unload the plugin if currently loaded.

        Raises 404 if the plugin or artifact is not found.
        """
        server.auth.ensure_secure_when_required(request)
        mgr = get_tagger_plugin_manager()
        plugin = mgr.get_plugin(name)
        if plugin is None:
            raise HTTPException(status_code=404, detail=f"Plugin '{name}' not found.")

        artifacts = plugin.list_downloaded_artifacts()
        if not any(a.get("name") == artifact_id for a in artifacts):
            raise HTTPException(
                status_code=404,
                detail=f"Artifact '{artifact_id}' not found for plugin '{name}'.",
            )

        plugin.delete_artifact(artifact_id)
        return {"status": "deleted"}

    return router
