import gc
import uvicorn
import os
import json
import re
import socket
import asyncio
import threading
from importlib.metadata import PackageNotFoundError, version as package_version
from platformdirs import user_config_dir


from contextlib import asynccontextmanager
from PIL import Image
from fastapi import (
    FastAPI,
    Request,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pillow_heif import register_heif_opener

from sqlmodel import select

from pixlstash.db_models import (
    Picture,
    User,
)

from pixlstash.event_types import EventType
from pixlstash.auth import AuthService, LoginRequest, is_auth_excluded_path
from pixlstash.pixl_logging import get_logger, uvicorn_log_config
from pixlstash.startup_checks import StartupChecks
from pixlstash.vault import Vault
from pixlstash.routes.config import create_router as create_config_router
from pixlstash.routes.characters import create_router as create_characters_router
from pixlstash.routes.picture_sets import create_router as create_picture_sets_router
from pixlstash.routes.projects import create_router as create_projects_router
from pixlstash.routes.tags import create_router as create_tags_router
from pixlstash.routes.stacks import create_router as create_stacks_router
from pixlstash.routes.pictures import (
    create_router as create_pictures_router,
    clear_stats_cache,
)
from pixlstash.routes.comfyui import create_router as create_comfyui_router
from pixlstash.routes.tag_predictions import (
    create_router as create_tag_predictions_router,
)
from pixlstash.routes.reference_folders import (
    create_router as create_reference_folders_router,
)
from pixlstash.routes.import_folders import (
    create_router as create_import_folders_router,
)
from pixlstash.routes.filesystem import create_router as create_filesystem_router
from pixlstash.routes.guest_scores import create_router as create_guest_scores_router
from pixlstash.routes.share import create_router as create_share_router
from pixlstash.routes.taggers import create_router as create_taggers_router
from pixlstash.routes.snapshots import create_router as create_snapshots_router
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.path_mapper import PathMapper
from pixlstash.utils.rate_limiter import RateLimitMiddleware


# Logging will be set up after config is loaded
logger = get_logger(__name__)

# Snapshot / restore / undo vault events mapped to the WebSocket ``type`` string
# the frontend listens for.  Keeping this as a single map keeps the delivery
# whitelist (_should_send_ws_update) and the payload builder (_broadcast_ws_event)
# in sync.
_WS_SNAPSHOT_EVENT_TYPES = {
    EventType.SNAPSHOT_CREATED: "snapshot_created",
    EventType.SNAPSHOT_DELETED: "snapshot_deleted",
    EventType.RESTORE_STARTED: "restore_started",
    EventType.RESTORE_COMPLETED: "restore_completed",
    EventType.UNDO_APPLIED: "undo_applied",
}


def _get_lan_ip() -> str | None:
    """Return the machine's primary LAN IP by probing an outbound UDP route.

    Does not send any data. Returns None if the IP cannot be determined.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


API_OPENAPI_TAGS = [
    {
        "name": "pictures",
        "description": "Picture listing, metadata, thumbnails, import/export and media operations.",
    },
    {
        "name": "config",
        "description": "User configuration, auth helpers, worker progress and server config utilities.",
    },
    {
        "name": "characters",
        "description": "Character CRUD, summaries, reference pictures and face assignment endpoints.",
    },
    {
        "name": "picture_sets",
        "description": "Picture set CRUD and picture membership management.",
    },
    {
        "name": "tags",
        "description": "Tag management for pictures, faces",
    },
    {
        "name": "stacks",
        "description": "Stack creation, ordering and membership operations.",
    },
    {
        "name": "comfyui",
        "description": "ComfyUI workflow management and image-to-image execution.",
    },
    {
        "name": "projects",
        "description": "Project management, including character/set scoping and file attachments.",
    },
    {
        "name": "taggers",
        "description": "Tagger plugin registry, artifact downloads and deletion.",
    },
    {
        "name": "snapshots",
        "description": "Library snapshots: create, list, restore and manage point-in-time backups.",
    },
    {
        "name": "tag_predictions",
        "description": "Automatic tag prediction and suggestion endpoints.",
    },
    {
        "name": "guest_scores",
        "description": "Guest scoring sessions and submitted picture scores.",
    },
    {
        "name": "folders",
        "description": "Reference and import folders: list, add, update, remove and open on disk.",
    },
    {
        "name": "auth",
        "description": "Login, logout and session checks.",
    },
    {
        "name": "server",
        "description": "Server status and control: version, network info and restart.",
    },
]

API_V1_PREFIX = "/api/v1"

# Rendered as Markdown at the top of the API reference (Scalar / Swagger). Image
# URLs are page-relative to ``scalar-assets/`` — served from the bundled package
# on a live server and copied next to each published page by the docs generator.
API_DESCRIPTION = """\
<div align="center">
  <a href="https://pixlstash.dev" target="_blank" rel="noopener">
    <img src="scalar-assets/logo.png" alt="PixlStash" width="150" />
  </a>
</div>

# Simplify your image workflow

**PixlStash is a self-hosted, open-source image library for creators.** It imports your
pictures and videos, auto-tags and captions them with local AI models, recognises
characters and faces, scores image quality, runs natural-language semantic search and
drives ComfyUI workflows — all on your own hardware, with no cloud and no lock-in.

**Integrate with scripts, pipelines and external tools** — fetch images, metadata, tags and
more. This REST API exposes everything the app can do — **pictures, tags, stacks, sets,
characters, projects, taggers and ComfyUI integration** — so you can script imports, build
integrations and automate your pipeline.

### → Learn more and download at **[pixlstash.dev](https://pixlstash.dev)**

---

## Quick start

Create an API token (steps below), then fetch your first 50 pictures — replace
`your-pixlstash-host` with your server's address:

```bash
curl "https://your-pixlstash-host/api/v1/pictures?limit=50" \\
  -H "Authorization: Bearer YOUR_TOKEN"
```

## Authentication

Every request authenticates with a personal **API token**, sent as a Bearer header:

```http
Authorization: Bearer YOUR_TOKEN
```

Tokens have one of two access types:

| Access type | Can do | Notes |
| --- | --- | --- |
| **Full access** | Read **and** write — everything your account can do | Never put a full-access token in a URL |
| **Read-only** | `GET` requests only | May also be passed as a `?token=…` query parameter (handy for share links) |

## Creating a token

**1. Open Settings** — click the gear icon in the top toolbar.

![Open Settings from the top toolbar](scalar-assets/WhereIsUserSettings.jpg)

**2. Open the *Account Settings* tab.**

![The Account Settings tab](scalar-assets/ScreenshotsUserSettings.jpg)

**3. Create the token** — in the **API Tokens** section, type a description, choose an
access type (*Full access* or *Read-only*), optionally tick *Apply watermark*, then click
**Create Token**.

![The API Tokens section in Account Settings](scalar-assets/ScreenshotTokens.jpg)

**4. Copy it now** — the token is shown **only once**. Copy it and store it somewhere safe;
you won't be able to see it again.

![Copy the newly created token](scalar-assets/ScreenshotToken.jpg)

## Using the token

The API is served under `/api/v1`. Send your token in the `Authorization` header on every
request, replacing `your-pixlstash-host` with the address of your PixlStash server. To
confirm a token is valid:

```bash
curl https://your-pixlstash-host/api/v1/check-session \\
  -H "Authorization: Bearer YOUR_TOKEN"
```

A **read-only** token may instead be passed in the query string for quick read requests
(never do this with a full-access token):

```bash
curl "https://your-pixlstash-host/api/v1/pictures?limit=50&token=YOUR_READ_TOKEN"
```

Browse the endpoints below for full request and response details.
"""

# Scalar theme overrides matching the PixlStash dark UI (see frontend/src/main.js).
# Custom properties carry !important so they win over Scalar's own ``.dark-mode``
# rules regardless of stylesheet load order.
_SCALAR_THEME_CSS = """\
    <style>
      .dark-mode {
        --scalar-font: 'Space Grotesk', ui-sans-serif, system-ui, sans-serif !important;
        --scalar-font-code: 'IBM Plex Mono', ui-monospace, monospace !important;
        --scalar-background-1: #2a2f36 !important;
        --scalar-background-2: #2b3138 !important;
        --scalar-background-3: #313337 !important;
        --scalar-background-accent: rgba(142, 166, 4, 0.16) !important;
        --scalar-color-1: #f2e5da !important;
        --scalar-color-2: rgba(242, 229, 218, 0.72) !important;
        --scalar-color-3: rgba(242, 229, 218, 0.5) !important;
        --scalar-color-accent: #8ea604 !important;
        --scalar-border-color: #3a4047 !important;
      }
      .dark-mode .sidebar {
        --scalar-sidebar-background-1: #1f2328 !important;
        --scalar-sidebar-color-1: #f2e5da !important;
        --scalar-sidebar-color-2: rgba(242, 229, 218, 0.7) !important;
        --scalar-sidebar-border-color: #3a4047 !important;
        --scalar-sidebar-item-hover-background: rgba(255, 255, 255, 0.06) !important;
        --scalar-sidebar-item-hover-color: #f2e5da !important;
        --scalar-sidebar-item-active-background: rgba(142, 166, 4, 0.16) !important;
        --scalar-sidebar-color-active: #8ea604 !important;
        --scalar-sidebar-search-background: #2b3138 !important;
        --scalar-sidebar-search-border-color: #3a4047 !important;
        --scalar-sidebar-search--color: rgba(242, 229, 218, 0.6) !important;
      }
    </style>"""


def render_scalar_html(spec_url: str) -> str:
    """Return the Scalar API-reference page wired to *spec_url*, forced to the
    PixlStash dark theme.

    Shared by the live ``/scalar`` route and the static docs generator so both
    stay in sync. *spec_url* is a trusted internal literal (``/openapi.json`` for
    the live server, ``openapi.json`` for the published per-version page).
    """
    return f"""<!doctype html>
<html lang="en">
  <head>
    <title>PixlStash API Reference</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap"
      rel="stylesheet"
    />
{_SCALAR_THEME_CSS}
  </head>
  <body>
    <script
      id="api-reference"
      data-url="{spec_url}"
      data-configuration='{{"forceDarkModeState":"dark","hideDarkModeToggle":true,"hideModels":true,"persistAuth":true,"authentication":{{"preferredSecurityScheme":"bearerAuth"}}}}'
    ></script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
  </body>
</html>
"""


def _example_for_schema(schema, schemas, seen=()):
    """Best-effort representative example value for an OpenAPI schema.

    Most response models are Pydantic ``Optional[...]`` fields, which serialize
    as ``anyOf: [T, null]`` with no example. Scalar then renders the whole
    response example as ``null`` — useless in the docs. We synthesize a
    shape-correct example (picking the non-null branch, recursing through
    ``$ref``/objects/arrays) so every endpoint shows its response structure.

    Returns ``None`` when nothing meaningful can be produced (e.g. a circular
    ref or an empty schema); callers skip injecting an example in that case.
    """
    if not isinstance(schema, dict):
        return None

    ref = schema.get("$ref")
    if ref:
        name = ref.split("/")[-1]
        if name in seen:  # circular reference — stop descending
            return None
        return _example_for_schema(schemas.get(name, {}), schemas, seen + (name,))

    if "example" in schema:
        return schema["example"]
    examples = schema.get("examples")
    if isinstance(examples, list) and examples:
        return examples[0]
    if "default" in schema:
        return schema["default"]
    enum = schema.get("enum")
    if enum:
        return enum[0]

    for combinator in ("anyOf", "oneOf"):
        for sub in schema.get(combinator, []):
            if isinstance(sub, dict) and sub.get("type") == "null":
                continue
            value = _example_for_schema(sub, schemas, seen)
            if value is not None:
                return value

    if "allOf" in schema:
        merged = {}
        for sub in schema["allOf"]:
            value = _example_for_schema(sub, schemas, seen)
            if isinstance(value, dict):
                merged.update(value)
        return merged or None

    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        return {
            key: _example_for_schema(prop, schemas, seen)
            for key, prop in schema.get("properties", {}).items()
        }
    if schema_type == "array":
        item = _example_for_schema(schema.get("items", {}), schemas, seen)
        return [item] if item is not None else []
    if schema_type == "string":
        return {
            "date-time": "2026-01-01T00:00:00Z",
            "date": "2026-01-01",
            "uuid": "00000000-0000-0000-0000-000000000000",
            "email": "user@example.com",
            "binary": "",
        }.get(schema.get("format"), "string")
    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0.0
    if schema_type == "boolean":
        return True
    return None


def _inject_response_examples(operation, schemas):
    """Attach a synthesized ``example`` to an operation's 2xx JSON responses.

    Media types that already carry an ``example``/``examples`` are left alone so
    any hand-authored example always wins.
    """
    for code, response in operation.get("responses", {}).items():
        if not str(code).startswith("2"):
            continue
        for media in response.get("content", {}).values():
            if "example" in media or "examples" in media:
                continue
            media_schema = media.get("schema")
            if not media_schema:
                continue
            try:
                example = _example_for_schema(media_schema, schemas)
            except Exception:
                example = None
            if example is not None:
                media["example"] = example


class Server:
    """
    Main server class for the PixlStash FastAPI application.

    Attributes:
        server_config_path(str): Server-side-only configuration file.
        DEFAULT_MAX_VRAM_GB: Class-level VRAM budget override (GB). When set
            (e.g. by the pytest ``--max-vram-gb`` option) it takes precedence
            over the persisted user config value for all Server instances.
            ``None`` means use the user config.
        DEFAULT_FORCE_CPU: Class-level CPU-inference override. When ``True``,
            forces CPU inference after startup checks complete, preventing the
            startup check from clobbering a ``--force-cpu`` flag set by the
            test framework. ``None`` means startup checks decide.
        DEFAULT_PORT: Class-level port override. When set (e.g. by the pytest
            conftest to a free OS-assigned port), it replaces the port from the
            persisted config for all Server instances. ``None`` means use the
            config value.
        DEFAULT_CLEANUP_MISSING_PICTURES: Class-level startup cleanup toggle.
            When ``True``, startup removes picture rows that point to missing
            source files before thumbnail generation. ``False`` means disabled.
    """

    DEFAULT_MAX_VRAM_GB: float | None = None
    DEFAULT_FORCE_CPU: bool | None = None
    DEFAULT_FAST_CAPTIONS: bool = False
    DEFAULT_PORT: int | None = None
    DEFAULT_CLEANUP_MISSING_PICTURES: bool = False

    @staticmethod
    def running_in_docker() -> bool:
        """Return True when the server is running inside a Docker container."""
        return os.environ.get("PIXLSTASH_IN_DOCKER", "") == "1"

    def __init__(
        self,
        server_config_path,
        path_map: dict[str, str] | None = None,
    ):
        """
        Initialize the Server instance.

        Args:
            server_config_path (str): Path to the server-only config file.
            path_map: Optional dict mapping host path prefixes to their
                container equivalents. Set by the ``--path-map`` CLI arg.
        """
        # Ensure garbage collection before starting server to free up memory.
        # This is mainly to ensure repeated runs within the testing framework do not accumulate memory usage.
        gc.collect()

        self._server_config_path = server_config_path

        self.path_mapper = PathMapper(path_map)

        self._server_config = self.init_server_config(server_config_path)
        self._startup_check_report = StartupChecks(
            server_config=self._server_config,
            server_config_path=self._server_config_path,
            logger=logger,
        ).run()
        with open(server_config_path, "w") as f:
            json.dump(self._server_config, f, indent=2)

        # SSL config
        if self._server_config.get("require_ssl", False):
            self._ensure_ssl_certificates()

        logger.debug(
            "Creating Vault instance with image root: "
            + str(self._server_config["image_root"])
        )

        register_heif_opener()

        _startup_forced_cpu = self._startup_check_report.get("forced_cpu", False)
        _force_cpu = (
            Server.DEFAULT_FORCE_CPU
            if Server.DEFAULT_FORCE_CPU is not None
            else _startup_forced_cpu
        )
        self.vault = Vault(
            image_root=self._server_config["image_root"],
            description=User().description,
            server_config_path=self._server_config_path,
            path_mapper=self.path_mapper,
            disable_background_workers=self._server_config.get(
                "disable_background_workers", False
            ),
            force_cpu=bool(_force_cpu),
            fast_captions=Server.DEFAULT_FAST_CAPTIONS,
            daily_snapshots_enabled=self._server_config.get("daily_snapshots", True),
        )

        self._ws_clients = []
        self._ws_clients_lock = threading.Lock()
        self._ws_loop = None
        self.vault.add_event_listener(self.handle_vault_event)

        self.auth = AuthService(
            self.vault.db,
            self._server_config,
            self._server_config_path,
            logger,
        )
        self._user = self.auth.ensure_user()
        if self._user and self._user.description is not None:
            self.vault.set_description(self._user.description)
        self.vault.set_keep_models_in_memory(
            getattr(self._user, "keep_models_in_memory", True)
        )
        effective_vram_gb = (
            Server.DEFAULT_MAX_VRAM_GB
            if Server.DEFAULT_MAX_VRAM_GB is not None
            else getattr(self._user, "max_vram_gb", None)
        )
        self.vault.set_max_vram_usage_gb(effective_vram_gb)
        # Initialise tagger_settings from the stored JSON (fills defaults for any
        # missing plugin entries so the engine always has a complete config).
        if self._user is not None:
            import json as _json
            from pixlstash.tagger_plugins.registry import get_tagger_plugin_manager

            raw_settings = getattr(self._user, "tagger_settings", None)
            if raw_settings:
                try:
                    parsed = _json.loads(raw_settings)
                except (ValueError, TypeError):
                    parsed = {}
            else:
                parsed = {}
            filled = get_tagger_plugin_manager().fill_defaults(parsed)
            self.vault.set_tagger_settings(filled)
        self.vault.start()

        self.api = FastAPI(
            title="PixlStash API",
            version=self._get_version(),
            description=API_DESCRIPTION,
            openapi_tags=API_OPENAPI_TAGS,
            lifespan=self.lifespan,
            redoc_url=None,
        )
        # CORS: always allow localhost/127.0.0.1 on any port plus the machine's
        # own LAN IP (any port) so the Vite dev server works over LAN without
        # any extra configuration. Additional origins can be added via cors_origins.
        self.allow_origins = list(self._server_config.get("cors_origins") or [])
        _cors_hosts = ["localhost", r"127\.0\.0\.1"]
        _lan_ip = _get_lan_ip()
        if _lan_ip and _lan_ip not in ("127.0.0.1", "localhost"):
            _cors_hosts.append(re.escape(_lan_ip))
        self.allow_origin_regex = r"^https?\://(" + "|".join(_cors_hosts) + r")(:\d+)?$"
        self.api.add_middleware(
            CORSMiddleware,
            allow_origins=self.allow_origins,
            allow_origin_regex=self.allow_origin_regex,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self._add_cors_exception_handler()
        self._setup_routes()
        self._install_custom_openapi()

        # Temporary storage for export tasks
        self.export_tasks = {}

        # Temporary storage for import tasks
        self.import_tasks = {}
        self._shutdown_on_lifespan = False

    def __enter__(self):
        # Allow use as a context manager for robust cleanup
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, "vault"):
            logger.info("Closing the vault and cleaning up resources")
            self.vault.close()
        gc.collect()

    def handle_vault_event(self, event_type: EventType, data=None):
        if event_type in (
            EventType.CHANGED_TAGS,
            EventType.CLEARED_TAGS,
            EventType.CHANGED_PICTURES,
            EventType.PICTURE_IMPORTED,
            EventType.QUALITY_UPDATED,
        ):
            clear_stats_cache()
        if not self._ws_loop:
            return
        coro = self._broadcast_ws_event(event_type, data)
        try:
            logger.debug("Got the following event from vault: %s", event_type)
            asyncio.run_coroutine_threadsafe(coro, self._ws_loop)
        except Exception as exc:
            logger.warning("Failed to dispatch websocket event: %s", exc)
            coro.close()  # prevent 'coroutine never awaited' ResourceWarning

    def _should_send_ws_update(self, event_type: EventType, filters: dict) -> bool:
        return (
            event_type
            in (
                EventType.CHANGED_PICTURES,
                EventType.PICTURE_IMPORTED,
                EventType.PLUGIN_PROGRESS,
                EventType.CHANGED_TAGS,
                EventType.CLEARED_TAGS,
                EventType.CHANGED_CHARACTERS,
                EventType.CHANGED_FACES,
                EventType.CHANGED_DESCRIPTIONS,
            )
            or event_type in _WS_SNAPSHOT_EVENT_TYPES
        )

    async def _broadcast_ws_event(self, event_type: EventType, data=None):
        with self._ws_clients_lock:
            clients = list(self._ws_clients)
        if not clients:
            return
        if event_type in (EventType.CHANGED_CHARACTERS, EventType.CHANGED_FACES):
            payload = {
                "type": "characters_changed",
                "event": event_type.name,
            }
        elif event_type == EventType.CHANGED_DESCRIPTIONS:
            picture_ids = data if isinstance(data, (list, tuple, set)) else []
            payload = {
                "type": "descriptions_changed",
                "event": event_type.name,
                "picture_ids": list(picture_ids),
            }
        elif event_type in (EventType.CHANGED_TAGS, EventType.CLEARED_TAGS):
            picture_ids = data if isinstance(data, (list, tuple, set)) else []
            payload = {
                "type": "tags_changed",
                "event": event_type.name,
                "picture_ids": list(picture_ids),
            }
        elif event_type == EventType.PICTURE_IMPORTED:
            if isinstance(data, dict):
                picture_ids = data.get("ids") or []
                source = data.get("source", "external")
            else:
                picture_ids = data if isinstance(data, (list, tuple, set)) else []
                source = "external"
            payload = {
                "type": "picture_imported",
                "event": event_type.name,
                "picture_ids": list(picture_ids),
                "source": source,
            }
        elif event_type == EventType.PLUGIN_PROGRESS:
            progress_payload = data if isinstance(data, dict) else {}
            payload = {
                "type": "plugin_progress",
                "event": event_type.name,
                **progress_payload,
            }
        elif event_type in _WS_SNAPSHOT_EVENT_TYPES:
            info = data if isinstance(data, dict) else {}
            payload = {
                **info,
                "type": _WS_SNAPSHOT_EVENT_TYPES[event_type],
                "event": event_type.name,
            }
        else:
            picture_ids = data if isinstance(data, (list, tuple, set)) else []
            payload = {
                "type": "pictures_changed",
                "event": event_type.name,
                "picture_ids": list(picture_ids) if picture_ids else [],
            }
        stale = []
        for client in clients:
            ws = client.get("ws")
            filters = client.get("filters") or {}
            if not ws:
                stale.append(client)
                continue
            if not self._should_send_ws_update(event_type, filters):
                continue
            try:
                logger.debug("Sending websocket event: %s", payload)
                await ws.send_json(payload)
            except Exception:
                stale.append(client)
        if stale:
            with self._ws_clients_lock:
                for client in stale:
                    if client in self._ws_clients:
                        self._ws_clients.remove(client)

    def _generate_missing_thumbnails(self):
        def fetch_pictures(session):
            return session.exec(select(Picture.id, Picture.file_path)).all()

        rows = self.vault.db.run_immediate_read_task(fetch_pictures)
        if not rows:
            logger.info("No pictures found for thumbnail generation.")
            return

        missing = []
        for row in rows:
            pic_id, file_path = row
            if not file_path:
                continue
            thumb_path = ImageUtils.get_thumbnail_path(self.vault.image_root, file_path)
            if thumb_path and os.path.exists(thumb_path):
                continue
            missing.append((pic_id, file_path))

        total = len(missing)
        if total == 0:
            logger.debug("All thumbnails already exist.")
            return

        logger.info("Generating %s missing thumbnails at startup.", total)
        generated = 0
        skipped = 0
        missing_source_count = 0
        for index, (pic_id, file_path) in enumerate(missing, start=1):
            resolved = ImageUtils.resolve_picture_path(self.vault.image_root, file_path)
            if not resolved or not os.path.exists(resolved):
                missing_source_count += 1
                skipped += 1
                logger.warning(
                    "Missing source file for thumbnail generation: %s", resolved
                )
                if (
                    missing_source_count == 1
                    and not Server.DEFAULT_CLEANUP_MISSING_PICTURES
                ):
                    logger.info(
                        "Startup cleanup tip: run with '--cleanup-missing-pictures' "
                        "to remove stale picture records that point to missing files."
                    )
                continue
            img = ImageUtils.load_image_or_video(resolved)
            if img is None:
                skipped += 1
                logger.warning(
                    "Failed to load image for thumbnail generation: %s", resolved
                )
                continue
            if not isinstance(img, Image.Image):
                img = Image.fromarray(img)
            thumbnail_bytes = ImageUtils.generate_thumbnail_bytes(img)
            if not thumbnail_bytes:
                skipped += 1
                logger.warning(
                    "Failed to generate thumbnail bytes for picture %s", pic_id
                )
                continue
            saved = ImageUtils.write_thumbnail_bytes(
                self.vault.image_root, file_path, thumbnail_bytes
            )
            if saved:
                generated += 1
            else:
                skipped += 1
                logger.warning("Failed to persist thumbnail for picture %s", pic_id)
            if index % 250 == 0:
                logger.info("Thumbnail generation progress: %s/%s", index, total)

        logger.info(
            "Thumbnail generation completed: %s generated, %s skipped (%s missing source files).",
            generated,
            skipped,
            missing_source_count,
        )

    def _cleanup_missing_pictures(self):
        def fetch_pictures(session):
            return session.exec(select(Picture.id, Picture.file_path)).all()

        rows = self.vault.db.run_immediate_read_task(fetch_pictures)
        if not rows:
            logger.info("No pictures found for startup missing-file cleanup.")
            return

        missing_ids = []
        thumbnail_candidates = []
        for row in rows:
            pic_id, file_path = row
            resolved = None
            if file_path:
                resolved = ImageUtils.resolve_picture_path(
                    self.vault.image_root, file_path
                )
            if not resolved or not os.path.isfile(resolved):
                missing_ids.append(pic_id)
                if file_path:
                    thumbnail_candidates.append(file_path)

        if not missing_ids:
            logger.info("Startup missing-file cleanup found no stale picture records.")
            return

        logger.warning(
            "Startup missing-file cleanup removing %s stale picture records.",
            len(missing_ids),
        )

        def delete_rows(session, ids: list[int]):
            deleted_count = 0
            pictures = session.exec(select(Picture).where(Picture.id.in_(ids))).all()
            for pic in pictures:
                session.delete(pic)
                deleted_count += 1
            session.commit()
            return deleted_count

        deleted_count = self.vault.db.run_task(delete_rows, missing_ids)

        thumbnails_removed = 0
        for rel_path in thumbnail_candidates:
            thumb_path = ImageUtils.get_thumbnail_path(self.vault.image_root, rel_path)
            if not thumb_path or not os.path.isfile(thumb_path):
                continue
            try:
                os.remove(thumb_path)
                thumbnails_removed += 1
            except Exception as exc:
                logger.warning(
                    "Failed to delete orphan thumbnail %s: %s", thumb_path, exc
                )

        logger.info(
            "Startup missing-file cleanup completed: %s records removed, %s orphan thumbnails removed.",
            deleted_count,
            thumbnails_removed,
        )

    def run(self):
        self._shutdown_on_lifespan = True
        version = self._get_version()
        host = self._server_config.get("host", "127.0.0.1")
        port = self._server_config.get("port", 9537)
        scheme = "https" if self._server_config.get("require_ssl", False) else "http"
        server_url = f"{scheme}://{host}:{port}"
        _w = 54
        _b = "═" * _w
        print(
            f"\n"
            f"  ╔{_b}╗\n"
            f"  ║{'  PixlStash  v' + version:<{_w}}║\n"
            f"  ╠{_b}╣\n"
            f"  ║{'  GitHub : https://github.com/pikselkroken/pixlstash':<{_w}}║\n"
            f"  ║{'  Server : ' + server_url:<{_w}}║\n"
            f"  ╚{_b}╝\n"
        )
        uvicorn_kwargs = dict(
            host=host,
            port=port,
            log_config=uvicorn_log_config,
        )
        if self._server_config.get("require_ssl", False):
            uvicorn_kwargs["ssl_keyfile"] = self._server_config.get("ssl_keyfile")
            uvicorn_kwargs["ssl_certfile"] = self._server_config.get("ssl_certfile")
            print(
                f"[SSL] Running with SSL: keyfile={self._server_config.get('ssl_keyfile')}, certfile={self._server_config.get('ssl_certfile')}"
            )
        try:
            uvicorn.run(self.api, **uvicorn_kwargs)
        finally:
            if hasattr(self, "vault"):
                self.vault.close()

    @asynccontextmanager
    async def lifespan(self, app):
        # Startup logic
        loop = asyncio.get_running_loop()
        # Only claim _ws_loop if nothing else (e.g. a WebSocket handler) has set it
        # yet. This avoids overwriting the WebSocket loop when TestClient creates a
        # fresh event loop per HTTP request.
        was_set_by_us = self._ws_loop is None
        if was_set_by_us:
            self._ws_loop = loop
        if Server.DEFAULT_CLEANUP_MISSING_PICTURES:
            await loop.run_in_executor(None, self._cleanup_missing_pictures)
        if self._server_config.get("generate_thumbnails_on_startup", True):
            await loop.run_in_executor(None, self._generate_missing_thumbnails)
        self.vault.start()
        host = self._server_config.get("host", "127.0.0.1")
        port = self._server_config.get("port", 9537)
        scheme = "https" if self._server_config.get("require_ssl", False) else "http"
        logger.info(
            "PixlStash is ready. Open in your browser: %s://%s:%s/", scheme, host, port
        )
        yield
        # Shutdown logic — only clear _ws_loop if this lifespan instance set it
        if was_set_by_us:
            self._ws_loop = None
        if self._shutdown_on_lifespan and hasattr(self, "vault"):
            self.vault.close()

    @staticmethod
    def init_server_config(server_config_path):
        config_dir = os.path.dirname(server_config_path)
        os.makedirs(config_dir, exist_ok=True)

        # SSL certs are always stored in the platform user-config dir so they
        # stay in a consistent, writable location regardless of where the
        # server-config file itself resides (e.g. a custom --server-config path).
        _ssl_dir = os.path.join(user_config_dir("pixlstash"), "ssl")
        default_log_path = os.path.join(config_dir, "server.log")
        default_ssl_cert_path = os.path.join(_ssl_dir, "cert.pem")
        default_ssl_key_path = os.path.join(_ssl_dir, "key.pem")
        default_image_root = os.path.join(config_dir, "images")

        server_config = {}
        if not os.path.exists(server_config_path):
            server_config = {
                "host": "localhost",
                "port": 9537,
                "log_level": "info",
                "log_file": default_log_path,
                "require_ssl": False,
                "ssl_keyfile": default_ssl_key_path,
                "ssl_certfile": default_ssl_cert_path,
                "cookie_samesite": "Lax",
                "cookie_secure": False,
                "image_root": default_image_root,
                "default_device": "auto",
                "min_free_disk_gb": 1.0,
                "min_free_vram_mb": 1024.0,
                "cors_origins": [],
                "max_attachment_size_mb": 50,
                "filesystem_roots": [],
            }
            with open(server_config_path, "w") as f:
                json.dump(server_config, f, indent=2)
        else:
            with open(server_config_path, "r") as f:
                server_config = json.load(f)

                # Ensure server config options exist
                if "host" not in server_config:
                    server_config["host"] = "localhost"
                if "port" not in server_config:
                    server_config["port"] = 8000
                if "log_level" not in server_config:
                    server_config["log_level"] = "info"
                if "log_file" not in server_config:
                    server_config["log_file"] = default_log_path
                if "require_ssl" not in server_config:
                    server_config["require_ssl"] = False
                if "ssl_keyfile" not in server_config:
                    server_config["ssl_keyfile"] = default_ssl_key_path
                if "ssl_certfile" not in server_config:
                    server_config["ssl_certfile"] = default_ssl_cert_path
                if "cookie_samesite" not in server_config:
                    server_config["cookie_samesite"] = "Lax"
                if "cookie_secure" not in server_config:
                    server_config["cookie_secure"] = False
                if "image_root" not in server_config:
                    server_config["image_root"] = default_image_root
                if "default_device" not in server_config:
                    server_config["default_device"] = "auto"
                if "min_free_disk_gb" not in server_config:
                    server_config["min_free_disk_gb"] = 1.0
                if "min_free_vram_mb" not in server_config:
                    server_config["min_free_vram_mb"] = 1024.0
                if "cors_origins" not in server_config:
                    server_config["cors_origins"] = []
                if "max_attachment_size_mb" not in server_config:
                    server_config["max_attachment_size_mb"] = 50
                if "generate_thumbnails_on_startup" not in server_config:
                    server_config["generate_thumbnails_on_startup"] = True
                if "filesystem_roots" not in server_config:
                    server_config["filesystem_roots"] = []
                if "daily_snapshots" not in server_config:
                    server_config["daily_snapshots"] = True

        # Resolve SSL paths that are relative: interpret them relative to the
        # config file's directory, not the process's CWD, so that the certs
        # always live alongside the config regardless of where the server is
        # launched from.
        for key in ("ssl_keyfile", "ssl_certfile"):
            value = server_config.get(key)
            if value and not os.path.isabs(value):
                server_config[key] = os.path.join(config_dir, value)

        # Apply any test-level port override (set by the pytest conftest before
        # Server is instantiated). This lets tests run on a free port even when
        # the production server is already occupying the configured port.
        if Server.DEFAULT_PORT is not None:
            server_config["port"] = Server.DEFAULT_PORT

        return server_config

    def _ensure_ssl_certificates(self):
        import datetime

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        keyfile = self._server_config.get("ssl_keyfile")
        certfile = self._server_config.get("ssl_certfile")
        # If either file is missing, generate self-signed cert
        if not (os.path.exists(keyfile) and os.path.exists(certfile)):
            os.makedirs(os.path.dirname(keyfile), exist_ok=True)
            os.makedirs(os.path.dirname(certfile), exist_ok=True)
            print(f"[SSL] Generating self-signed certificate: {certfile}, {keyfile}")
            try:
                private_key = rsa.generate_private_key(
                    public_exponent=65537,
                    key_size=2048,
                )
                subject = issuer = x509.Name(
                    [x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]
                )
                now = datetime.datetime.now(datetime.timezone.utc)
                cert = (
                    x509.CertificateBuilder()
                    .subject_name(subject)
                    .issuer_name(issuer)
                    .public_key(private_key.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(now)
                    .not_valid_after(now + datetime.timedelta(days=365))
                    .add_extension(
                        x509.SubjectAlternativeName([x509.DNSName("localhost")]),
                        critical=False,
                    )
                    .sign(private_key, hashes.SHA256())
                )
                with open(keyfile, "wb") as f:
                    f.write(
                        private_key.private_bytes(
                            serialization.Encoding.PEM,
                            serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption(),
                        )
                    )
                with open(certfile, "wb") as f:
                    f.write(cert.public_bytes(serialization.Encoding.PEM))
            except Exception as e:
                print(f"[SSL] Failed to generate self-signed certificate: {e}")
                raise

    def _add_cors_exception_handler(self):
        @self.api.exception_handler(HTTPException)
        async def cors_exception_handler(request, exc):
            origin = request.headers.get("origin")
            headers = {
                "Access-Control-Allow-Credentials": "true",
            }
            if origin and (
                origin in self.allow_origins
                or (
                    self.allow_origin_regex
                    and re.match(self.allow_origin_regex, origin)
                )
            ):
                headers["Access-Control-Allow-Origin"] = origin
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=headers,
            )

        @self.api.exception_handler(Exception)
        async def generic_exception_handler(request, exc):
            logger.error(f"Unhandled exception: {exc}")
            origin = request.headers.get("origin")
            headers = {
                "Access-Control-Allow-Credentials": "true",
            }
            if origin and (
                origin in self.allow_origins
                or (
                    self.allow_origin_regex
                    and re.match(self.allow_origin_regex, origin)
                )
            ):
                headers["Access-Control-Allow-Origin"] = origin
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal Server Error"},
                headers=headers,
            )

        @self.api.exception_handler(RequestValidationError)
        async def validation_exception_handler(request, exc):
            origin = request.headers.get("origin")
            headers = {
                "Access-Control-Allow-Credentials": "true",
            }
            if origin and (
                origin in self.allow_origins
                or (
                    self.allow_origin_regex
                    and re.match(self.allow_origin_regex, origin)
                )
            ):
                headers["Access-Control-Allow-Origin"] = origin

            detail = exc.errors()
            for err in detail:
                if err.get("type") == "string_too_short" and "password" in (
                    err.get("loc") or []
                ):
                    return JSONResponse(
                        status_code=422,
                        content={
                            "detail": "Password must be at least 8 characters long."
                        },
                        headers=headers,
                    )

            return JSONResponse(
                status_code=422,
                content={"detail": detail},
                headers=headers,
            )

    def _get_version(self):
        # Prefer pyproject.toml when running from the repo so that the version
        # is always authoritative and never stale from an old editable install.
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        pyproject_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "pyproject.toml"
        )
        try:
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
            ver = data.get("project", {}).get("version")
            if ver:
                return ver
        except OSError as exc:
            logger.debug("Could not read version from pyproject.toml: %s", exc)

        # Fall back to installed package metadata (pip install / wheel deployment).
        try:
            return package_version("pixlstash")
        except PackageNotFoundError:
            return "unknown"

    def _get_frontend_dist_dir(self):
        package_dir = os.path.abspath(os.path.dirname(__file__))
        packaged_dist_dir = os.path.join(package_dir, "frontend", "dist")
        if os.path.isdir(packaged_dist_dir):
            return packaged_dist_dir

        repo_root = os.path.abspath(os.path.join(package_dir, ".."))
        repo_dist_dir = os.path.join(repo_root, "frontend", "dist")
        if os.path.isdir(repo_dist_dir):
            return repo_dist_dir

        return None

    def _get_frontend_index_path(self):
        dist_dir = self._get_frontend_dist_dir()
        if not dist_dir:
            return None
        index_path = os.path.join(dist_dir, "index.html")
        if not os.path.isfile(index_path):
            return None
        return index_path

    def _install_custom_openapi(self):
        """Post-process the generated OpenAPI schema for the reference UI.

        Two fixes, both stemming from the schema FastAPI emits by default:

        * **Bearer auth** — auth is enforced by middleware, not per-route
          dependencies, so FastAPI declares no ``securitySchemes`` and the docs'
          example code omits the ``Authorization`` header. We declare an HTTP
          Bearer scheme and attach it to every operation that actually requires
          auth (same public-path rules as the middleware).
        * **Response examples** — most response models are Pydantic
          ``Optional[...]`` (``anyOf: [T, null]``) with no example, which Scalar
          renders as a bare ``null``. We synthesize a shape-correct example for
          each 2xx JSON response so endpoints show their actual structure.
        """

        build_schema = self.api.openapi

        def custom_openapi():
            if self.api.openapi_schema:
                return self.api.openapi_schema
            schema = build_schema()
            # A server entry lets the reference UI build concrete request URLs
            # for its code samples; without one Scalar renders an empty example.
            # Relative so it resolves against whatever origin serves the docs.
            schema.setdefault("servers", [{"url": "/", "description": "This server"}])
            components = schema.setdefault("components", {})
            components.setdefault("securitySchemes", {})["bearerAuth"] = {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": (
                    "Personal API token from Account Settings → API Tokens. "
                    "Read-only tokens may also be passed as a `?token=` query "
                    "parameter."
                ),
            }
            requirement = [{"bearerAuth": []}]
            all_schemas = components.get("schemas", {})
            http_methods = {"get", "post", "put", "patch", "delete"}
            for path, path_item in schema.get("paths", {}).items():
                public = is_auth_excluded_path(path)
                for method, operation in path_item.items():
                    if method.lower() not in http_methods:
                        continue
                    if not public:
                        operation["security"] = requirement
                    _inject_response_examples(operation, all_schemas)

            # Lead the reference with the picture listing (the most useful
            # starting point) by ordering its path first. This is presentation
            # only — it does not affect route matching.
            paths = schema.get("paths", {})
            lead_path = f"{API_V1_PREFIX}/pictures"
            if lead_path in paths:
                schema["paths"] = {
                    lead_path: paths[lead_path],
                    **{p: item for p, item in paths.items() if p != lead_path},
                }
            self.api.openapi_schema = schema
            return schema

        self.api.openapi = custom_openapi

    def _setup_routes(self):
        ###############################
        # Rate limiting              ##
        ###############################
        self.api.add_middleware(RateLimitMiddleware)

        ###############################
        # Static file endpoints      ##
        ###############################
        dist_dir = self._get_frontend_dist_dir()
        if dist_dir:
            assets_dir = os.path.join(dist_dir, "assets")
            if os.path.isdir(assets_dir):
                self.api.mount(
                    "/assets",
                    StaticFiles(directory=assets_dir),
                    name="frontend-assets",
                )

        # Images embedded in the API reference description (logo + token
        # screenshots). Bundled with the package and served same-origin so
        # /scalar works offline, without depending on pixlstash.dev. The
        # static docs generator copies the same files next to each published
        # page, so the page-relative URLs resolve there too.
        scalar_assets_dir = os.path.join(
            os.path.dirname(__file__), "data", "scalar-assets"
        )
        if os.path.isdir(scalar_assets_dir):
            self.api.mount(
                "/scalar-assets",
                StaticFiles(directory=scalar_assets_dir),
                name="scalar-assets",
            )

        @self.api.get("/", include_in_schema=False)
        async def read_root():
            index_path = self._get_frontend_index_path()
            if index_path:
                return FileResponse(index_path)
            version = self._get_version()
            return {"message": "PixlStash REST API", "version": version}

        @self.api.get("/version", tags=["server"])
        async def read_version():
            version = self._get_version()
            install_type = "docker" if Server.running_in_docker() else "pip"
            docker_variant = os.environ.get("PIXLSTASH_DOCKER_VARIANT", "gpu")
            logger.info(
                "[/version] PIXLSTASH_DOCKER_VARIANT=%r -> docker_variant=%r",
                os.environ.get("PIXLSTASH_DOCKER_VARIANT"),
                docker_variant,
            )
            return {
                "message": "PixlStash REST API",
                "version": version,
                "install_type": install_type,
                "docker_variant": docker_variant,
            }

        @self.api.get("/scalar", include_in_schema=False)
        async def scalar_reference():
            # Scalar API reference UI, rendered client-side from the live
            # OpenAPI schema. Served alongside the built-in Swagger /docs.
            return HTMLResponse(content=render_scalar_html("/openapi.json"))

        @self.api.get("/favicon.ico", include_in_schema=False)
        def favicon():
            index_path = self._get_frontend_index_path()
            if index_path:
                favicon_path = os.path.join(os.path.dirname(index_path), "favicon.ico")
                if os.path.isfile(favicon_path):
                    return FileResponse(
                        favicon_path, media_type="image/vnd.microsoft.icon"
                    )
            favicon_path = os.path.join(
                os.path.dirname(__file__), "..", "frontend", "public", "favicon.ico"
            )
            return FileResponse(favicon_path, media_type="image/vnd.microsoft.icon")

        @self.api.websocket(f"{API_V1_PREFIX}/ws/updates")
        async def websocket_updates(websocket: WebSocket):
            await websocket.accept()
            # Always refresh _ws_loop so it tracks the currently-running event loop.
            # In production (uvicorn) this is always the same loop; in tests each
            # WebSocket session may run on a different loop than HTTP requests.
            self._ws_loop = asyncio.get_running_loop()
            client = {"ws": websocket, "filters": {}}
            with self._ws_clients_lock:
                self._ws_clients.append(client)
            try:
                while True:
                    message = await websocket.receive_text()
                    if not message:
                        continue
                    try:
                        payload = json.loads(message)
                    except Exception:
                        continue
                    if payload.get("type") == "set_filters":
                        filters = {
                            "selected_character": payload.get("selected_character"),
                            "selected_set": payload.get("selected_set"),
                            "search_query": payload.get("search_query"),
                        }
                        client["filters"] = filters
            except WebSocketDisconnect:
                logger.debug("WebSocket client disconnected normally.")
            finally:
                with self._ws_clients_lock:
                    if client in self._ws_clients:
                        self._ws_clients.remove(client)

        self.api.include_router(
            create_config_router(self),
            prefix=API_V1_PREFIX,
            tags=["config"],
        )
        self.api.include_router(
            create_characters_router(self),
            prefix=API_V1_PREFIX,
            tags=["characters"],
        )
        self.api.include_router(
            create_picture_sets_router(self),
            prefix=API_V1_PREFIX,
            tags=["picture_sets"],
        )
        self.api.include_router(
            create_projects_router(self),
            prefix=API_V1_PREFIX,
            tags=["projects"],
        )
        self.api.include_router(
            create_tags_router(self),
            prefix=API_V1_PREFIX,
            tags=["tags"],
        )
        self.api.include_router(
            create_stacks_router(self),
            prefix=API_V1_PREFIX,
            tags=["stacks"],
        )
        # tag_predictions must be registered before pictures so that the
        # specific path /pictures/{id}/tag_predictions is not swallowed by
        # the wildcard /pictures/{id}/{field} route in the pictures router.
        self.api.include_router(
            create_tag_predictions_router(self),
            prefix=API_V1_PREFIX,
            tags=["tag_predictions"],
        )
        # guest_scores must be registered before pictures for the same reason:
        # /pictures/guest-scores must not be swallowed by /pictures/{id}/{field}.
        self.api.include_router(
            create_guest_scores_router(self),
            prefix=API_V1_PREFIX,
            tags=["guest_scores"],
        )
        self.api.include_router(
            create_pictures_router(self),
            prefix=API_V1_PREFIX,
            tags=["pictures"],
        )
        self.api.include_router(
            create_comfyui_router(self),
            prefix=API_V1_PREFIX,
            tags=["comfyui"],
        )
        self.api.include_router(
            create_reference_folders_router(self),
            prefix=API_V1_PREFIX,
        )
        self.api.include_router(
            create_import_folders_router(self),
            prefix=API_V1_PREFIX,
        )
        self.api.include_router(
            create_filesystem_router(self),
            prefix=API_V1_PREFIX,
            tags=["config"],
        )
        self.api.include_router(
            create_taggers_router(self),
            prefix=API_V1_PREFIX,
            tags=["taggers"],
        )
        self.api.include_router(
            create_snapshots_router(self),
            prefix=API_V1_PREFIX,
            tags=["snapshots"],
        )
        # Public share endpoint — no API prefix; auth is embedded in the URL token.
        self.api.include_router(
            create_share_router(self),
            tags=["share"],
        )

        @self.api.middleware("http")
        async def auth_middleware(request: Request, call_next):
            return await self.auth.auth_middleware(
                request,
                call_next,
                self.allow_origins,
                self.allow_origin_regex,
            )

        @self.api.get(f"{API_V1_PREFIX}/check-session", tags=["auth"])
        async def check_session(request: Request):
            return self.auth.check_session(request)

        @self.api.get(f"{API_V1_PREFIX}/network/info", tags=["server"])
        def network_info(request: Request):
            self.auth.require_user_id(request)
            try:
                lan_ip = socket.gethostbyname(socket.gethostname())
            except OSError:
                lan_ip = "127.0.0.1"
            import ipaddress

            try:
                addr = ipaddress.ip_address(lan_ip)
                is_private = addr.is_private or addr.is_loopback
            except ValueError:
                is_private = True
            return {"lan_ip": lan_ip, "is_private": is_private}

        @self.api.post(f"{API_V1_PREFIX}/login", tags=["auth"])
        def login(login_request: LoginRequest, http_request: Request):
            response = self.auth.login(login_request, http_request)
            self._user = self.auth.user
            return response

        @self.api.get(f"{API_V1_PREFIX}/login", tags=["auth"])
        def check_registration():
            return self.auth.check_registration()

        @self.api.post(f"{API_V1_PREFIX}/logout", tags=["auth"])
        def logout(response: Response, request: Request):
            return self.auth.logout(response, request)

        @self.api.get(f"{API_V1_PREFIX}/protected", include_in_schema=False)
        async def protected():
            return {"message": "You are authenticated!"}

        @self.api.get("/{full_path:path}", include_in_schema=False)
        async def frontend_fallback(full_path: str):
            dist_dir = self._get_frontend_dist_dir()
            if not dist_dir:
                raise HTTPException(status_code=404, detail="Not Found")

            safe_path = os.path.normpath(full_path).lstrip(os.sep)
            candidate = os.path.abspath(os.path.join(dist_dir, safe_path))
            if candidate.startswith(dist_dir) and os.path.isfile(candidate):
                return FileResponse(candidate)

            index_path = self._get_frontend_index_path()
            if not index_path:
                raise HTTPException(status_code=404, detail="Not Found")
            return FileResponse(index_path)
