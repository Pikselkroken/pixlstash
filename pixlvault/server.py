import base64
import gc
import uvicorn
import io
import os
import json
import re
import uuid
import mimetypes
import concurrent.futures
import sys
import time
import zipfile

from collections import defaultdict, deque
from sqlalchemy.orm import selectinload
from sqlmodel import Session, delete, select

from contextlib import asynccontextmanager
from fastapi import Body, FastAPI, File, Request, UploadFile, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pillow_heif import register_heif_opener
from typing import List

from pixlvault.db_models import (
    Character,
    Face,
    Conversation,
    Message,
    Picture,
    PictureSet,
    PictureSetMember,
    SortMechanism,
)

from pixlvault.db_models import PictureLikeness, FaceLikeness
from pixlvault.db_models.face_character_likeness import FaceCharacterLikeness
from pixlvault.picture_stack_utils import (
    order_stack_pictures,
    combined_picture_face_likeness,
)
from pixlvault.database import DBPriority
from pixlvault.event_types import EventType
from pixlvault.utils import safe_model_dict
from pixlvault.picture_utils import PictureUtils
from pixlvault.pixl_logging import get_logger, uvicorn_log_config
from pixlvault.vault import Vault

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
            + str(self._config["selected_image_root"])
        )

        register_heif_opener()

        self.vault = Vault(
            image_root=self._config["selected_image_root"],
            description=self._config.get("description"),
        )

        self.api = FastAPI(lifespan=self.lifespan)
        # Enable CORS for frontend dev server
        self.api.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Or restrict to ["http://localhost:5173"]
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self._add_cors_exception_handler()
        self._setup_routes()

    def __enter__(self):
        # Allow use as a context manager for robust cleanup
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, "vault"):
            logger.warning("Closing the vault and cleaning up resources")
            self.vault.close()
        gc.collect()

    def run(self):
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
        if hasattr(self, "vault"):
            self.vault.close()

    @staticmethod
    def create_config(**kwargs):
        """
        Create a config dict from provided keys in kwargs, using defaults for missing keys.
        """
        config_dir = kwargs.get("config_dir")
        if not config_dir:
            config_dir = os.path.expanduser("~/.pixlvault")
        default_image_root = os.path.join(config_dir, "images")
        defaults = {
            "image_roots": [default_image_root],
            "selected_image_root": default_image_root,
            "description": DEFAULT_DESCRIPTION,
            "sort": SortMechanism.Keys.DATE.name,
            "descending": True,
            "thumbnail": "default",
            "thumbnail_size": "default",
            "show_stars": True,
            "openai_host": "localhost",
            "openai_port": 8000,
            "openai_model": "gpt-3.5-turbo",
            "default_device": "cpu",
        }
        config = defaults.copy()
        config.update({k: v for k, v in kwargs.items() if v is not None})
        # Ensure image_roots and selected_image_root are valid
        if not config.get("image_roots") or len(config["image_roots"]) == 0:
            config["image_roots"] = [default_image_root]
        if not config.get("selected_image_root"):
            config["selected_image_root"] = config["image_roots"][0]
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
        # Ensure image_roots and selected_image_root are valid
        if not config.get("image_roots") or len(config["image_roots"]) == 0:
            config["image_roots"] = [os.path.join(config_dir, "images")]
        if not config.get("selected_image_root"):
            config["selected_image_root"] = config["image_roots"][0]
        return config

    @staticmethod
    def _init_server_config(server_config_path):
        config_dir = os.path.dirname(server_config_path)
        os.makedirs(config_dir, exist_ok=True)

        default_log_path = os.path.join(config_dir, "server.log")
        default_ssl_cert_path = os.path.join(config_dir, "ssl", "cert.pem")
        default_ssl_key_path = os.path.join(config_dir, "ssl", "key.pem")

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
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers={"Access-Control-Allow-Origin": "*"},
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
                if reference_character_id in character_ids:
                    # Skip pictures that already have the reference character assigned
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
        # Chat endpoints              #
        ###############################
        @self.api.delete("/conversations/{id}")
        async def delete_conversation(id: int):
            """Delete a conversation and all messages."""

            def delete_query(session, id: int):
                conversation = session.get(Conversation, id)
                session.delete(conversation)
                session.commit()

            future = self.vault.db.submit_task(delete_query, id)
            if future.exception():
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to delete conversation {id}",
                )

            return {"status": "ok"}

        @self.api.get("/conversations/{id}")
        async def get_conversation(id: int, limit: int = 100):
            """Return conversation and its messages."""
            future = self.vault.db.submit_task(
                lambda session: session.get(Conversation, id)
            )
            if future.exception():
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to load conversation {id}",
                )
            conversation = future.result()
            if conversation is None:
                raise HTTPException(
                    status_code=404, detail=f"Conversation {id} not found"
                )
            future = self.vault.db.submit_task(
                lambda session: session.exec(
                    select(Message)
                    .where(Message.conversation_id == id)
                    .order_by(Message.timestamp.asc())
                    .limit(limit)
                ).all()
            )
            if future.exception():
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to load chat messages for conversation {id}",
                )

            messages = future.result()
            return {"conversation": conversation, "messages": messages}

        @self.api.get("/conversations")
        async def list_conversations():
            """Return list of conversations."""
            future = self.vault.db.submit_task(
                lambda session: session.exec(select(Conversation)).all()
            )
            if future.exception():
                raise HTTPException(
                    status_code=500,
                    detail="Failed to load conversations",
                )
            conversations = future.result()
            return {"conversations": conversations}

        @self.api.post("/conversations")
        async def create_conversation(
            character_id: int = Query(None),
            description: str = Query("Chat with this character"),
        ):
            """Create a new chat session for a character. Returns conversation_id."""
            if character_id is None:
                raise HTTPException(status_code=400, detail="character_id is required")

            def create_conversation(session: Session, character_id: int):
                conversation = Conversation(
                    character_id=character_id, description=description
                )
                session.add(conversation)
                session.commit()
                session.refresh(conversation)
                return conversation

            future = self.vault.db.submit_task(create_conversation, character_id)
            if future.exception():
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to create chat session. Exception occurred: {future.exception()}",
                )
            conversation = future.result()
            if conversation is None:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to create chat session. The resulting conversation is None.",
                )

            logger.info(
                "Created new conversation with ID: {}. Now trying to load it again.".format(
                    conversation.id
                )
            )

            future = self.vault.db.submit_task(
                lambda session: session.get(Conversation, conversation.id)
            )
            if future.exception():
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to load created chat session due to exception {future.exception()}.",
                )
            return {"conversation_id": conversation.id}

        @self.api.post("/conversations/message")
        async def post_conversation_message(payload: dict):
            """Save a chat message. Expects conversation_id, role, content, picture_id (optional)."""
            required = ["conversation_id", "role", "content"]
            for key in required:
                if key not in payload:
                    return JSONResponse(
                        status_code=400,
                        content={"error": f"Missing required field: {key}"},
                    )
            logger.info(
                f"[Chat] Saving message: conversation_id={payload.get('conversation_id')}, role={payload.get('role')}, picture_id={payload.get('picture_id')}"
            )

            def save_message(session: Session, message: str):
                session.add(message)
                session.commit()

            message = Message(
                conversation_id=payload["conversation_id"],
                role=payload["role"],
                content=payload["content"],
                picture_id=payload.get("picture_id"),
            )
            future = self.vault.db.submit_task(save_message, message)
            if future.exception():
                raise HTTPException(status_code=500, detail="Failed to save message")
            return {"status": "ok"}

        ###############################
        # Config endpoints            #
        ###############################
        @self.api.get("/config")
        async def get_config():
            """
            Return the current image roots config (config.json) and OpenAI chat service config.
            """
            logger.debug(f"Transmitting current config {self._config}")
            return self._config

        @self.api.patch("/config")
        async def patch_config(request: Request):
            import time

            start_time = time.time()
            logger.info(f"[TIMING] PATCH /config called at {start_time:.3f}")
            """
            Update existing config values or append to existing lists. Does not allow adding new keys.
            Body: { key: value, ... } (value replaces or is appended to existing key)
            If the value is a list and the existing value is a list, appends items.
            Ensures new image root directories and DBs are created as needed.
            """
            patch_data = await request.json()
            updated = False
            image_root_changed = False
            for key, value in patch_data.items():
                logger.info(f"Updating config key '{key}' with value: {value}")
                if key not in self._config:
                    # Allow adding 'sort', 'thumbnail', 'show_stars' keys if missing
                    if key in (
                        "sort",
                        "descending",
                        "thumbnail",
                        "show_stars",
                        "likeness_threshold",
                        "openai_host",
                        "openai_port",
                        "openai_model",
                        "default_device",
                    ):
                        self._config[key] = value
                        updated = True
                        continue
                    raise HTTPException(
                        status_code=400, detail=f"Key '{key}' does not exist in config."
                    )
                if key == "image_roots" and isinstance(value, list):
                    # Ensure all image root directories exist
                    for v in value:
                        if not os.path.exists(v):
                            os.makedirs(v, exist_ok=True)
                if (
                    key == "selected_image_root"
                    and self._config.get("selected_image_root") != value
                ):
                    image_root_changed = True
                if isinstance(self._config[key], list) and isinstance(value, list):
                    # Append unique items
                    for v in value:
                        if v not in self._config[key]:
                            self._config[key].append(v)
                            updated = True
                else:
                    # Replace value
                    if self._config[key] != value:
                        self._config[key] = value
                        updated = True
            if updated:
                # Save config
                config_path = self._config_path
                with open(config_path, "w") as f:
                    json.dump(self._config, f, indent=2)
            # If selected_image_root changed, re-initialize vault with new root
            if image_root_changed:
                new_root = self._config["selected_image_root"]
                if not os.path.exists(new_root):
                    os.makedirs(new_root, exist_ok=True)
                # Re-initialize vault (and DB) with new root
                self.vault = Vault(
                    image_root=new_root,
                    description=self._config.get("description"),
                )
            elapsed = time.time() - start_time
            logger.info(f"[TIMING] PATCH /config completed in {elapsed:.3f} seconds")
            return {"status": "success", "updated": updated, "config": self._config}

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

                crop = PictureUtils.crop_face_bbox_exact(best_pic.file_path, bbox)
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
                characters = self.vault.db.run_immediate_read_task(
                    lambda session: Character.find(session, name=name)
                )
                return characters
            except KeyError:
                raise HTTPException(status_code=404, detail="Character not found")

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
        async def get_picture_stacks(threshold: float = 0.0, min_group_size: int = 2):
            """
            Return groups (stacks) of near-identical pictures based on likeness threshold.
            Each stack contains picture dicts and preselection info.
            """

            def fetch_likeness(session):
                # Query all likeness pairs above threshold
                rows = session.exec(select(PictureLikeness)).all()
                logger.info(f"Fetched {len(rows)} picture likeness rows from DB.")
                return {
                    (row.picture_id_a, row.picture_id_b): row.likeness for row in rows
                }

            def fetch_face_likeness(session):
                # Fetch all face likeness scores
                rows = session.exec(select(FaceLikeness)).all()
                logger.info(f"Fetched {len(rows)} face likeness rows from DB.")
                return {(row.face_id_a, row.face_id_b): row.likeness for row in rows}

            picture_likeness_map = self.vault.db.run_immediate_read_task(fetch_likeness)
            logger.info(
                f"Picture likeness map keys: {list(picture_likeness_map.keys())[:10]} (showing up to 10)"
            )
            face_likeness_map = self.vault.db.run_immediate_read_task(
                fetch_face_likeness
            )
            logger.info(
                f"Face likeness map keys: {list(face_likeness_map.keys())[:10]} (showing up to 10)"
            )

            neighbors = defaultdict(set)
            for picture_id_a, picture_id_b in picture_likeness_map.keys():
                neighbors[picture_id_a].add(picture_id_b)
                neighbors[picture_id_b].add(picture_id_a)

            logger.info(
                f"Neighbors dict: {dict(list(neighbors.items())[:5])} (showing up to 5)"
            )

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

            logger.info(
                f"Found {len(groups)} groups (stacks) with min_group_size={min_group_size}."
            )
            if groups:
                logger.info(f"First group: {groups[0]}")

            # For each group, fetch picture info and select best
            def fetch_pictures(session, ids):
                return Picture.find(
                    session,
                    id=ids,
                    select_fields=["faces", "quality", "height", "width"],
                )

            def face_likeness_lookup(face_a, face_b):
                # Try both (a, b) and (b, a) since order may vary
                return face_likeness_map.get(
                    (face_a.id, face_b.id)
                ) or face_likeness_map.get((face_b.id, face_a.id))

            stacks = []
            for group in groups:
                pics = self.vault.db.run_task(fetch_pictures, group)
                logger.info(f"Fetched {len(pics)} pictures for group: {group}")
                # Compute combined likeness for all pairs in group
                likeness_matrix = {}
                for i, pic_a in enumerate(pics):
                    for j, pic_b in enumerate(pics):
                        if i < j:
                            score = combined_picture_face_likeness(
                                pic_a, pic_b, face_likeness_lookup
                            )
                            logger.info(
                                f"Combined likeness for ({pic_a.id}, {pic_b.id}): {score}"
                            )
                            likeness_matrix[(pic_a.id, pic_b.id)] = score
                # Convert tuple keys to string keys for JSON compatibility
                likeness_matrix_json = {
                    f"{k[0]}|{k[1]}": v for k, v in likeness_matrix.items()
                }
                logger.info(f"Likeness matrix for group: {likeness_matrix_json}")
                ordered = order_stack_pictures(pics)
                stacks.append(
                    {
                        "pictures": [safe_model_dict(pic) for pic in ordered],
                        "likeness_matrix": likeness_matrix_json,
                    }
                )

            return {
                "stacks": stacks,
                "threshold": threshold,
                "min_group_size": min_group_size,
            }

        @self.api.post("/pictures/thumbnails")
        async def get_thumbnails(payload: dict = Body(...)):
            ids = payload.get("ids", [])
            if not isinstance(ids, list):
                raise HTTPException(status_code=400, detail="'ids' must be a list")

            # Fetch pictures and their faces
            pics = self.vault.db.run_task(
                lambda session: Picture.find(
                    session, id=ids, select_fields=["id", "thumbnail", "faces"]
                )
            )
            results = {}
            for pic in pics:
                try:
                    thumbnail_bytes = pic.thumbnail
                    # Gather face bboxes and ids
                    face_data = []
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
                                    "bbox": bbox,
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
                    }
                except Exception as exc:
                    logger.error(
                        f"Picture not found or error for id={pic.id} (thumbnail request): {exc}"
                    )
                    results[pic.id] = {"thumbnail": None, "faces": []}
            response = JSONResponse(results)
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response

        @self.api.get("/pictures/export")
        async def export_pictures_zip(
            request: Request,
            query: str = Query(None),
            set_id: int = Query(None),
            threshold: float = Query(0.0),
        ):
            """
            Export pictures matching the filters as a zip file.
            Uses same filter logic as /pictures endpoint, but returns a zip.
            """
            query_params = dict(request.query_params)
            query_params.pop("query", None)
            query_params.pop("set_id", None)
            query_params.pop("threshold", None)

            pics = []
            if set_id is not None:
                # Fetch picture IDs in set, then fetch Picture objects
                def fetch_members(session, set_id):
                    members = session.exec(
                        select(PictureSetMember).where(
                            PictureSetMember.set_id == set_id
                        )
                    ).all()
                    picture_ids = [m.picture_id for m in members]
                    if not picture_ids:
                        return []
                    return Picture.find(session, id=picture_ids)

                pics = self.vault.db.run_task(fetch_members, set_id)
            elif query:

                def find_by_text(session, query):
                    words = re.findall(r"\b\w+\b", query.lower())
                    preprocessed_query_words = self.vault.preprocess_query_words(words)
                    query_full = "A photo of " + query
                    return [
                        r[0]
                        for r in Picture.semantic_search(
                            session,
                            query_full,
                            preprocessed_query_words,
                            text_to_embedding=self.vault.generate_text_embedding,
                            offset=0,
                            limit=sys.maxsize,
                            threshold=threshold,
                            select_fields=Picture.metadata_fields(),
                        )
                    ]

                pics = self.vault.db.run_task(find_by_text, query)
            else:
                # Fallback to filter-based search
                pics = self.vault.db.run_task(
                    Picture.find,
                    offset=0,
                    limit=sys.maxsize,
                    select_fields=Picture.metadata_fields(),
                    **query_params,
                )

            # Create zip file in memory
            zip_buffer = io.BytesIO()
            char_groups = defaultdict(list)
            for pic in pics:
                char_groups["image"].append(pic)

            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for char_name, group in char_groups.items():
                    for idx, pic in enumerate(group, start=1):
                        if (
                            hasattr(pic, "file_path")
                            and pic.file_path
                            and os.path.exists(pic.file_path)
                        ):
                            ext = os.path.splitext(pic.file_path)[1]
                            arcname = f"{char_name}_{idx:05d}{ext}"
                            zip_file.write(pic.file_path, arcname=arcname)

            zip_buffer.seek(0)

            # Generate filename based on filters
            filename_parts = []
            if set_id is not None:

                def get_set(session, set_id):
                    return session.get(PictureSet, set_id)

                picture_set = self.vault.db.run_task(get_set, set_id)
                if picture_set:
                    filename_parts.append(picture_set.name.replace(" ", "_"))
            if query:
                filename_parts.append(f"search_{query[:20]}")

            filename = "_".join(filename_parts) if filename_parts else "pictures"
            filename = f"{filename}_{len(pics)}_images.zip"

            return StreamingResponse(
                io.BytesIO(zip_buffer.getvalue()),
                media_type="application/zip",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )

        @self.api.get("/pictures/search")
        async def search_pictures(
            request: Request,
            query: str,
            offset: int = Query(0),
            limit: int = Query(sys.maxsize),
            threshold: float = Query(0.5),
        ):
            query_params = {}
            if request.query_params:
                query_params = dict(request.query_params)
                query = query_params.pop("query", query)
                offset = int(query_params.pop("offset", offset))
                limit = int(query_params.pop("limit", limit))
            if not query:
                raise HTTPException(
                    status_code=400, detail="Query parameter is required for search"
                )

            # Handle semantic search
            def find_by_text(session, query, offset, limit):
                # Use regex to extract words, removing punctuation
                words = re.findall(r"\b\w+\b", query.lower())
                preprocessed_query_words = self.vault.preprocess_query_words(words)
                query = "A photo of " + query
                return Picture.semantic_search(
                    session,
                    query,
                    preprocessed_query_words,
                    text_to_embedding=self.vault.generate_text_embedding,
                    offset=offset,
                    limit=limit,
                    threshold=threshold,
                    select_fields=Picture.metadata_fields(),
                )

            results = self.vault.db.run_task(find_by_text, query, offset, limit)
            # Each result is (pic, likeness_score)
            return [Picture.serialize_with_likeness(r) for r in results]

        @self.api.get("/pictures/{id}")
        async def get_picture(id: str):
            if not isinstance(id, str):
                logger.error(f"Invalid id type: {type(id)} value: {id}")
                raise HTTPException(status_code=400, detail="Invalid picture id type")

            id = int(id)  # Convert id to int

            pics = self.vault.db.run_task(lambda session: Picture.find(session, id=id))
            if not pics:
                logger.error(f"Picture not found for id={id}")
                raise HTTPException(status_code=404, detail="Picture not found")
            pic = pics[0]

            # Otherwise, deliver picture file as bytes
            if not pic.file_path or not os.path.isfile(pic.file_path):
                logger.error(
                    f"File path missing or does not exist for picture id={pic.id}, file_path={pic.file_path}"
                )
                raise HTTPException(
                    status_code=404, detail=f"File not found for picture id={pic.id}"
                )

            # Return the image file with CORS headers
            response = FileResponse(pic.file_path)
            response.headers["Access-Control-Allow-Origin"] = "*"
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

        @self.api.post("/pictures")
        async def import_pictures(
            file: List[UploadFile] = File(None),
        ):
            """
            Import new pictures. Accepts:
            - image: bytes upload (single file)
            Detects media type and sets ID as uuid + extension.
            """

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
                logger.error("No new pictures to import; all are duplicates.")
                raise HTTPException(
                    status_code=400, detail="All pictures are duplicates"
                )

            # Build results after DB import so picture_id is available
            results = []
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

            self.vault.notify(EventType.CHANGED_PICTURES)
            return {"results": results}

        @self.api.delete("/pictures/{id}")
        async def delete_picture(id: str):
            """
            Delete a picture by id
            """

            def delete_pic(session, id):
                pic = session.get(Picture, id)
                if not pic:
                    return False
                file_path = pic.file_path
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
            return {
                "status": "success",
            }

        @self.api.get("/pictures")
        async def list_pictures(
            request: Request,
            sort: str = Query(None),
            descending: bool = Query(True),
            offset: int = Query(0),
            limit: int = Query(sys.maxsize),
        ):
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

            logger.warning("SORTING ORDER: " + str(sort) + " DESC: " + str(descending))
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
                    pics = Picture.find(
                        session,
                        select_fields=["characters"],
                        format=format,
                    )
                    return [pic.id for pic in pics if not pic.characters]

                picture_ids = self.vault.db.run_task(find_unassigned)
                if not picture_ids:
                    return []
                pics = self.vault.db.run_task(
                    Picture.find,
                    id=picture_ids,
                    sort=sort_mech,
                    offset=offset,
                    limit=limit,
                    select_fields=Picture.metadata_fields(),
                )
                return [safe_model_dict(pic) for pic in pics]

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
                    sort=sort_mech,
                    offset=offset,
                    limit=limit,
                    select_fields=Picture.metadata_fields(),
                    format=format,
                )
                return [safe_model_dict(pic) for pic in pics]
            else:
                pics = self.vault.db.run_task(
                    Picture.find,
                    sort=sort_mech,
                    offset=offset,
                    limit=limit,
                    select_fields=Picture.metadata_fields(),
                    format=format,
                    **query_params,
                )
            return [safe_model_dict(pic) for pic in pics]
