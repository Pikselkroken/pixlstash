import gc
import uvicorn
import os
import json
import re
import asyncio
import threading


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
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pillow_heif import register_heif_opener

from sqlmodel import select

from pixlvault.db_models import (
    Picture,
    User,
)

from pixlvault.event_types import EventType
from pixlvault.auth import AuthService, LoginRequest
from pixlvault.pixl_logging import get_logger, uvicorn_log_config
from pixlvault.vault import Vault
from pixlvault.routes.config import create_router as create_config_router
from pixlvault.routes.characters import create_router as create_characters_router
from pixlvault.routes.picture_sets import create_router as create_picture_sets_router
from pixlvault.routes.tags import create_router as create_tags_router
from pixlvault.routes.stacks import create_router as create_stacks_router
from pixlvault.routes.pictures import create_router as create_pictures_router
from pixlvault.routes.comfyui import create_router as create_comfyui_router
from pixlvault.utils.image_processing.image_utils import ImageUtils


# Logging will be set up after config is loaded
logger = get_logger(__name__)


API_OPENAPI_TAGS = [
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
        "description": "Tag management for pictures, faces and hands.",
    },
    {
        "name": "stacks",
        "description": "Stack creation, ordering and membership operations.",
    },
    {
        "name": "pictures",
        "description": "Picture listing, metadata, thumbnails, import/export and media operations.",
    },
    {
        "name": "comfyui",
        "description": "ComfyUI workflow management and image-to-image execution.",
    },
]


class Server:
    """
    Main server class for the PixlVault FastAPI application.

    Attributes:
        server_config_path(str): Server-side-only configuration file.
    """

    def __init__(
        self,
        server_config_path,
    ):
        """
        Initialize the Server instance.

        Args:
            server_config_path (str): Path to the server-only config file.
        """

        # Ensure garbage collection before starting server to free up memory
        # This is mainly to ensure repeated runs within the testing framework do not accumulate memory usage
        gc.collect()

        self._server_config_path = server_config_path

        self._server_config = self._init_server_config(server_config_path)
        with open(server_config_path, "w") as f:
            json.dump(self._server_config, f, indent=2)

        # SSL config
        if self._server_config.get("require_ssl", False):
            self._ensure_ssl_certificates()

        logger.info(
            "Creating Vault instance with image root: "
            + str(self._server_config["image_root"])
        )

        register_heif_opener()

        self.vault = Vault(
            image_root=self._server_config["image_root"],
            description=User().description,
            server_config_path=self._server_config_path,
        )

        self._ws_clients = []
        self._ws_clients_lock = threading.Lock()
        self._ws_loop = None
        self.vault.add_event_listener(self._handle_vault_event)

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
        self.vault.set_max_vram_usage_gb(getattr(self._user, "max_vram_gb", None))

        self.api = FastAPI(
            title="PixlVault API",
            version=self._get_version(),
            description=(
                "PixlVault backend API for picture management, tagging, stacks, "
                "sets, character workflows and ComfyUI integration."
            ),
            openapi_tags=API_OPENAPI_TAGS,
            lifespan=self.lifespan,
        )
        # Enable CORS for any origin (credentials require explicit origin echo)
        self.allow_origins = []
        self.allow_origin_regex = r".*"
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

        # Temporary storage for export tasks
        self.export_tasks = {}
        self.TEMP_EXPORT_DIR = "tmp/exports"
        os.makedirs(self.TEMP_EXPORT_DIR, exist_ok=True)

        # Temporary storage for import tasks
        self.import_tasks = {}
        self._shutdown_on_lifespan = False

    def __enter__(self):
        # Allow use as a context manager for robust cleanup
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, "vault"):
            logger.warning("Closing the vault and cleaning up resources")
            self.vault.close()
        gc.collect()

    def _handle_vault_event(self, event_type: EventType, data=None):
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
        return event_type in (
            EventType.CHANGED_PICTURES,
            EventType.PICTURE_IMPORTED,
            EventType.PLUGIN_PROGRESS,
            EventType.CHANGED_TAGS,
            EventType.CLEARED_TAGS,
        )

    async def _broadcast_ws_event(self, event_type: EventType, data=None):
        with self._ws_clients_lock:
            clients = list(self._ws_clients)
        if not clients:
            return
        if event_type in (EventType.CHANGED_TAGS, EventType.CLEARED_TAGS):
            picture_ids = data if isinstance(data, (list, tuple, set)) else []
            payload = {
                "type": "tags_changed",
                "event": event_type.name,
                "picture_ids": list(picture_ids),
            }
        elif event_type == EventType.PICTURE_IMPORTED:
            picture_ids = data if isinstance(data, (list, tuple, set)) else []
            payload = {
                "type": "picture_imported",
                "event": event_type.name,
                "picture_ids": list(picture_ids),
            }
        elif event_type == EventType.PLUGIN_PROGRESS:
            progress_payload = data if isinstance(data, dict) else {}
            payload = {
                "type": "plugin_progress",
                "event": event_type.name,
                **progress_payload,
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
            logger.info("All thumbnails already exist.")
            return

        logger.info("Generating %s missing thumbnails at startup.", total)
        generated = 0
        skipped = 0
        for index, (pic_id, file_path) in enumerate(missing, start=1):
            resolved = ImageUtils.resolve_picture_path(self.vault.image_root, file_path)
            if not resolved or not os.path.exists(resolved):
                skipped += 1
                logger.warning(
                    "Missing source file for thumbnail generation: %s", resolved
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
            "Thumbnail generation completed: %s generated, %s skipped.",
            generated,
            skipped,
        )

    def run(self):
        self._shutdown_on_lifespan = True
        uvicorn_kwargs = dict(
            host="0.0.0.0",
            port=self._server_config.get("port", 8000),
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
        if self._server_config.get("generate_thumbnails_on_startup", True):
            await loop.run_in_executor(None, self._generate_missing_thumbnails)
        yield
        # Shutdown logic — only clear _ws_loop if this lifespan instance set it
        if was_set_by_us:
            self._ws_loop = None
        if self._shutdown_on_lifespan and hasattr(self, "vault"):
            self.vault.close()

    @staticmethod
    def _init_server_config(server_config_path):
        config_dir = os.path.dirname(server_config_path)
        os.makedirs(config_dir, exist_ok=True)

        default_log_path = os.path.join(config_dir, "server.log")
        default_ssl_cert_path = os.path.join(config_dir, "ssl", "cert.pem")
        default_ssl_key_path = os.path.join(config_dir, "ssl", "key.pem")
        default_image_root = os.path.join(config_dir, "images")

        server_config = {}
        if not os.path.exists(server_config_path):
            server_config = {
                "host": "localhost",
                "port": 8000,
                "log_level": "info",
                "log_file": default_log_path,
                "require_ssl": False,
                "ssl_keyfile": default_ssl_key_path,
                "ssl_certfile": default_ssl_cert_path,
                "cookie_samesite": "Lax",
                "cookie_secure": False,
                "image_root": default_image_root,
                "default_device": "cpu",
                "USERNAME": None,
                "watch_folders": [],
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
                    server_config["default_device"] = "cpu"
                if "USERNAME" not in server_config:
                    server_config["USERNAME"] = None
                if "watch_folders" not in server_config:
                    server_config["watch_folders"] = []
                if "generate_thumbnails_on_startup" not in server_config:
                    server_config["generate_thumbnails_on_startup"] = True

        return server_config

    def _ensure_ssl_certificates(self):
        import subprocess

        keyfile = self._server_config.get("ssl_keyfile")
        certfile = self._server_config.get("ssl_certfile")
        # If either file is missing, generate self-signed cert
        if not (os.path.exists(keyfile) and os.path.exists(certfile)):
            os.makedirs(os.path.dirname(keyfile), exist_ok=True)
            os.makedirs(os.path.dirname(certfile), exist_ok=True)
            print(f"[SSL] Generating self-signed certificate: {certfile}, {keyfile}")
            try:
                subprocess.run(
                    [
                        "openssl",
                        "req",
                        "-x509",
                        "-nodes",
                        "-days",
                        "365",
                        "-newkey",
                        "rsa:2048",
                        "-keyout",
                        keyfile,
                        "-out",
                        certfile,
                        "-subj",
                        "/CN=localhost",
                    ],
                    check=True,
                )
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
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        pyproject_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "pyproject.toml"
        )
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version", "unknown")

    def _get_frontend_dist_dir(self):
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        dist_dir = os.path.join(base_dir, "frontend", "dist")
        if not os.path.isdir(dist_dir):
            return None
        return dist_dir

    def _get_frontend_index_path(self):
        dist_dir = self._get_frontend_dist_dir()
        if not dist_dir:
            return None
        index_path = os.path.join(dist_dir, "index.html")
        if not os.path.isfile(index_path):
            return None
        return index_path

    def _setup_routes(self):
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

        @self.api.get("/")
        async def read_root():
            index_path = self._get_frontend_index_path()
            if index_path:
                return FileResponse(index_path)
            version = self._get_version()
            return {"message": "PixlVault REST API", "version": version}

        @self.api.get("/version")
        async def read_version():
            version = self._get_version()
            return {"message": "PixlVault REST API", "version": version}

        @self.api.get("/favicon.ico")
        def favicon():
            index_path = self._get_frontend_index_path()
            if index_path:
                favicon_path = os.path.join(os.path.dirname(index_path), "favicon.ico")
                if os.path.isfile(favicon_path):
                    return FileResponse(favicon_path)
            favicon_path = os.path.join(
                os.path.dirname(__file__), "..", "frontend", "public", "favicon.ico"
            )
            return FileResponse(favicon_path)

        @self.api.websocket("/ws/updates")
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
                pass
            finally:
                with self._ws_clients_lock:
                    if client in self._ws_clients:
                        self._ws_clients.remove(client)

        self.api.include_router(create_config_router(self), tags=["config"])
        self.api.include_router(create_characters_router(self), tags=["characters"])
        self.api.include_router(create_picture_sets_router(self), tags=["picture_sets"])
        self.api.include_router(create_tags_router(self), tags=["tags"])
        self.api.include_router(create_stacks_router(self), tags=["stacks"])
        self.api.include_router(create_pictures_router(self), tags=["pictures"])
        self.api.include_router(create_comfyui_router(self), tags=["comfyui"])

        ###############################
        # Config endpoints            #
        ###############################
        def _ensure_secure_when_required(request: Request):
            self.auth.ensure_secure_when_required(request)

        @self.api.middleware("http")
        async def auth_middleware(request: Request, call_next):
            return await self.auth.auth_middleware(
                request,
                call_next,
                self.allow_origins,
                self.allow_origin_regex,
            )

        @self.api.get("/check-session")
        async def check_session(request: Request):
            return self.auth.check_session(request)

        @self.api.post("/login")
        def login(request: LoginRequest):
            response = self.auth.login(request)
            self._user = self.auth.user
            return response

        @self.api.get("/login")
        def check_registration():
            return self.auth.check_registration()

        @self.api.post("/logout")
        def logout(response: Response, request: Request):
            return self.auth.logout(response, request)

        @self.api.get("/protected")
        async def protected():
            return {"message": "You are authenticated!"}

        @self.api.get("/{full_path:path}")
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
