"""Routes for the tagger plugin system.

Provides:
    GET  /taggers                          - list all registered plugins + current settings
    GET  /taggers/plugin-diagnostics       - scanned folders + load failures (local owner)
    POST /taggers/{name}/download          - kick off an artifact download for a plugin
    DELETE /taggers/{name}/artifacts/{id}  - remove a downloaded artifact
"""

from __future__ import annotations

import json
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from pixlstash.pixl_logging import get_logger
from pixlstash.hub.cli_hint import cli_hint
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


class TaggerListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    plugins: list[TaggerPluginResponse] = []
    settings: dict = {}


class TaggerPluginDiagnosticsResponse(BaseModel):
    """Everything about plugin *installation* - a §16.3 host-path disclosure.

    Deliberately its own route rather than fields on ``GET /taggers``, which
    used to be ANY_TOKEN and handed a share-link holder the owner's home
    directory. (That route is OWNER_ONLY now as well, for the settings it
    carries; this one is stricter still because a host path is the §16.3
    locality class.) Both halves disclose it. ``plugin_dirs`` says so plainly,
    and a ``load_errors`` message is built from an exception raised by
    third-party code at import - an ``OSError`` out of a plugin's module body
    carries whatever absolute path it was reaching for. Sanitising that text is
    guesswork; not serving it to a share token is not.
    """

    model_config = ConfigDict(extra="allow")

    plugin_dirs: dict = {}
    load_errors: list[dict] = []
    cli_hint: Optional[str] = None
    cli_available_hint: Optional[str] = None
    cli_search_hint: Optional[str] = None
    cli_list_hint: Optional[str] = None


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
                  "is_loaded": false
                },
                ...
              ],
              "settings": { ... }
            }

        Neither the scanned folders nor the plugins that failed to load are
        here: both name paths on the server's disk, and this route's job is the
        plugin list, not installation. See ``GET /taggers/plugin-diagnostics``,
        which is local-owner-only.

        OWNER_ONLY: ``settings`` is the caller's own ``tagger_settings``, and a
        plugin is free to declare a free-text parameter the owner has typed a
        path into.
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
                }
            )

        return {
            "plugins": plugins_out,
            "settings": _current_settings(request),
        }

    @router.get(
        "/taggers/plugin-diagnostics",
        summary="Plugin folders and load failures (installation diagnostics)",
        response_model=TaggerPluginDiagnosticsResponse,
    )
    def plugin_diagnostics(request: Request):
        """Return the scanned folders and every plugin that failed to import.

        LOCAL_OWNER_ONLY: both halves name paths on the server's disk, which is
        the §16.3 disclosure class. Nothing is lost by gating them - acting on
        either means editing a file in that folder and restarting.
        """
        server.auth.ensure_secure_when_required(request)
        mgr = get_tagger_plugin_manager()
        return {
            "plugin_dirs": mgr.plugin_dirs(),
            "load_errors": mgr.list_errors(),
            "cli_hint": cli_hint("plugins install <name-or-path>"),
            "cli_available_hint": cli_hint("plugins available"),
            "cli_search_hint": cli_hint("plugins available <search-term>"),
            "cli_list_hint": cli_hint("plugins list"),
        }

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
