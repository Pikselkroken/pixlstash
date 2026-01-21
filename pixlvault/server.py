import base64
import gc
import uvicorn
import os
import json
import re
import uuid
import mimetypes
import concurrent.futures
import sys
import time
import zipfile
from email.utils import formatdate
from datetime import datetime
import secrets

from collections import defaultdict, deque
from sqlalchemy.orm import load_only, selectinload
from sqlalchemy import exists
from sqlmodel import Session, delete, select

from contextlib import asynccontextmanager
from fastapi import Body, FastAPI, File, Request, UploadFile, Query, HTTPException
from fastapi import BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from pillow_heif import register_heif_opener
from typing import List, Optional
from passlib.hash import bcrypt
from pydantic import BaseModel, Field

from pixlvault.db_models import (
    Character,
    Face,
    Picture,
    PictureSet,
    PictureSetMember,
    SortMechanism,
    User,
    UserToken,
)

from pixlvault.db_models import PictureLikeness
from pixlvault.db_models.face_character_likeness import FaceCharacterLikeness
from pixlvault.db_models.tag import Tag
from pixlvault.database import DBPriority
from pixlvault.event_types import EventType
from pixlvault.utils import safe_model_dict
from pixlvault.picture_utils import PictureUtils
from pixlvault.pixl_logging import get_logger, uvicorn_log_config
from pixlvault.vault import Vault
from pixlvault.worker_registry import WorkerType
from pixlvault.watch_folder_worker import WatchFolderWorker

DEFAULT_DESCRIPTION = "PixlVault default configuration"

# Logging will be set up after config is loaded
logger = get_logger(__name__)


class Server:
    """
    Main server class for the PixlVault FastAPI application.

    Attributes:
        config_path(str): Remote accessible configuration file.
        server_config_path(str): Server-side-only configuration file.
    """

    def __init__(
        self,
        config_path,
        server_config_path,
    ):
        """
        Initialize the Server instance.

        Args:
            config_path (str): Path to the image roots config file.
            server_config_path (str): Path to the server-only config file.
        """

        # Ensure garbage collection before starting server to free up memory
        # This is mainly to ensure repeated runs within the testing framework do not accumulate memory usage
        gc.collect()

        self._config_path = config_path
        self._server_config_path = server_config_path

        self._config = self._init_config(config_path)
        with open(config_path, "w") as f:
            json.dump(self._config, f, indent=2)

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
            description=self._config.get("description"),
        )

        WatchFolderWorker.configure(self._server_config_path)

        self._user = self._ensure_user()
        if self._user and self._user.description is not None:
            self.vault.set_description(self._user.description)

        self.api = FastAPI(lifespan=self.lifespan)
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

        # Keep cached credentials for compatibility
        self.PASSWORD_HASH = self._user.password_hash if self._user else None
        self.USERNAME = self._user.username if self._user else None
        self.active_session_ids = {}

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

    def set_password_hash(self, hashed_password):
        def update_user(session: Session):
            user = session.exec(select(User)).first()
            if user is None:
                user = User()
            user.password_hash = hashed_password
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

        user = self.vault.db.run_task(update_user, priority=DBPriority.IMMEDIATE)
        self.PASSWORD_HASH = user.password_hash
        self._user = user
        print("Password hash stored in user database.")

    def set_username(self, username):
        def update_user(session: Session):
            user = session.exec(select(User)).first()
            if user is None:
                user = User()
            user.username = username
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

        user = self.vault.db.run_task(update_user, priority=DBPriority.IMMEDIATE)
        self.USERNAME = user.username
        self._user = user
        print("Username stored in user database.")

    def remove_password_hash(self):
        """Remove the PASSWORD_HASH and username by deleting them from the user table."""
        logger.info("Removing stored password hash from user database.")

        def clear_user(session: Session):
            user = session.exec(select(User)).first()
            if user is None:
                return None
            user.password_hash = None
            user.username = None
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

        user = self.vault.db.run_task(clear_user, priority=DBPriority.IMMEDIATE)
        self._user = user
        self.PASSWORD_HASH = None
        self.USERNAME = None
        self.active_session_ids = {}
        if "PASSWORD_HASH" in self._server_config:
            del self._server_config["PASSWORD_HASH"]
        if "USERNAME" in self._server_config:
            del self._server_config["USERNAME"]
            with open(self._server_config_path, "w") as f:
                json.dump(self._server_config, f, indent=2)

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
        uvicorn.run(self.api, **uvicorn_kwargs)

    @asynccontextmanager
    async def lifespan(self, app):
        # Startup logic (if needed)
        yield
        # Shutdown logic
        if self._shutdown_on_lifespan and hasattr(self, "vault"):
            self.vault.close()

    @staticmethod
    def create_config(**kwargs):
        """
        Create a config dict from provided keys in kwargs, using defaults for missing keys.
        """
        defaults = {
            "description": DEFAULT_DESCRIPTION,
            "sort": SortMechanism.Keys.DATE.name,
            "descending": True,
            "thumbnail_size": "default",
            "show_stars": True,
            "similarity_character": None,
        }
        config = defaults.copy()
        config.update({k: v for k, v in kwargs.items() if v is not None})
        return config

    @staticmethod
    def _init_config(config_path):
        """
        Initialize and load the server configuration from file, creating defaults if necessary.
        Returns:
            dict: Configuration dictionary.
        """
        config_dir = os.path.dirname(config_path)
        os.makedirs(config_dir, exist_ok=True)
        if not os.path.exists(config_path):
            config = Server.create_config(config_dir=config_dir)
        else:
            with open(config_path, "r") as f:
                config = json.load(f)
            # Fill in missing keys with defaults
            defaults = Server.create_config(config_dir=config_dir)
            for k, v in defaults.items():
                if k not in config:
                    config[k] = v
        # Remove server-only fields from public config
        for key in ("image_root", "default_device"):
            config.pop(key, None)
        return config

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

        return server_config

    def _normalize_thumbnail_size(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            if value.lower() == "default":
                return None
            if value.isdigit():
                return int(value)
            return None
        if isinstance(value, (int, float)):
            return int(value)
        return None

    def _ensure_user(self):
        defaults = self._config if isinstance(self._config, dict) else {}

        def ensure_user(session: Session):
            user = session.exec(select(User)).first()
            if user:
                logger.info("Found user in the database: %s", user.username)
                return user

            thumbnail_value = defaults.get("thumbnail_size", defaults.get("thumbnail"))
            user = User(
                username=self._server_config.get("USERNAME"),
                password_hash=self._server_config.get("PASSWORD_HASH"),
                description=defaults.get("description", DEFAULT_DESCRIPTION),
                sort=defaults.get("sort", SortMechanism.Keys.DATE.name),
                descending=bool(defaults.get("descending", True)),
                thumbnail_size=self._normalize_thumbnail_size(thumbnail_value),
                show_stars=bool(defaults.get("show_stars", True)),
                similarity_character=defaults.get("similarity_character"),
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

        return self.vault.db.run_task(ensure_user, priority=DBPriority.IMMEDIATE)

    def _get_user(self):
        return self.vault.db.run_task(
            lambda session: session.exec(select(User)).first(),
            priority=DBPriority.IMMEDIATE,
        )

    def _user_to_config(self, user: User):
        defaults = Server.create_config()
        if not user:
            return defaults

        sort_value = user.sort or defaults.get("sort")
        return {
            "description": user.description or defaults.get("description"),
            "sort": sort_value,
            "sort_order": sort_value,
            "descending": bool(user.descending),
            "columns": int(user.columns),
            "show_stars": bool(user.show_stars),
            "similarity_character": user.similarity_character,
        }

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

    def _create_picture_imports(self, uploaded_files, dest_folder):
        """
        Given a list of (img_bytes, ext), create Picture objects for new images,
        skipping duplicates based on pixel_sha hash.
        Returns (shas, existing_map, new_pictures)
        """

        def create_sha(img_bytes):
            return PictureUtils.calculate_hash_from_bytes(img_bytes)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            shas = list(
                executor.map(create_sha, (img_bytes for img_bytes, _ in uploaded_files))
            )

        existing_pictures = self.vault.db.run_immediate_read_task(
            lambda session: Picture.find(session, pixel_shas=shas)
        )

        existing_map = {pic.pixel_sha: pic for pic in existing_pictures}

        importable = [
            (entry, sha)
            for (entry, sha) in zip(uploaded_files, shas)
            if sha not in existing_map
        ]

        if importable:

            def create_one_picture(args):
                file_entry, sha = args
                img_bytes, ext = file_entry
                pic_uuid = str(uuid.uuid4()) + ext
                logger.debug(f"Importing picture from uploaded bytes as id={pic_uuid}")
                return PictureUtils.create_picture_from_bytes(
                    image_root_path=dest_folder,
                    image_bytes=img_bytes,
                    picture_uuid=pic_uuid,
                    pixel_sha=sha,
                )

            with concurrent.futures.ThreadPoolExecutor() as executor:
                new_pictures = list(executor.map(create_one_picture, importable))
        else:
            new_pictures = []

        return shas, existing_map, new_pictures

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

    def _find_reference_character_id_for_set(self, picture_set_id):
        # Find reference_character_id if this is a reference set
        reference_character_id = None

        def find_reference_character(session, picture_set_id):
            character = Character.find(
                session,
                select_fields=["reference_picture_set_id"],
                reference_picture_set_id=picture_set_id,
            )
            logger.info(
                f"Found reference character for set {picture_set_id}: {character}"
            )
            return character[0].id if character else None

        reference_character_id = self.vault.db.run_immediate_read_task(
            find_reference_character, picture_set_id
        )
        return reference_character_id

    def _find_pictures_by_character_likeness(
        self, character_id, reference_character_id, offset, limit, descending
    ):
        reference_character_id = int(reference_character_id)

        # List pictures by likeness to character
        # 1. Fetch reference faces from the reference pictures set for character
        # 2. Fetch all faces
        # 3. Order them by average likeness to reference faces
        # 4. Return pictures containing those faces in the same order as the faces
        def get_character_reference_faces(session, reference_character_id):
            # Need to get pictures in the reference set for this character
            character = Character.find(session, id=reference_character_id)
            reference_set = session.get(
                PictureSet, character[0].reference_picture_set_id
            )
            if not reference_set:
                return []
            members = session.exec(
                select(PictureSetMember).where(
                    PictureSetMember.set_id == reference_set.id
                )
            ).all()
            picture_ids = [m.picture_id for m in members]
            if not picture_ids:
                logger.warning(
                    f"No pictures in reference set id={reference_set.id} for character id={character_id}"
                )
                return []
            faces = Face.find(session, picture_id=picture_ids)
            return faces

        # 1. Get reference faces (use set of face IDs for uniqueness)
        reference_faces = self.vault.db.run_task(
            get_character_reference_faces,
            reference_character_id,
            priority=DBPriority.IMMEDIATE,
        )

        if not reference_faces:
            logger.warning(f"No reference faces found for character id={character_id}")
            return []

        # 2. Get all faces
        def get_all_faces(session, character_id):
            query = select(Face)
            if character_id == "ALL" or character_id is None:
                pass
            elif character_id == "UNASSIGNED":
                query = query.where(Face.character_id.is_(None))
            else:
                query = query.where(Face.character_id == int(character_id))
            faces = session.exec(query).all()
            return faces

        candidate_faces = self.vault.db.run_task(get_all_faces, character_id)
        if not candidate_faces:
            logger.warning("No unassigned faces found")
            return []

        # Fetch likeness scores directly from FaceCharacterLikeness
        def fetch_character_likeness(session, reference_character_id):
            rows = session.exec(
                select(
                    FaceCharacterLikeness.face_id,
                    FaceCharacterLikeness.likeness,
                ).where(FaceCharacterLikeness.character_id == reference_character_id)
            ).all()
            return {row.face_id: row.likeness for row in rows}

        character_likeness_map = self.vault.db.run_task(
            fetch_character_likeness, reference_character_id
        )

        # Debug logging for character likeness map
        logger.debug(f"Character likeness map: {character_likeness_map}")

        # 3. Get unique picture IDs in that order
        # For each picture, use the maximum character_likeness among all its unassigned faces
        picture_likeness_map = {}
        for face in candidate_faces:
            pic_id = face.picture_id
            likeness = character_likeness_map.get(face.id, 0.0)
            if pic_id not in picture_likeness_map:
                picture_likeness_map[pic_id] = likeness
            else:
                picture_likeness_map[pic_id] = max(
                    picture_likeness_map[pic_id], likeness
                )

        # Debug logging for picture likeness map
        logger.debug(f"Picture likeness map: {picture_likeness_map}")

        # Fetch Picture objects
        candidate_pics = self.vault.db.run_task(
            Picture.find,
            id=list(picture_likeness_map.keys()),
            select_fields=Picture.metadata_fields() | {"characters"},
        )

        # Assign character_likeness to pictures
        dicts = []
        for pic in candidate_pics:
            if character_id == "UNASSIGNED":
                character_ids = [c.id for c in pic.characters]
                if reference_character_id in character_ids or character_ids:
                    # Skip pictures that already have any characters assigned
                    continue
            pic_dict = safe_model_dict(pic)
            pic_id = pic_dict["id"]
            pic_dict["character_likeness"] = picture_likeness_map.get(pic_id, 0.0)
            dicts.append(pic_dict)

        # Sort by character_likeness descending
        dicts.sort(key=lambda x: x["character_likeness"], reverse=True)

        # Apply offset and limit
        selected_pics = dicts[offset : offset + limit]
        return selected_pics

    def _setup_routes(self):
        ###############################
        # Static file endpoints      ##
        ###############################
        @self.api.get("/")
        async def read_root():
            version = self._get_version()
            return {"message": "PixlVault REST API", "version": version}

        @self.api.get("/favicon.ico")
        def favicon():
            favicon_path = os.path.join(
                os.path.dirname(__file__), "..", "frontend", "public", "favicon.ico"
            )
            return FileResponse(favicon_path)

        @self.api.get("/sort_mechanisms")
        async def get_pictures_sort_mechanisms():
            """Return available sorting mechanisms for pictures."""
            result = SortMechanism.all()
            logger.info("Returning sort mechanisms: {}".format(result))
            return result

        ###############################
        # Config endpoints            #
        ###############################
        def _ensure_secure_when_required(request: Request):
            if self._server_config.get("require_ssl", False):
                if request.url.scheme != "https":
                    raise HTTPException(
                        status_code=403,
                        detail="HTTPS is required for this operation.",
                    )

        class ChangePasswordRequest(BaseModel):
            current_password: Optional[str] = None
            new_password: str = Field(
                ...,
                min_length=8,
                description="Password must be at least 8 characters long",
            )

        class CreateTokenRequest(BaseModel):
            description: Optional[str] = None

        @self.api.get("/users/me/config")
        async def get_me_config(request: Request):
            _ensure_secure_when_required(request)
            session_id = request.cookies.get("session_id")
            if not session_id:
                raise HTTPException(status_code=401, detail="Not authenticated")
            user_id = self.active_session_ids.get(session_id)
            if user_id is None:
                raise HTTPException(status_code=401, detail="Not authenticated")
            user = self.vault.db.run_task(
                lambda session: session.get(User, user_id),
                priority=DBPriority.IMMEDIATE,
            )
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            return self._user_to_config(user)

        @self.api.patch("/users/me/config")
        async def patch_me_config(request: Request):
            _ensure_secure_when_required(request)
            import time

            session_id = request.cookies.get("session_id")
            if not session_id:
                raise HTTPException(status_code=401, detail="Not authenticated")
            user_id = self.active_session_ids.get(session_id)
            if user_id is None:
                raise HTTPException(status_code=401, detail="Not authenticated")

            start_time = time.time()
            logger.info(f"[TIMING] PATCH /users/me/config called at {start_time:.3f}")
            patch_data = await request.json()
            allowed_keys = {
                "description",
                "sort",
                "descending",
                "columns",
                "show_stars",
                "similarity_character",
            }

            def update_user(session: Session, user_id: int):
                user = session.get(User, user_id)
                if user is None:
                    raise HTTPException(status_code=404, detail="User not found")

                updated = False
                for key, value in patch_data.items():
                    logger.info(f"Updating config key '{key}' with value: {value}")
                    if key not in allowed_keys:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Key '{key}' does not exist in config.",
                        )
                    if key == "similarity_character":
                        if value in ("", None, "null"):
                            new_value = None
                        elif isinstance(value, str) and value.isdigit():
                            new_value = int(value)
                        else:
                            new_value = value
                        if user.similarity_character != new_value:
                            user.similarity_character = new_value
                            updated = True
                        continue
                    if key == "columns":
                        user.columns = int(value)
                        updated = True
                        logger.info(f"Set user.columns to {user.columns}")
                        continue
                    current_value = getattr(user, key, None)
                    if current_value != value:
                        setattr(user, key, value)
                        updated = True

                if updated:
                    session.add(user)
                    session.commit()
                    session.refresh(user)
                return user, updated

            user, updated = self.vault.db.run_task(
                update_user, user_id, priority=DBPriority.IMMEDIATE
            )
            elapsed = time.time() - start_time
            logger.info(
                f"[TIMING] PATCH /users/me/config completed in {elapsed:.3f} seconds"
            )
            return {
                "status": "success",
                "updated": updated,
                "config": self._user_to_config(user),
            }

        @self.api.post("/users/me/auth")
        async def change_me_password(payload: ChangePasswordRequest, request: Request):
            _ensure_secure_when_required(request)
            session_id = request.cookies.get("session_id")
            if not session_id:
                raise HTTPException(status_code=401, detail="Not session id provided")
            user_id = self.active_session_ids.get(session_id)
            if user_id is None:
                raise HTTPException(status_code=401, detail="Not authenticated")

            logger.info("Looking for user id: {}".format(user_id))
            user = self.vault.db.run_task(
                lambda session: session.get(User, user_id),
                priority=DBPriority.IMMEDIATE,
            )
            if user is None:
                logger.error("No user found with id: {}".format(user_id))
                raise HTTPException(status_code=404, detail="User not found")

            if user.password_hash:
                if not payload.current_password:
                    raise HTTPException(
                        status_code=400,
                        detail="Current password is required",
                    )
                if not bcrypt.verify(payload.current_password, user.password_hash):
                    raise HTTPException(status_code=401, detail="Invalid password")

            hashed_password = bcrypt.hash(payload.new_password)

            def update_user(session: Session, user_id: int):
                db_user = session.get(User, user_id)
                if db_user is None:
                    logger.info(f"User {user_id} not found in DB when updating")
                    raise HTTPException(
                        status_code=404, detail="User not found when updating"
                    )
                db_user.password_hash = hashed_password
                session.add(db_user)
                session.commit()
                session.refresh(db_user)
                return db_user

            updated_user = self.vault.db.run_task(
                update_user, user_id, priority=DBPriority.IMMEDIATE
            )
            self._user = updated_user
            self.PASSWORD_HASH = updated_user.password_hash
            self.USERNAME = updated_user.username
            self.active_session_ids = {}
            return {"status": "success"}

        @self.api.get("/users/me/auth")
        async def get_me_auth(request: Request):
            _ensure_secure_when_required(request)
            session_id = request.cookies.get("session_id")
            if not session_id:
                raise HTTPException(status_code=401, detail="Not authenticated")
            user_id = self.active_session_ids.get(session_id)
            if user_id is None:
                raise HTTPException(status_code=401, detail="Not authenticated")
            user = self.vault.db.run_task(
                lambda session: session.get(User, user_id),
                priority=DBPriority.IMMEDIATE,
            )
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")
            return {
                "username": user.username,
                "has_password": bool(user.password_hash),
            }

        @self.api.post("/users/me/token")
        async def create_me_token(payload: CreateTokenRequest, request: Request):
            _ensure_secure_when_required(request)
            session_id = request.cookies.get("session_id")
            if not session_id:
                raise HTTPException(status_code=401, detail="Not authenticated")
            user_id = self.active_session_ids.get(session_id)
            if user_id is None:
                raise HTTPException(status_code=401, detail="Not authenticated")

            token_value = secrets.token_urlsafe(32)
            token_hash = bcrypt.hash(token_value)

            def create_token(
                session: Session,
                user_id: int,
                token_hash: str,
                description: Optional[str],
            ):
                user = session.get(User, user_id)
                if user is None:
                    raise HTTPException(status_code=404, detail="User not found")
                token = UserToken(
                    user_id=user_id,
                    token_hash=token_hash,
                    created_at=datetime.utcnow(),
                    description=description,
                )
                session.add(token)
                session.commit()
                session.refresh(token)
                return token

            token = self.vault.db.run_task(
                create_token,
                user_id,
                token_hash,
                payload.description,
                priority=DBPriority.IMMEDIATE,
            )

            return {
                "token": token_value,
                "token_id": token.id,
            }

        @self.api.get("/users/me/token")
        async def list_me_tokens(request: Request):
            _ensure_secure_when_required(request)
            session_id = request.cookies.get("session_id")
            if not session_id:
                raise HTTPException(status_code=401, detail="Not authenticated")
            user_id = self.active_session_ids.get(session_id)
            if user_id is None:
                raise HTTPException(status_code=401, detail="Not authenticated")

            def fetch_tokens(session: Session, user_id: int):
                tokens = session.exec(
                    select(UserToken)
                    .where(UserToken.user_id == user_id)
                    .order_by(UserToken.created_at.desc())
                ).all()
                return tokens

            tokens = self.vault.db.run_task(
                fetch_tokens, user_id, priority=DBPriority.IMMEDIATE
            )
            return [
                {
                    "id": token.id,
                    "description": token.description,
                    "created_at": token.created_at,
                    "last_used_at": token.last_used_at,
                }
                for token in tokens
            ]

        @self.api.delete("/users/me/token/{token_id}")
        async def delete_me_token(token_id: int, request: Request):
            _ensure_secure_when_required(request)
            session_id = request.cookies.get("session_id")
            if not session_id:
                raise HTTPException(status_code=401, detail="Not authenticated")
            user_id = self.active_session_ids.get(session_id)
            if user_id is None:
                raise HTTPException(status_code=401, detail="Not authenticated")

            def remove_token(session: Session, user_id: int, token_id: int):
                token = session.get(UserToken, token_id)
                if token is None or token.user_id != user_id:
                    raise HTTPException(status_code=404, detail="Token not found")
                session.delete(token)
                session.commit()
                return True

            self.vault.db.run_task(
                remove_token, user_id, token_id, priority=DBPriority.IMMEDIATE
            )

            return {"status": "success", "deleted_id": token_id}

        ###############################
        # Character endpoints         #
        ###############################
        @self.api.get("/characters/{id}/summary")
        async def get_characters_summary(id: str = None):
            """
            Return summary statistics for a single category:
            - If character_id is ALL: all pictures
            - If character_id is UNASSIGNED: unassigned pictures
            - If character_id is set: that character's pictures
            """
            start = time.time()
            # Determine which set to query
            if id == "ALL":
                # All
                metadata_fields = Picture.metadata_fields()
                pics = self.vault.db.run_immediate_read_task(
                    Picture.find, select_fields=metadata_fields
                )
                image_count = len(pics)
                logger.info("ALL pics count: {}".format(image_count))
                char_id = None
            elif id == "UNASSIGNED":
                # Unassigned
                def find_unassigned(session: Session):
                    pics = Picture.find(session, select_fields=["characters"])
                    return [pic for pic in pics if not pic.characters]

                pics = self.vault.db.run_immediate_read_task(find_unassigned)
                image_count = len(pics)
                logger.info("UNASSIGNED pics count: {}".format(image_count))
                char_id = None
            else:

                def find_assigned(session: Session, character_id: int):
                    faces = session.exec(
                        select(Face).filter(Face.character_id == character_id)
                    ).all()
                    return set(face.picture_id for face in faces)

                faces = self.vault.db.run_immediate_read_task(
                    find_assigned, character_id=int(id)
                )
                image_count = len(faces)
                char_id = int(id)

            # Thumbnail URL (reuse existing endpoint)
            if char_id:
                thumb_url = None
                if char_id not in (None, "", "null"):
                    thumb_url = f"/characters/{char_id}/thumbnail"

                # Ensure reference set exists for this character
                def find_reference_set(session: Session, character_id: int):
                    character = Character.find(
                        session,
                        id=character_id,
                        select_fields=["reference_picture_set_id"],
                    )
                    return (
                        character[0].reference_picture_set_id
                        if len(character) > 0
                        else None
                    )

                reference_set_id = self.vault.db.run_immediate_read_task(
                    find_reference_set, char_id
                )
            else:
                thumb_url = None
                reference_set_id = None

            summary = {
                "character_id": char_id,
                "image_count": image_count,
                "thumbnail_url": thumb_url,
                "reference_picture_set_id": reference_set_id,
            }
            elapsed = time.time() - start
            logger.info(f"Category summary computed in {elapsed:.4f} seconds")
            logger.info(f"Category summary: {summary}")
            return summary

        @self.api.patch("/characters/{id}")
        async def patch_character(id: int, request: Request):
            data = await request.json()
            name = data.get("name")
            description = data.get("description")
            char = None
            try:

                def alter_char(session: Session, id: int, name: str, description: str):
                    character = session.get(Character, id)
                    if character is None:
                        raise KeyError("Character not found")
                    updated = False
                    if name is not None and name != character.name:
                        character.name = name
                        updated = True
                    if description is not None and description != character.description:
                        character.description = description
                        updated = True
                    if updated:
                        session.add(character)

                        pictures = Picture.find(session, character_id=id)
                        for pic in pictures:
                            pic.description = None
                            pic.text_embedding = None
                            session.add(pic)

                        session.commit()
                    return character

                char = self.vault.db.run_task(
                    alter_char, id, name, description, priority=DBPriority.IMMEDIATE
                )
                self.vault.notify(EventType.CHANGED_CHARACTERS)

            except KeyError:
                raise HTTPException(status_code=404, detail="Character not found")

            return {"status": "success", "character": char}

        @self.api.delete("/characters/{id}")
        async def delete_character(id: int):
            # Delete the character
            try:

                def clear_character_and_nullify_faces(
                    session: Session, character_id: int
                ):
                    character = session.get(Character, character_id)
                    if character is None:
                        raise KeyError("Character not found")
                    # Nullify character_id on all faces linked to this character
                    faces = session.exec(
                        select(Face).where(Face.character_id == character_id)
                    ).all()
                    for face in faces:
                        face.character_id = None
                        session.add(face)
                    session.commit()
                    session.delete(character)
                    session.commit()

                self.vault.db.run_task(
                    clear_character_and_nullify_faces,
                    id,
                    priority=DBPriority.IMMEDIATE,
                )
                self.vault.notify(EventType.CHANGED_CHARACTERS)
                return {"status": "success", "deleted_id": id}
            except KeyError:
                raise HTTPException(status_code=404, detail="Character not found")

        @self.api.get("/characters/{id}")
        async def get_character_by_id(id: int):
            try:
                char = self.vault.db.run_immediate_read_task(
                    lambda session: Character.find(session, id=id)
                )
                return char[0] if char else None
            except KeyError:
                raise HTTPException(status_code=404, detail="Character not found")
            return char

        @self.api.get("/characters/{id}/{field}")
        async def get_character_field_by_id(id: int, field: str):
            if field == "thumbnail":
                # Find character and relationships
                char = self.vault.db.run_immediate_read_task(
                    Character.find,
                    select_fields=["reference_picture_set_id", "faces"],
                    id=id,
                )
                if not char:
                    raise HTTPException(status_code=404, detail="Character not found")
                char = char[0]
                # Try reference picture set first
                best_pic = None
                best_face = None

                def get_reference_set_and_members(session, reference_picture_set_id):
                    ref_set = (
                        session.get(PictureSet, reference_picture_set_id)
                        if reference_picture_set_id
                        else None
                    )
                    if ref_set:
                        session.refresh(ref_set)
                        members = list(ref_set.members)
                        return ref_set, members
                    return None, []

                ref_set, members = self.vault.db.run_immediate_read_task(
                    get_reference_set_and_members, char.reference_picture_set_id
                )
                if ref_set and ref_set.members:
                    # Query all pictures in the reference set
                    pics = sorted(
                        members,
                        key=lambda p: (p.score or 0),
                        reverse=True,
                    )
                    for pic in pics:
                        # Query faces for this picture
                        faces = self.vault.db.run_immediate_read_task(
                            Face.find, picture_id=pic.id
                        )
                        # Find face with character_id == char.id
                        for face in faces:
                            if face.character_id == char.id:
                                best_pic = pic
                                best_face = face
                                break
                        if best_pic and best_face:
                            logger.info("Found thumbnail from reference set!")
                            break
                # Fallback: use faces from char.faces, query their pictures
                if not best_pic or not best_face:
                    for face in char.faces:
                        # Query picture for this face
                        pic = self.vault.db.run_immediate_read_task(
                            Picture.find,
                            id=face.picture_id,
                            sort_field="score",
                        )
                        if pic:
                            best_pic = pic
                            best_face = face
                            break
                if not best_pic or not best_face:
                    raise HTTPException(
                        status_code=404, detail="No face thumbnail found for character"
                    )
                # Crop picture to face bbox and return as PNG
                from pixlvault.picture_utils import PictureUtils

                bbox = best_face.bbox

                if isinstance(best_pic, list):
                    best_pic = best_pic[0]

                picture_path = PictureUtils.resolve_picture_path(
                    self.vault.image_root, best_pic.file_path
                )
                crop = PictureUtils.crop_face_bbox_exact(picture_path, bbox)
                if crop is None:
                    raise HTTPException(
                        status_code=404, detail="Failed to crop face thumbnail"
                    )
                from io import BytesIO

                buf = BytesIO()
                crop.save(buf, format="PNG")
                return Response(content=buf.getvalue(), media_type="image/png")
            # Default: return field value
            try:
                char = self.vault.db.run_immediate_read_task(
                    Character.find, select_fields=[field], id=id
                )
                if not char:
                    raise KeyError("Character not found")
                char = char[0]
                logger.debug(
                    "Data type for Character field {}: {}".format(field, type(char))
                )
                if not hasattr(char, field):
                    raise HTTPException(
                        status_code=404, detail=f"Field {field} not found in Character"
                    )
                returnValue = {field: safe_model_dict(getattr(char, field))}
                logger.debug(
                    f"Returning character id={id} field={field} value={returnValue}"
                )
                return returnValue
            except KeyError:
                raise HTTPException(status_code=404, detail="Character not found")

        @self.api.get("/characters")
        async def get_characters(name: str = Query(None)):
            try:
                logger.info(f"Fetching characters with name: {name}")
                characters = self.vault.db.run_immediate_read_task(
                    lambda session: Character.find(session, name=name)
                )
                return characters
            except KeyError:
                logger.error("Character not found")
                raise HTTPException(status_code=404, detail="Character not found")
            except Exception as e:
                logger.error(f"Error fetching characters: {e}")
                raise HTTPException(status_code=500, detail="Internal Server Error")

        @self.api.post("/characters")
        async def create_character(payload: dict = Body(...)):
            from pixlvault.db_models import PictureSet

            try:

                def create_character_and_reference_set(session, payload):
                    character = Character(**payload)
                    session.add(character)
                    session.commit()
                    session.refresh(character)
                    logger.debug("Created character with ID: {}".format(character.id))
                    reference_set = PictureSet(
                        name="reference_pictures", description=str(character.name)
                    )
                    session.add(reference_set)
                    session.commit()
                    session.refresh(reference_set)
                    character.reference_picture_set_id = reference_set.id
                    session.add(character)
                    session.commit()
                    session.refresh(character)
                    return character.model_dump(exclude_unset=False)

                char_dict = self.vault.db.run_task(
                    create_character_and_reference_set,
                    payload,
                    priority=DBPriority.IMMEDIATE,
                )
                logger.debug("Created character: {}".format(char_dict))
                self.vault.notify(EventType.CHANGED_CHARACTERS)
                return {"status": "success", "character": char_dict}
            except Exception as e:
                logger.error(f"Error creating character: {e}")
                raise HTTPException(status_code=400, detail="Invalid character data")

        @self.api.post("/characters/{character_id}/faces")
        async def assign_face_to_character(
            character_id: int, payload: dict = Body(...)
        ):
            """Assigns faces to a character. Payload: { face_ids: list[int] } or { picture_ids: list[str] }"""
            face_ids = payload.get("face_ids")
            picture_ids = payload.get("picture_ids")
            if face_ids is not None and not isinstance(face_ids, list):
                raise HTTPException(status_code=400, detail="face_ids must be a list")
            if picture_ids is not None and not isinstance(picture_ids, list):
                raise HTTPException(
                    status_code=400, detail="picture_ids must be a list"
                )

            def assign_faces(
                session: Session,
                face_ids: list[int],
                picture_ids: list[str],
                character_id: int,
            ):
                faces_to_assign = []
                # If picture_ids are provided, find the largest face in each picture
                if picture_ids:
                    for pic_id in picture_ids:
                        faces = Face.find(session, picture_id=pic_id)
                        if not faces:
                            continue  # No faces in this picture

                        # Select the largest face by area (width * height)
                        def face_area(face):
                            try:
                                return (face.width or 0) * (face.height or 0)
                            except Exception:
                                return 0

                        largest_face = max(faces, key=face_area)
                        faces_to_assign.append(largest_face)
                # If face_ids are provided, add those faces
                if face_ids:
                    for face_id in face_ids:
                        face = session.get(Face, face_id)
                        if not face:
                            raise HTTPException(
                                status_code=404, detail=f"Face {face_id} not found"
                            )
                        faces_to_assign.append(face)
                # Remove duplicates
                unique_faces = {face.id: face for face in faces_to_assign}.values()
                for face in unique_faces:
                    face.character_id = character_id
                    session.add(face)
                session.commit()
                for face in unique_faces:
                    session.refresh(face)
                return list(unique_faces)

            faces = self.vault.db.run_task(
                assign_faces,
                face_ids,
                picture_ids,
                character_id,
                priority=DBPriority.IMMEDIATE,
            )
            self.vault.db.run_task(Picture.clear_field, picture_ids, "text_embedding")
            for face in faces:
                if face.character_id != character_id:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to set character {character_id} for face {face.id}",
                    )
            self.vault.notify(EventType.CHANGED_CHARACTERS)
            self.vault.notify(EventType.CHANGED_FACES)
            return {
                "status": "success",
                "face_ids": [face.id for face in faces],
                "character_id": character_id,
            }

        @self.api.delete("/characters/{character_id}/faces")
        async def remove_character_from_faces(
            character_id: int, payload: dict = Body(...)
        ):
            """Remove the character association from a specific face."""

            face_ids = payload.get("face_ids", None)
            picture_ids = payload.get("picture_ids", None)
            if not isinstance(face_ids, list) and not isinstance(picture_ids, list):
                raise HTTPException(
                    status_code=400,
                    detail="Must send a list of picture_ids or face_ids",
                )

            def remove_faces_from_character(
                session: Session,
                character_id: int,
                face_ids: list[int] = None,
                picture_ids: list[str] = None,
            ):
                faces = []
                if picture_ids:
                    for pic_id in picture_ids:
                        pic_faces = Face.find(session, picture_id=pic_id)
                        for face in pic_faces:
                            if face.character_id == character_id:
                                face.character_id = None
                                session.add(face)
                                faces.append(face)
                elif face_ids:
                    for face_id in face_ids:
                        face = session.get(Face, face_id)
                        if face and face.character_id == character_id:
                            face.character_id = None
                            session.add(face)
                session.commit()
                session.refresh(face)
                return faces

            self.vault.db.run_task(
                remove_faces_from_character,
                character_id,
                face_ids,
                picture_ids,
                priority=DBPriority.IMMEDIATE,
            )

            self.vault.db.run_task(Picture.clear_field, picture_ids, "text_embedding")
            self.vault.notify(EventType.CHANGED_CHARACTERS)
            self.vault.notify(EventType.CHANGED_FACES)
            return {
                "status": "success",
                "face_ids": face_ids,
                "character_id": character_id,
            }

        ##########################
        # Picture Sets Endpoints #
        ##########################
        @self.api.get("/picture_sets")
        async def get_picture_sets():
            """List all picture sets."""

            def fetch_sets(session):
                sets = session.exec(
                    select(PictureSet).options(
                        selectinload(PictureSet.reference_character)
                    )
                ).all()
                result = []
                for s in sets:
                    # Count members
                    members = session.exec(
                        select(PictureSetMember).where(PictureSetMember.set_id == s.id)
                    ).all()
                    count = len(members)
                    set_dict = safe_model_dict(s)
                    set_dict["picture_count"] = count
                    result.append(set_dict)
                return result

            result = safe_model_dict(self.vault.db.run_immediate_read_task(fetch_sets))
            logger.debug(f"Fetched picture set {result}")
            return result

        @self.api.post("/picture_sets")
        async def create_picture_set(payload: dict = Body(...)):
            """Create a new picture set."""
            from pixlvault.db_models import PictureSet

            name = payload.get("name")
            description = payload.get("description", "")
            if not name:
                raise HTTPException(status_code=400, detail="name is required")

            def create_set(session, name, description):
                picture_set = PictureSet(name=name, description=description)
                session.add(picture_set)
                session.commit()
                session.refresh(picture_set)
                return picture_set.dict()

            set_dict = self.vault.db.run_task(
                create_set, name, description, priority=DBPriority.IMMEDIATE
            )
            return {"status": "success", "picture_set": set_dict}

        @self.api.get("/picture_sets/{id}")
        async def get_picture_set(id: int, info: bool = Query(False)):
            """Get a picture set by id. Use ?info=true to get metadata only."""
            from pixlvault.db_models import PictureSet, PictureSetMember, Picture

            def fetch_set(session, id):
                picture_set = session.get(PictureSet, id)
                if not picture_set:
                    return None, None
                members = session.exec(
                    select(PictureSetMember).where(PictureSetMember.set_id == id)
                ).all()
                picture_ids = [m.picture_id for m in members]
                return picture_set, picture_ids

            picture_set, picture_ids = self.vault.db.run_immediate_read_task(
                fetch_set, id
            )
            if not picture_set:
                raise HTTPException(status_code=404, detail="Picture set not found")
            if info:
                set_dict = picture_set.dict()
                set_dict["picture_count"] = len(picture_ids)
                return set_dict

            # Return the full pictures data
            def fetch_pics(session, picture_ids):
                pics = session.exec(
                    select(Picture).where(Picture.id.in_(picture_ids))
                ).all()
                return [
                    pic.dict(exclude={"file_path", "thumbnail", "text_embedding"})
                    for pic in pics
                ]

            pictures = self.vault.db.run_immediate_read_task(fetch_pics, picture_ids)
            return {"pictures": pictures, "set": safe_model_dict(picture_set)}

        @self.api.patch("/picture_sets/{id}")
        async def update_picture_set(id: int, payload: dict = Body(...)):
            """Update a picture set's name and/or description"""
            from pixlvault.db_models import PictureSet

            name = payload.get("name")
            description = payload.get("description")

            def update_set(session, id, name, description):
                picture_set = session.get(PictureSet, id)
                if not picture_set:
                    return False
                if name is not None:
                    picture_set.name = name
                if description is not None:
                    picture_set.description = description

                session.commit()
                return True

            success = self.vault.db.run_task(
                update_set, id, name, description, priority=DBPriority.IMMEDIATE
            )
            if not success:
                raise HTTPException(status_code=404, detail="Picture set not found")
            return {"status": "success"}

        @self.api.delete("/picture_sets/{id}")
        async def delete_picture_set(id: int):
            """Delete a picture set and all its members."""
            from pixlvault.db_models import PictureSet, PictureSetMember

            def delete_set(session, id):
                picture_set = session.get(PictureSet, id)
                if not picture_set:
                    return False
                # Delete members
                members = session.exec(
                    select(PictureSetMember).where(PictureSetMember.set_id == id)
                ).all()
                for member in members:
                    session.delete(member)
                session.delete(picture_set)
                session.commit()
                return True

            success = self.vault.db.run_task(
                delete_set, id, priority=DBPriority.IMMEDIATE
            )
            if not success:
                raise HTTPException(status_code=404, detail="Picture set not found")
            return {"status": "success", "deleted_id": id}

        @self.api.get("/picture_sets/{id}/members")
        async def get_picture_set_pictures(id: int):
            """Get all picture ids in a set."""
            from pixlvault.db_models import PictureSet, PictureSetMember

            def fetch_members(session, id):
                picture_set = session.get(PictureSet, id)
                if not picture_set:
                    return None
                members = session.exec(
                    select(PictureSetMember).where(PictureSetMember.set_id == id)
                ).all()
                return [m.picture_id for m in members]

            picture_ids = self.vault.db.run_immediate_read_task(fetch_members, id)
            if picture_ids is None:
                raise HTTPException(status_code=404, detail="Picture set not found")
            return {"picture_ids": picture_ids}

        @self.api.post("/picture_sets/{id}/members/{picture_id}")
        async def add_picture_to_set(id: int, picture_id: str):
            """Add a picture to a set."""
            from pixlvault.db_models import PictureSet, PictureSetMember, Picture

            # Find reference_character_id if this is a reference set
            reference_character_id = self._find_reference_character_id_for_set(id)

            def add_member(session, id, picture_id, reference_character_id=None):
                picture_set = session.get(PictureSet, id)
                picture = session.get(Picture, picture_id)
                if not picture_set or not picture:
                    return False
                # Check if already exists
                exists = session.exec(
                    select(PictureSetMember).where(
                        PictureSetMember.set_id == id,
                        PictureSetMember.picture_id == picture_id,
                    )
                ).first()
                if exists:
                    return False
                member = PictureSetMember(set_id=id, picture_id=picture_id)
                session.add(member)
                session.add(picture_set)
                # If it is a reference set we need to remove all FaceCharacterLikeness entries for this character
                if reference_character_id is not None:
                    session.exec(
                        delete(FaceCharacterLikeness).where(
                            FaceCharacterLikeness.character_id == reference_character_id
                        )
                    )
                    logger.info(
                        "Deleted FaceCharacterLikeness entries for character {}".format(
                            reference_character_id
                        )
                    )

                session.commit()
                return True

            success = self.vault.db.run_task(
                add_member,
                id,
                picture_id,
                reference_character_id=reference_character_id,
                priority=DBPriority.IMMEDIATE,
            )
            if success:
                # Wake up FaceCharacterLikenessWorker to recompute likenesses for this character
                if reference_character_id is not None:
                    self.vault.notify(
                        EventType.CHANGED_CHARACTERS,
                    )

            else:
                raise HTTPException(
                    status_code=400,
                    detail="Failed to add picture to set (set may not exist or picture already in set)",
                )
            return {"status": "success"}

        @self.api.delete("/picture_sets/{id}/members/{picture_id}")
        async def remove_picture_from_set(id: int, picture_id: str):
            """Remove a picture from a set."""
            from pixlvault.db_models import PictureSetMember

            # Find reference_character_id if this is a reference set
            reference_character_id = self._find_reference_character_id_for_set(id)

            def remove_member(session, id, picture_id, reference_character_id=None):
                member = session.exec(
                    select(PictureSetMember).where(
                        PictureSetMember.set_id == id,
                        PictureSetMember.picture_id == picture_id,
                    )
                ).first()
                if not member:
                    return False
                session.delete(member)

                # If it is a reference set we need to remove all FaceCharacterLikeness entries for this character
                if reference_character_id is not None:
                    session.exec(
                        delete(FaceCharacterLikeness).where(
                            FaceCharacterLikeness.character_id == reference_character_id
                        )
                    )
                    logger.info(
                        "Deleted FaceCharacterLikeness entries for character {}".format(
                            reference_character_id
                        )
                    )

                session.commit()
                return True

            success = self.vault.db.run_task(
                remove_member,
                id,
                picture_id,
                reference_character_id=reference_character_id,
                priority=DBPriority.IMMEDIATE,
            )
            if success:
                # Wake up FaceCharacterLikenessWorker to recompute likenesses for this character
                if reference_character_id is not None:
                    self.vault.notify(
                        EventType.CHANGED_CHARACTERS,
                    )
            else:
                raise HTTPException(status_code=404, detail="Picture not in set")
            return {"status": "success"}

        ################################
        # Picture endpoints            #
        ################################
        @self.api.get("/pictures/stacks")
        async def get_picture_stacks(
            threshold: float = 0.0,
            min_group_size: int = 2,
            set_id: int = Query(None),
            character_id: str = Query(None),
            format: List[str] = Query(None),
        ):
            """
            Return pictures with stack_index assigned based on likeness clustering.
            Output matches /pictures endpoint plus stack_index for each image.
            """

            candidate_ids = None

            if set_id is not None:

                def fetch_set_ids(session, set_id):
                    members = session.exec(
                        select(PictureSetMember).where(
                            PictureSetMember.set_id == set_id
                        )
                    ).all()
                    return [m.picture_id for m in members]

                candidate_ids = set(
                    self.vault.db.run_immediate_read_task(fetch_set_ids, set_id)
                )
            elif character_id is not None:
                if character_id == "UNASSIGNED":

                    def fetch_unassigned_ids(session):
                        query = select(Picture.id)
                        unassigned_condition = ~exists(
                            select(Face.id).where(
                                Face.picture_id == Picture.id,
                                Face.character_id.is_not(None),
                            )
                        )
                        query = query.where(unassigned_condition)
                        return list(session.exec(query).all())

                    candidate_ids = set(
                        self.vault.db.run_immediate_read_task(fetch_unassigned_ids)
                    )
                elif character_id == "ALL" or character_id == "":
                    candidate_ids = None
                elif character_id.isdigit():

                    def fetch_character_ids(session, character_id):
                        faces = session.exec(
                            select(Face).where(Face.character_id == character_id)
                        ).all()
                        return list({face.picture_id for face in faces})

                    candidate_ids = set(
                        self.vault.db.run_immediate_read_task(
                            fetch_character_ids, int(character_id)
                        )
                    )

            if format:

                def fetch_format_ids(session, format):
                    rows = session.exec(
                        select(Picture.id).where(Picture.format.in_(format))
                    ).all()
                    return list(rows)

                format_ids = set(
                    self.vault.db.run_immediate_read_task(fetch_format_ids, format)
                )
                candidate_ids = (
                    format_ids if candidate_ids is None else candidate_ids & format_ids
                )

            def fetch_likeness(session):
                rows = session.exec(
                    select(PictureLikeness).where(PictureLikeness.likeness >= threshold)
                ).all()
                logger.info(
                    "Fetched %d picture likeness rows above threshold=%s",
                    len(rows),
                    threshold,
                )
                return rows

            rows = self.vault.db.run_immediate_read_task(fetch_likeness)

            neighbors = defaultdict(set)
            for row in rows:
                if candidate_ids is not None:
                    if (
                        row.picture_id_a not in candidate_ids
                        or row.picture_id_b not in candidate_ids
                    ):
                        continue
                neighbors[row.picture_id_a].add(row.picture_id_b)
                neighbors[row.picture_id_b].add(row.picture_id_a)

            # Find connected components (groups)
            visited = set()
            groups = []
            for node in neighbors:
                if node in visited:
                    continue
                stack = set()
                queue = deque([node])
                while queue:
                    n = queue.popleft()
                    if n in visited:
                        continue
                    visited.add(n)
                    stack.add(n)
                    for nbr in neighbors[n]:
                        if nbr not in visited:
                            queue.append(nbr)
                if len(stack) >= min_group_size:
                    groups.append(list(stack))

            groups = sorted(groups, key=min)
            stack_index_map = {}
            ordered_ids = []
            for idx, group in enumerate(groups):
                for pic_id in sorted(group):
                    stack_index_map[pic_id] = idx
                    ordered_ids.append(pic_id)

            if not ordered_ids:
                return []

            def fetch_pictures(session, ids):
                return Picture.find(
                    session,
                    id=ids,
                    select_fields=Picture.metadata_fields(),
                )

            ordered_pics = self.vault.db.run_immediate_read_task(
                fetch_pictures, ordered_ids
            )
            pics_by_id = {pic.id: pic for pic in ordered_pics}
            ordered_pics = [pics_by_id.get(pid) for pid in ordered_ids]
            ordered_pics = [pic for pic in ordered_pics if pic is not None]

            response = []
            for pic in ordered_pics:
                pic_dict = safe_model_dict(pic)
                pic_dict["stack_index"] = stack_index_map.get(pic.id)
                response.append(pic_dict)

            return response

        @self.api.post("/pictures/thumbnails")
        async def get_thumbnails(request: Request, payload: dict = Body(...)):
            ids = payload.get("ids", [])
            if not isinstance(ids, list):
                raise HTTPException(status_code=400, detail="'ids' must be a list")

            def map_bbox_to_thumbnail(bbox, picture):
                if not bbox or len(bbox) != 4:
                    return bbox, False
                left = getattr(picture, "thumbnail_left", None)
                top = getattr(picture, "thumbnail_top", None)
                side = getattr(picture, "thumbnail_side", None)
                if left is None or top is None or side in (None, 0):
                    return bbox, False
                try:
                    scale = 256.0 / float(side)
                    x1, y1, x2, y2 = bbox
                    x1 = max(0.0, min(256.0, (x1 - left) * scale))
                    y1 = max(0.0, min(256.0, (y1 - top) * scale))
                    x2 = max(0.0, min(256.0, (x2 - left) * scale))
                    y2 = max(0.0, min(256.0, (y2 - top) * scale))
                    return (
                        [
                            int(round(x1)),
                            int(round(y1)),
                            int(round(x2)),
                            int(round(y2)),
                        ],
                        True,
                    )
                except Exception:
                    return bbox, False

            # Fetch pictures and their faces
            pics = self.vault.db.run_task(
                lambda session: Picture.find(
                    session,
                    id=ids,
                    select_fields=[
                        "id",
                        "thumbnail",
                        "faces",
                        "thumbnail_left",
                        "thumbnail_top",
                        "thumbnail_side",
                    ],
                )
            )
            results = {}
            for pic in pics:
                try:
                    thumbnail_bytes = pic.thumbnail
                    # Gather face bboxes and ids
                    face_data = []
                    mapped_any = False
                    for face in getattr(pic, "faces", []):
                        # Defensive: ensure bbox is a list of 4 ints
                        bbox = None
                        try:
                            bbox = face.bbox if hasattr(face, "bbox") else None
                            if bbox and isinstance(bbox, str):
                                import ast

                                bbox = ast.literal_eval(bbox)
                        except Exception:
                            bbox = None
                        if bbox and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                            mapped_bbox, mapped = map_bbox_to_thumbnail(bbox, pic)
                            mapped_any = mapped_any or mapped
                            character = (
                                self.vault.db.run_task(
                                    lambda session: Character.find(
                                        session,
                                        id=face.character_id,
                                        select_fields=["name"],
                                    )
                                )
                                if face.character_id
                                else None
                            )
                            face_data.append(
                                {
                                    "id": face.id,
                                    "bbox": mapped_bbox,
                                    "character_id": face.character_id,
                                    "character_name": getattr(
                                        character[0], "name", None
                                    )
                                    if character
                                    else None,
                                    "frame_index": getattr(face, "frame_index", None),
                                }
                            )
                    results[pic.id] = {
                        "thumbnail": base64.b64encode(thumbnail_bytes).decode("utf-8")
                        if thumbnail_bytes
                        else None,
                        "faces": face_data,
                        "thumbnail_width": 256 if mapped_any else None,
                        "thumbnail_height": 256 if mapped_any else None,
                    }
                except Exception as exc:
                    logger.error(
                        f"Picture not found or error for id={pic.id} (thumbnail request): {exc}"
                    )
                    results[pic.id] = {"thumbnail": None, "faces": []}
            response = JSONResponse(results)
            origin = request.headers.get("origin")
            if origin and (
                origin in self.allow_origins
                or (
                    self.allow_origin_regex
                    and re.match(self.allow_origin_regex, origin)
                )
            ):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
            return response

        @self.api.get("/pictures/export")
        async def export_pictures_zip(
            request: Request,
            background_tasks: BackgroundTasks,
            query: str = Query(None),
            set_id: int = Query(None),
            threshold: float = Query(0.0),
            caption_mode: str = Query("description"),
            include_character_name: bool = Query(False),
        ):
            """
            Export pictures matching the filters as a zip file.
            Uses same filter logic as /pictures endpoint, but returns a task ID.
            """
            task_id = str(uuid.uuid4())
            self.export_tasks[task_id] = {
                "status": "in_progress",
                "file_path": None,
                "total": 0,
                "processed": 0,
                "filename": None,
            }

            def generate_zip():
                try:
                    caption_mode_normalized = (caption_mode or "description").lower()
                    if caption_mode_normalized not in {"none", "description", "tags"}:
                        caption_mode_normalized = "description"
                    include_character_name_enabled = (
                        bool(include_character_name)
                        and caption_mode_normalized != "none"
                    )

                    picture_ids = request.query_params.getlist("id")
                    query_params = dict(request.query_params)
                    query_params.pop("query", None)
                    query_params.pop("set_id", None)
                    query_params.pop("threshold", None)
                    query_params.pop("caption_mode", None)
                    query_params.pop("include_character_name", None)
                    character_id = query_params.pop("character_id", None)

                    select_fields = Picture.metadata_fields()
                    if (
                        caption_mode_normalized == "tags"
                        or include_character_name_enabled
                    ):
                        select_fields = select_fields | {"tags", "characters"}

                    pics = []

                    if picture_ids:
                        pics = self.vault.db.run_task(
                            Picture.find, id=picture_ids, select_fields=select_fields
                        )
                    elif set_id is not None:
                        logger.info("Exporting pictures set {} ".format(set_id))

                        def fetch_members(session, set_id):
                            members = session.exec(
                                select(PictureSetMember).where(
                                    PictureSetMember.set_id == set_id
                                )
                            ).all()
                            picture_ids = [m.picture_id for m in members]
                            if not picture_ids:
                                return []
                            return Picture.find(
                                session,
                                id=picture_ids,
                                select_fields=select_fields,
                            )

                        pics = self.vault.db.run_task(fetch_members, set_id)
                    elif character_id is not None:
                        logger.info(
                            "Exporting pictures for character ID: {}".format(
                                character_id
                            )
                        )

                        def fetch_by_character(session, character_id):
                            faces = session.exec(
                                select(Face).where(Face.character_id == character_id)
                            ).all()
                            picture_ids = list({face.picture_id for face in faces})
                            if not picture_ids:
                                return []
                            return Picture.find(
                                session,
                                id=picture_ids,
                                select_fields=select_fields,
                            )

                        pics = self.vault.db.run_task(fetch_by_character, character_id)
                    elif query:
                        logger.info(
                            "Exporting pictures using search query: {}".format(query)
                        )

                        def find_by_text(session, query):
                            words = re.findall(r"\b\w+\b", query.lower())
                            query_full = "A photo of " + query
                            return [
                                r[0]
                                for r in Picture.semantic_search(
                                    session,
                                    query_full,
                                    words,
                                    text_to_embedding=self.vault.generate_text_embedding,
                                    offset=0,
                                    limit=sys.maxsize,
                                    threshold=threshold,
                                    select_fields=select_fields,
                                )
                            ]

                        pics = self.vault.db.run_task(find_by_text, query)
                    else:
                        logger.info(
                            "Exporting pictures using filter parameters: {}".format(
                                query_params
                            )
                        )
                        pics = self.vault.db.run_task(
                            Picture.find,
                            offset=0,
                            limit=sys.maxsize,
                            select_fields=select_fields,
                            **query_params,
                        )

                    self.export_tasks[task_id]["total"] = len(pics)
                    self.export_tasks[task_id]["processed"] = 0

                    logger.info(
                        f"Export task {task_id}: {len(pics)} pictures to be added to the ZIP."
                    )

                    if not pics:
                        self.export_tasks[task_id]["status"] = "failed"
                        return

                    filename_parts = []
                    if set_id is not None:

                        def get_set(session, set_id):
                            return session.get(PictureSet, set_id)

                        picture_set = self.vault.db.run_task(get_set, set_id)
                        if picture_set:
                            filename_parts.append(picture_set.name.replace(" ", "_"))
                    if query:
                        filename_parts.append(f"search_{query[:20]}")

                    filename = (
                        "_".join(filename_parts) if filename_parts else "pictures"
                    )
                    filename = f"{filename}_{len(pics)}_images.zip"
                    self.export_tasks[task_id]["filename"] = filename

                    zip_path = os.path.join(
                        self.TEMP_EXPORT_DIR, f"export_{task_id}.zip"
                    )
                    with zipfile.ZipFile(
                        zip_path, "w", zipfile.ZIP_DEFLATED
                    ) as zip_file:
                        for idx, pic in enumerate(pics, start=1):
                            if (
                                hasattr(pic, "file_path")
                                and pic.file_path
                                and os.path.exists(
                                    PictureUtils.resolve_picture_path(
                                        self.vault.image_root, pic.file_path
                                    )
                                )
                            ):
                                full_path = PictureUtils.resolve_picture_path(
                                    self.vault.image_root, pic.file_path
                                )
                                ext = os.path.splitext(full_path)[1]
                                arcname = f"image_{idx:05d}{ext}"
                                zip_file.write(full_path, arcname=arcname)
                                caption_text = None
                                if caption_mode_normalized == "description":
                                    caption_text = pic.description or ""
                                elif caption_mode_normalized == "tags":
                                    tags = []
                                    for tag in getattr(pic, "tags", []) or []:
                                        tag_value = getattr(tag, "tag", None)
                                        if tag_value:
                                            tags.append(tag_value)
                                    caption_text = ", ".join(tags)

                                if include_character_name_enabled:
                                    character_names = []
                                    for character in (
                                        getattr(pic, "characters", []) or []
                                    ):
                                        name_value = getattr(character, "name", None)
                                        if name_value:
                                            character_names.append(name_value)
                                    if character_names:
                                        if caption_mode_normalized == "tags":
                                            prefix = ", ".join(character_names)
                                            if caption_text:
                                                caption_text = (
                                                    f"{prefix}, {caption_text}"
                                                )
                                            else:
                                                caption_text = prefix
                                        elif caption_mode_normalized == "description":
                                            prefix = "A picture of " + ", ".join(
                                                character_names
                                            )
                                            if caption_text:
                                                caption_text = (
                                                    f"{prefix}. {caption_text}"
                                                )
                                            else:
                                                caption_text = prefix

                                if (
                                    caption_mode_normalized != "none"
                                    and caption_text is not None
                                ):
                                    zip_file.writestr(
                                        f"image_{idx:05d}.txt", caption_text
                                    )
                                self.export_tasks[task_id]["processed"] += 1

                    zip_size = os.path.getsize(zip_path)
                    logger.info(
                        f"Export task {task_id}: ZIP file created with size {zip_size} bytes."
                    )

                    self.export_tasks[task_id]["status"] = "completed"
                    self.export_tasks[task_id]["file_path"] = zip_path
                except Exception as exc:
                    self.export_tasks[task_id]["status"] = "failed"
                    logger.error(f"Export task {task_id} failed: {exc}")

            background_tasks.add_task(generate_zip)
            return JSONResponse({"task_id": task_id})

        @self.api.get("/pictures/export/status")
        async def export_status(task_id: str):
            """Check the status of an export task."""
            task = self.export_tasks.get(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            total = task.get("total") or 0
            processed = task.get("processed") or 0
            progress = (processed / total * 100.0) if total else 0.0

            if task["status"] == "completed":
                return {
                    "status": "completed",
                    "download_url": f"/pictures/export/download/{task_id}",
                    "total": total,
                    "processed": processed,
                    "progress": progress,
                }

            return {
                "status": task["status"],
                "total": total,
                "processed": processed,
                "progress": progress,
            }

        @self.api.get("/pictures/export/download/{task_id}")
        async def download_export(task_id: str):
            """Download the completed ZIP file."""
            task = self.export_tasks.get(task_id)
            if not task or task["status"] != "completed":
                raise HTTPException(status_code=404, detail="File not ready")

            filename = task.get("filename") or os.path.basename(task["file_path"])
            return FileResponse(task["file_path"], filename=filename)

        @self.api.get("/pictures/search")
        async def search_pictures(
            request: Request,
            query: str,
            offset: int = Query(0),
            limit: int = Query(sys.maxsize),
            threshold: float = Query(0.5),
        ):
            query_params = {}
            format = None
            if request.query_params:
                query_params = dict(request.query_params)
                query = query_params.pop("query", query)
                offset = int(query_params.pop("offset", offset))
                limit = int(query_params.pop("limit", limit))
                format = request.query_params.getlist("format")
            if not query:
                raise HTTPException(
                    status_code=400, detail="Query parameter is required for search"
                )

            # Handle semantic search
            def find_by_text(session, query, offset, limit):
                # Use regex to extract words, removing punctuation
                words = re.findall(r"\b\w+\b", query.lower())
                # preprocessed_query_words = self.vault.preprocess_query_words(words)
                query = "A photo of " + query
                return Picture.semantic_search(
                    session,
                    query,
                    words,
                    text_to_embedding=self.vault.generate_text_embedding,
                    offset=offset,
                    limit=limit,
                    threshold=threshold,
                    format=format,
                    select_fields=Picture.metadata_fields(),
                )

            results = self.vault.db.run_task(find_by_text, query, offset, limit)
            # Each result is (pic, likeness_score)
            return [Picture.serialize_with_likeness(r) for r in results]

        @self.api.get("/pictures/{id}.{ext}")
        async def get_picture(request: Request, id: str, ext: str):
            if not isinstance(id, str):
                logger.error(f"Invalid id type: {type(id)} value: {id}")
                raise HTTPException(status_code=400, detail="Invalid picture id type")

            if not ext or not isinstance(ext, str):
                logger.error(f"Invalid extension type: {type(ext)} value: {ext}")
                raise HTTPException(status_code=400, detail="Invalid picture extension")
            id = int(id)  # Convert id to int

            pics = self.vault.db.run_task(lambda session: Picture.find(session, id=id))
            if not pics:
                logger.error(f"Picture not found for id={id}")
                raise HTTPException(status_code=404, detail="Picture not found")
            pic = pics[0]

            # Otherwise, deliver picture file as bytes
            file_path = PictureUtils.resolve_picture_path(
                self.vault.image_root, pic.file_path
            )
            if not file_path or not os.path.isfile(file_path):
                logger.error(
                    f"File path missing or does not exist for picture id={pic.id}, file_path={pic.file_path}"
                )
                raise HTTPException(
                    status_code=404, detail=f"File not found for picture id={pic.id}"
                )
            if pic.format.lower() != ext.lower():
                logger.error(
                    f"Requested extension '{ext}' does not match picture format '{pic.format}' for id={pic.id}"
                )
                raise HTTPException(
                    status_code=400,
                    detail="Requested extension does not match picture format",
                )

            # Return the image file with CORS headers
            response = FileResponse(file_path)
            try:
                stat = os.stat(file_path)
                etag = f'W/"{stat.st_size}-{int(stat.st_mtime)}"'
                response.headers["ETag"] = etag
                response.headers["Last-Modified"] = formatdate(
                    stat.st_mtime, usegmt=True
                )
                # Force revalidation without disabling caching completely
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
            except OSError:
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
            origin = request.headers.get("origin")
            if origin and (
                origin in self.allow_origins
                or (
                    self.allow_origin_regex
                    and re.match(self.allow_origin_regex, origin)
                )
            ):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
            return response

        @self.api.get("/pictures/{id}/metadata")
        async def get_picture_metadata(id: str):
            """Return all simple metadata for a picture"""
            metadata_fields = Picture.metadata_fields()
            pics = self.vault.db.run_task(
                Picture.find, id=id, select_fields=metadata_fields
            )
            if not pics:
                logger.error(f"Picture not found for id={id}")
                raise HTTPException(status_code=404, detail="Picture not found")
            pic = pics[0]

            pic_tags = self.vault.db.run_task(
                Picture.find, id=id, select_fields=["tags"]
            )
            pic_tags = pic_tags[0].tags if pic_tags else []
            pic_dict = safe_model_dict(pic)
            tags = []
            for tag in pic_tags:
                tags.append(tag.tag)

            pic_dict["tags"] = tags

            logger.info("Returning dict: " + str(pic_dict))
            return pic_dict

        @self.api.post("/pictures/{id}/tags")
        async def add_tag_to_picture(id: str, payload: dict = Body(...)):
            """
            Add a tag to a picture.
            """
            try:
                tag = payload.get("tag")
                if not tag:
                    raise HTTPException(status_code=400, detail="Tag is required")

                pic_list = self.vault.db.run_task(
                    lambda session: Picture.find(session, id=id, select_fields=["tags"])
                )
                if not pic_list:
                    raise HTTPException(status_code=404, detail="Picture not found")
                pic = pic_list[0]

                full_tag = Tag(tag=tag, picture_id=pic.id)
                if full_tag not in pic.tags:

                    def update_picture(session, pic_id, tag):
                        pic = Picture.find(session, id=pic_id, select_fields=["tags"])[
                            0
                        ]
                        pic.tags.append(tag)
                        session.add(pic)
                        session.commit()
                        session.refresh(pic)
                        return pic

                    self.vault.db.run_task(update_picture, pic.id, full_tag)
                    self.vault.notify(EventType.CHANGED_TAGS)

                return {"status": "success", "tags": pic.tags}
            except Exception as e:
                logger.error(f"Failed to add tag: {e}")
                raise HTTPException(status_code=500, detail="Failed to add tag")

        @self.api.delete("/pictures/{id}/tags/{tag}")
        async def remove_tag_from_picture(id: str, tag: str):
            """
            Remove a tag from a picture.
            """
            try:
                pic_list = self.vault.db.run_task(
                    lambda session: Picture.find(session, id=id, select_fields=["tags"])
                )
                logger.info(
                    f"Removing tag '{tag}' from picture id={id}. Found pics: {pic_list}"
                )
                if not pic_list:
                    raise HTTPException(status_code=404, detail="Picture not found")
                pic = pic_list[0]

                full_tag = Tag(tag=tag, picture_id=pic.id)

                if full_tag in pic.tags:
                    logger.info(
                        f"Tag {tag} found in picture tags {pic.tags}, proceeding to remove."
                    )

                    def update_picture(session, pic_id, tag):
                        pic = Picture.find(session, id=pic_id, select_fields=["tags"])[
                            0
                        ]
                        pic.tags.remove(tag)
                        session.add(pic)
                        session.commit()
                        session.refresh(pic)
                        return pic

                    self.vault.db.run_task(update_picture, pic.id, full_tag)
                    self.vault.notify(EventType.CHANGED_TAGS)

                    logger.info(f"Remaining tags after removal: {pic.tags}")
                else:
                    logger.info(
                        f"Tag {tag} not found in picture tags {pic.tags}, nothing to remove."
                    )

                return {"status": "success", "tags": pic.tags}
            except Exception as e:
                logger.error(f"Failed to remove tag: {e}")
                raise HTTPException(status_code=500, detail="Failed to remove tag")

        @self.api.get("/pictures/import/status")
        async def import_status(task_id: str):
            """Check the status of an import task."""
            task = self.import_tasks.get(task_id)
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            total = task.get("total") or 0
            processed = task.get("processed") or 0
            progress = (processed / total * 100.0) if total else 0.0

            payload = {
                "status": task["status"],
                "total": total,
                "processed": processed,
                "progress": progress,
            }
            if task["status"] == "completed":
                payload["results"] = task.get("results") or []
            if task["status"] == "failed":
                payload["error"] = task.get("error")
            return payload

        @self.api.get("/pictures/{id}/{field}")
        async def get_picture_field(id: str, field: str):
            """Return single field for a picture"""
            pics = self.vault.db.run_task(
                lambda session: Picture.find(session, id=id, select_fields=[field])
            )
            if not pics:
                logger.error(f"Picture not found for id={id}")
                raise HTTPException(status_code=404, detail="Picture not found")
            pic = pics[0]

            if field == "thumbnail":
                # Return as image, not JSON
                return Response(content=pic.thumbnail, media_type="image/png")
            elif field in Picture.large_binary_fields():
                # Return as bytes
                return {field: base64.b64encode(getattr(pic, field)).decode("utf-8")}
            else:
                return {field: safe_model_dict(getattr(pic, field))}

        @self.api.patch("/pictures/{id}")
        async def patch_picture(id: str, request: Request):
            """
            Update fields of a picture using query parameters, e.g., /pictures/{id}
            Also supports JSON body with fields to update, e.g., { "tags": ["tag1", "tag2"] }.
            """
            params = dict(request.query_params)

            logger.debug("Got a PATCH request for picture id={}".format(id))

            # If PATCH is called with a JSON body, use it
            content_type = request.headers.get("content-type", "")

            json_body = None
            if "application/json" in content_type:
                try:
                    json_body = await request.json()
                except Exception:
                    json_body = None

            try:
                pic_list = self.vault.db.run_task(
                    lambda session: Picture.find(session, id=id)
                )
                if not pic_list:
                    raise HTTPException(status_code=404, detail="Picture not found")
                pic = pic_list[0]
            except KeyError:
                raise HTTPException(status_code=404, detail="Picture not found")

            logger.debug(f"Updating picture id={id}")
            # If JSON body is provided, use it
            if json_body and isinstance(json_body, dict):
                params = json_body | params

            logger.debug(
                f"Updating picture id={id} with params: {params} and json_body: {json_body}"
            )
            updated = False
            # Update fields
            for key, value in params.items():
                # Instrument for debugging: log if value is bytes
                if isinstance(value, bytes):
                    logger.error(
                        f"PATCH attempted to set field '{key}' to bytes value: {value!r} (type={type(value)})"
                    )
                try:
                    cast_val = int(value)
                except Exception:
                    cast_val = value

                if hasattr(pic, key):
                    logger.debug(
                        f"Updating picture id={id} field={key} to value={cast_val} (type={type(cast_val)})"
                    )
                    # Assert metrics are not bytes before assignment
                    if key in [
                        "sharpness",
                        "edge_density",
                        "contrast",
                        "brightness",
                        "noise_level",
                    ]:
                        assert not isinstance(cast_val, bytes), (
                            f"PATCH attempted to set metric '{key}' to bytes for picture {id}: {cast_val!r}"
                        )
                    old_val = getattr(pic, key)
                    setattr(pic, key, cast_val)
                    # Drop embedding if character_id changes
                    if key == "character_id" and old_val != cast_val:
                        pic.description = None
                        pic.text_embedding = None
                    updated = True
            if updated:

                def update_picture(session, pic):
                    session.add(pic)
                    session.commit()
                    session.refresh(pic)
                    return pic

                result = self.vault.db.run_task(update_picture, pic)
                if result.id == id:
                    self.vault.notify(EventType.CHANGED_PICTURES)
            return {"status": "success", "picture": safe_model_dict(pic)}

        @self.api.post("/pictures/import")
        async def import_pictures(
            background_tasks: BackgroundTasks,
            file: List[UploadFile] = File(None),
        ):
            """
            Import new pictures. Accepts:
            - image: bytes upload (single file)
            Detects media type and sets ID as uuid + extension.
            """

            if not self.vault.is_worker_running(WorkerType.FACE):
                raise HTTPException(
                    status_code=503,
                    detail="Face extraction worker is not running. Cannot import pictures.",
                )

            dest_folder = self.vault.image_root
            logger.debug("Importing pictures to folder: " + str(dest_folder))
            os.makedirs(dest_folder, exist_ok=True)
            uploaded_files = []
            # Collect files to import
            if file is not None:
                for image in file:
                    img_bytes = await image.read()
                    # Try to get extension from UploadFile filename

                    ext = None
                    if image.filename:
                        ext = os.path.splitext(image.filename)[1]

                    if not ext:
                        # Guess from content type
                        ext = mimetypes.guess_extension(image.content_type or "")

                    # Detect extension if missing
                    if not ext or ext == "":
                        # Try to guess from bytes (fallback to .png)
                        import imghdr

                        img_type = imghdr.what(None, h=img_bytes)
                        if img_type:
                            ext = f".{img_type}"
                        else:
                            ext = ".png"
                    # Ensure ext starts with .
                    if not ext.startswith("."):
                        ext = "." + ext

                    uploaded_files.append((img_bytes, ext))
            else:
                logger.error("No files provided for import")
                raise HTTPException(status_code=400, detail="No image provided")

            task_id = str(uuid.uuid4())
            self.import_tasks[task_id] = {
                "status": "in_progress",
                "total": len(uploaded_files),
                "processed": 0,
                "results": None,
                "error": None,
            }

            def run_import_task():
                try:
                    shas, existing_map, new_pictures = self._create_picture_imports(
                        uploaded_files, dest_folder
                    )

                    logger.debug(
                        f"Importing {len(new_pictures)} new pictures out of {len(uploaded_files)} uploaded."
                    )

                    # Import all at once
                    if new_pictures:

                        def import_task(session):
                            session.add_all(new_pictures)
                            session.commit()
                            for pic in new_pictures:
                                session.refresh(pic)
                            return new_pictures

                        new_pictures = self.vault.db.run_task(import_task)
                        logger.debug(
                            f"Queuing likeness calculation for {len(new_pictures)} new pictures."
                        )
                    else:
                        logger.warning("No new pictures to import; all are duplicates.")
                        new_pictures = []

                    # Build results after DB import so picture_id is available
                    results = []
                    duplicate_count = 0
                    index = 0
                    for _, sha in zip(uploaded_files, shas):
                        if sha in existing_map:
                            pic = existing_map[sha]
                            results.append(
                                {
                                    "status": "duplicate",
                                    "picture_id": pic.id,
                                    "file": pic.file_path,
                                }
                            )
                            duplicate_count += 1
                        else:
                            pic = new_pictures[index]
                            results.append(
                                {
                                    "status": "success",
                                    "picture_id": pic.id,
                                    "file": pic.file_path,
                                }
                            )
                            index += 1

                    if duplicate_count:
                        logger.warning(
                            "Import completed with %d duplicate(s) out of %d file(s).",
                            duplicate_count,
                            len(uploaded_files),
                        )
                    self.import_tasks[task_id]["results"] = results
                    self.import_tasks[task_id]["processed"] = len(uploaded_files)
                    if new_pictures:
                        self.import_tasks[task_id]["status"] = "processing_faces"
                        face_futures = [
                            self.vault.get_worker_future(
                                WorkerType.FACE, Picture, pic.id, "faces"
                            )
                            for pic in new_pictures
                        ]
                        self.vault.notify(EventType.CHANGED_PICTURES)
                        face_timeout_s = 120
                        for pic, future in zip(new_pictures, face_futures):
                            try:
                                future.result(timeout=face_timeout_s)
                            except Exception as exc:
                                raise RuntimeError(
                                    f"Face extraction timed out for picture id={pic.id}"
                                ) from exc
                        self.import_tasks[task_id]["status"] = "completed"
                    else:
                        self.import_tasks[task_id]["status"] = "completed"
                        self.vault.notify(EventType.CHANGED_PICTURES)
                except Exception as exc:
                    self.import_tasks[task_id]["status"] = "failed"
                    self.import_tasks[task_id]["error"] = str(exc)
                    logger.error(f"Import task {task_id} failed: {exc}")

            background_tasks.add_task(run_import_task)
            return {"task_id": task_id}

        @self.api.delete("/pictures/{id}")
        async def delete_picture(id: str):
            """
            Delete a picture by id
            """

            def delete_pic(session, id):
                pic = session.get(Picture, id)
                if not pic:
                    return False
                file_path = PictureUtils.resolve_picture_path(
                    self.vault.image_root, pic.file_path
                )
                if not file_path or not os.path.isfile(file_path):
                    logger.error(
                        f"File path missing or does not exist for picture id={pic.id}, file_path={pic.file_path}"
                    )
                    session.delete(pic)
                    session.commit()
                    return True
                session.delete(pic)
                try:
                    os.remove(file_path)
                except Exception as e:
                    logger.error(f"Failed to delete picture file {file_path}: {e}")
                    session.rollback()
                    return False
                session.commit()
                return True

            success = self.vault.db.run_task(delete_pic, id)
            if not success:
                raise HTTPException(status_code=404, detail="Picture not found")
            return JSONResponse(
                content={"status": "success", "message": f"Picture id={id} deleted."}
            )

        @self.api.get("/pictures")
        async def list_pictures(
            request: Request,
            sort: str = Query(None),
            descending: bool = Query(True),
            offset: int = Query(0),
            limit: int = Query(sys.maxsize),
        ):
            metadata_fields = Picture.metadata_fields()

            def serialize_metadata(pic: Picture):
                return {field: getattr(pic, field) for field in metadata_fields}

            query_params = {}
            format = None
            if request.query_params:
                logger.info("Received query params: " + str(request.query_params))
                format = request.query_params.getlist("format")
                logger.info("Format param: " + str(format))
                query_params = dict(request.query_params)
                query_params.pop("format", None)
                sort = query_params.pop("sort", sort)
                desc_val = query_params.pop("descending", descending)
                if isinstance(desc_val, str):
                    descending = desc_val.lower() == "true"
                else:
                    descending = bool(desc_val)
                offset = int(query_params.pop("offset", offset))
                limit = int(query_params.pop("limit", limit))

            character_id = query_params.pop("character_id", None)
            reference_character_id = query_params.pop("reference_character_id", None)

            try:
                sort_mech = (
                    SortMechanism.from_string(sort, descending=descending)
                    if sort
                    else None
                )
            except ValueError as ve:
                logger.error(f"Invalid sort mechanism: {sort} - {ve}")
                raise HTTPException(status_code=400, detail=str(ve))

            if sort_mech and sort_mech.key == SortMechanism.Keys.CHARACTER_LIKENESS:
                if not reference_character_id:
                    raise HTTPException(
                        status_code=400,
                        detail="reference_character_id is required for CHARACTER_LIKENESS sort",
                    )
                return self._find_pictures_by_character_likeness(
                    character_id, reference_character_id, offset, limit, descending
                )

            if character_id == "UNASSIGNED":

                def find_unassigned(session: Session):
                    query = select(Picture)
                    unassigned_condition = ~exists(
                        select(Face.id).where(
                            Face.picture_id == Picture.id,
                            Face.character_id.is_not(None),
                        )
                    )
                    query = query.where(unassigned_condition)

                    if format:
                        query = query.where(Picture.format.in_(format))

                    select_fields = Picture.metadata_fields()
                    if select_fields:
                        select_fields = list(set(select_fields) | {"id"})
                        scalar_attrs = [
                            getattr(Picture, field)
                            for field in Picture.scalar_fields().intersection(
                                select_fields
                            )
                        ]
                        if scalar_attrs:
                            query = query.options(load_only(*scalar_attrs))
                        rel_attrs = [
                            getattr(Picture, field)
                            for field in Picture.relationship_fields().intersection(
                                select_fields
                            )
                        ]
                        for rel_attr in rel_attrs:
                            query = query.options(selectinload(rel_attr))

                    if sort_mech:
                        field = getattr(Picture, sort_mech.field, None)
                        if field is not None:
                            query = query.order_by(
                                field.desc() if sort_mech.descending else field.asc()
                            )

                    if offset > 0 or limit != sys.maxsize:
                        query = query.offset(offset).limit(limit)

                    return session.exec(query).all()

                pics = self.vault.db.run_task(find_unassigned)
                return [serialize_metadata(pic) for pic in pics]

            if character_id == "ALL":
                character_id = None

            if (
                character_id is not None
                and character_id != ""
                and character_id.isdigit()
            ):
                character_id = int(character_id)

            if character_id is not None and character_id != "":
                # Find all faces for this character
                def get_picture_ids_for_character(session, character_id):
                    faces = session.exec(
                        select(Face).where(Face.character_id == character_id)
                    ).all()
                    return list({face.picture_id for face in faces})

                picture_ids = self.vault.db.run_task(
                    get_picture_ids_for_character, character_id
                )
                if not picture_ids:
                    return []
                pics = self.vault.db.run_task(
                    Picture.find,
                    id=picture_ids,
                    sort_mech=sort_mech,
                    offset=offset,
                    limit=limit,
                    select_fields=Picture.metadata_fields(),
                    format=format,
                )
                return [serialize_metadata(pic) for pic in pics]
            else:
                pics = self.vault.db.run_task(
                    Picture.find,
                    sort_mech=sort_mech,
                    offset=offset,
                    limit=limit,
                    select_fields=Picture.metadata_fields(),
                    format=format,
                    **query_params,
                )
            return [serialize_metadata(pic) for pic in pics]

        @self.api.middleware("http")
        async def auth_middleware(request: Request, call_next):
            # Exclude specific routes from authentication
            excluded_paths = [
                "/login",
                "/docs",
                "/openapi.json",
                "/favicon.ico",
                "/",
                "/check-session",
                "/logout",
            ]
            if request.method == "OPTIONS":
                return await call_next(request)

            if request.url.path not in excluded_paths:
                session_id = request.cookies.get("session_id")
                logger.info(
                    f"Retrieved session_id from cookies: {session_id}"
                )  # Log for debugging
                logger.debug(f"Current active_session_ids: {self.active_session_ids}")
                if session_id not in self.active_session_ids:
                    logger.error(
                        f"Invalid session_id: {session_id}. It has expired and the client needs to log in again. When trying to access {request.url.path}"
                    )
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
                        status_code=401,
                        content={"detail": "Not authenticated"},
                        headers=headers,
                    )
            return await call_next(request)

        class LoginRequest(BaseModel):
            username: Optional[str] = Field(
                default=None,
                min_length=1,
                description="Username is required",
            )
            password: Optional[str] = Field(
                default=None,
                min_length=8,
                description="Password must be at least 8 characters long",
            )
            token: Optional[str] = Field(
                default=None,
                description="API token for authentication",
            )

        @self.api.get("/check-session")
        async def check_session(request: Request):
            session_id = request.cookies.get("session_id")
            if session_id and session_id in self.active_session_ids:
                return JSONResponse(content={"status": "success"})
            raise HTTPException(status_code=401, detail="Invalid session")

        @self.api.post("/login")
        def login(request: LoginRequest):
            if request.token:
                user = self._get_user()
                if not user:
                    raise HTTPException(status_code=404, detail="User not found")

                def fetch_tokens(session: Session, user_id: int):
                    tokens = session.exec(
                        select(UserToken).where(UserToken.user_id == user_id)
                    ).all()
                    return tokens

                tokens = self.vault.db.run_task(
                    fetch_tokens, user.id, priority=DBPriority.IMMEDIATE
                )
                matched_token = None
                for token in tokens:
                    if bcrypt.verify(request.token, token.token_hash):
                        matched_token = token
                        break
                if matched_token is None:
                    raise HTTPException(status_code=401, detail="Invalid token")

                def update_token_last_used(session: Session, token_id: int):
                    db_token = session.get(UserToken, token_id)
                    if db_token is None:
                        return None
                    db_token.last_used_at = datetime.utcnow()
                    session.add(db_token)
                    session.commit()
                    return db_token

                self.vault.db.run_task(
                    update_token_last_used,
                    matched_token.id,
                    priority=DBPriority.IMMEDIATE,
                )

                response = JSONResponse(content={"message": "Login successful."})
            else:
                if not request.username or not request.password:
                    raise HTTPException(
                        status_code=400,
                        detail="Username and password are required",
                    )

                user = self._get_user() or self._ensure_user()
                if not user.username or not user.password_hash:
                    # First login: set the username and password
                    hashed_password = bcrypt.hash(request.password)

                    def set_credentials(session: Session):
                        db_user = session.exec(select(User)).first()
                        if db_user is None:
                            db_user = User()
                        db_user.username = request.username
                        db_user.password_hash = hashed_password
                        session.add(db_user)
                        session.commit()
                        session.refresh(db_user)
                        return db_user

                    user = self.vault.db.run_task(
                        set_credentials, priority=DBPriority.IMMEDIATE
                    )
                    self._user = user
                    self.USERNAME = user.username
                    self.PASSWORD_HASH = user.password_hash
                    response = JSONResponse(
                        content={"message": "Username and password set successfully."}
                    )
                else:
                    # Validate the username and password
                    if request.username != user.username:
                        raise HTTPException(status_code=401, detail="Invalid username")
                    if not bcrypt.verify(request.password, user.password_hash):
                        raise HTTPException(status_code=401, detail="Invalid password")
                    response = JSONResponse(content={"message": "Login successful."})

            # Generate a new session ID using uuid
            session_id = str(uuid.uuid4())
            if not user or user.id is None:
                raise HTTPException(status_code=500, detail="User not found")
            self.active_session_ids[session_id] = user.id

            # Set the session ID as a cookie
            cookie_samesite = self._server_config.get("cookie_samesite", "Lax")
            cookie_secure = self._server_config.get("cookie_secure", False)
            if cookie_samesite == "None" and not cookie_secure:
                logger.warning(
                    "cookie_samesite=None requires cookie_secure=True for cross-site cookies to work in browsers."
                )
            response.set_cookie(
                key="session_id",
                value=session_id,
                httponly=True,
                samesite=cookie_samesite,
                secure=bool(cookie_secure),
            )
            return response

        @self.api.get("/login")
        def check_registration():
            user = self._get_user()
            if not user or not user.username or not user.password_hash:
                return JSONResponse(content={"needs_registration": True})
            return JSONResponse(content={"needs_registration": False})

        @self.api.post("/logout")
        def logout(response: Response, request: Request):
            session_id = request.cookies.get("session_id")
            if session_id in self.active_session_ids:
                self.active_session_ids.pop(session_id, None)
                logger.info(f"Session {session_id} invalidated.")
            response.delete_cookie("session_id", path="/")
            return {"message": "Logged out successfully."}

        @self.api.get("/protected")
        async def protected():
            return {"message": "You are authenticated!"}
