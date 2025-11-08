import uvicorn
import io
import os
import json
import uuid
import mimetypes
import re
import concurrent.futures
import sys

from dataclasses import asdict
from contextlib import asynccontextmanager
from fastapi import Body, FastAPI, File, Form, Request, UploadFile, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import Image
from rapidfuzz import fuzz
from typing import List

from pixelurgy_vault.character import CharacterModel
from pixelurgy_vault.logging import get_logger
from pixelurgy_vault.picture_utils import PictureUtils
from pixelurgy_vault.pictures import get_sort_mechanisms
from pixelurgy_vault.vault import Vault

DEFAULT_DESCRIPTION = "Pixelurgy Vault default configuration"

# Logging will be set up after config is loaded
logger = get_logger(__name__)


class Server:
    def __enter__(self):
        # Allow use as a context manager for robust cleanup
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self, "vault"):
            self.vault.close()

    """
    Main server class for the Pixelurgy Vault FastAPI application.

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
        print(f"Initializing Pixelurgy Vault server with config: {config_path}")
        self._config_path = config_path

        self._config = self.init_config(config_path)
        with open(config_path, "w") as f:
            json.dump(self._config, f, indent=2)

        self._server_config = self.init_server_config(server_config_path)
        with open(server_config_path, "w") as f:
            json.dump(self._server_config, f, indent=2)

        # SSL config
        if self._server_config.get("require_ssl", False):
            self._ensure_ssl_certificates()

        logger.info(
            "Creating Vault instance with image root: "
            + str(self._config["selected_image_root"])
        )

        print(
            f"Creating Vault instance with image root: {self._config['selected_image_root']}"
        )
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
        self._init_worker_pause()

    def run(self):
        uvicorn_kwargs = dict(
            host="0.0.0.0",
            port=self._server_config.get("port", 8000),
        )
        if self._server_config.get("require_ssl", False):
            uvicorn_kwargs["ssl_keyfile"] = self._server_config.get("ssl_keyfile")
            uvicorn_kwargs["ssl_certfile"] = self._server_config.get("ssl_certfile")
            print(
                f"[SSL] Running with SSL: keyfile={self._server_config.get('ssl_keyfile')}, certfile={self._server_config.get('ssl_certfile')}"
            )
        uvicorn.run(self.api, **uvicorn_kwargs)

    def _init_worker_pause(self):
        import threading

        self._worker_resume_timer = None
        self._worker_pause_lock = threading.Lock()

    def _schedule_worker_resume(self, delay=5):
        import threading

        with self._worker_pause_lock:
            if self._worker_resume_timer is not None:
                self._worker_resume_timer.cancel()
            self._worker_resume_timer = threading.Timer(delay, self._resume_workers)
            self._worker_resume_timer.start()

    def _resume_workers(self):
        with self._worker_pause_lock:
            self._worker_resume_timer = None
        self.vault.start_background_workers()

    @asynccontextmanager
    async def lifespan(self, app):
        # Startup logic (if needed)
        yield
        # Shutdown logic
        if hasattr(self, "vault"):
            self.vault.close()

    @staticmethod
    def init_config(
        config_path,
    ):
        """
        Initialize and load the server configuration from file, creating defaults if necessary.

        Returns:
            dict: Configuration dictionary.
        """
        config_dir = os.path.dirname(config_path)
        os.makedirs(config_dir, exist_ok=True)

        default_image_root = os.path.join(config_dir, "images")

        config = {}
        if not os.path.exists(config_path):
            config = {
                "image_roots": [default_image_root],
                "selected_image_root": default_image_root,
                "description": DEFAULT_DESCRIPTION,
                "sort": "date_desc",
                "thumbnail": "default",
                "show_stars": True,
                "show_only_reference": False,
                "openai_host": "localhost",
                "openai_port": 8000,
                "openai_model": "gpt-3.5-turbo",
            }
        else:
            with open(config_path, "r") as f:
                config = json.load(f)
                # Ensure new config options exist

                if "sort_order" not in config:
                    config["sort_order"] = "date_desc"
                if "thumbnail_size" not in config:
                    config["thumbnail_size"] = "default"
                if "show_stars" not in config:
                    config["show_stars"] = True
                if "show_only_reference" not in config:
                    config["show_only_reference"] = False
                if "openai_host" not in config:
                    config["openai_host"] = "localhost"
                if "openai_port" not in config:
                    config["openai_port"] = 8000
                if "openai_model" not in config:
                    config["openai_model"] = "gpt-3.5-turbo"
                if "image_roots" not in config or len(config["image_roots"]) == 0:
                    config["image_roots"] = [default_image_root]
                if "selected_image_root" not in config:
                    config["selected_image_root"] = config["image_roots"][0]

        return config

    @staticmethod
    def init_server_config(server_config_path):
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

    def _setup_routes(self):
        @self.api.get("/config")
        async def get_config():
            """
            Return the current image roots config (config.json) and OpenAI chat service config.
            """
            logger.debug(f"Transmitting current config {self._config}")
            return self._config

        @self.api.patch("/config")
        async def patch_config(request: Request):
            """
            Update existing config values or append to existing lists. Does not allow adding new keys.
            Body: { key: value, ... } (value replaces or is appended to existing key)
            If the value is a list and the existing value is a list, appends items.
            Ensures new image root directories and DBs are created as needed.
            """
            import os

            patch_data = await request.json()
            updated = False
            image_root_changed = False
            for key, value in patch_data.items():
                if key not in self._config:
                    # Allow adding 'sort', 'thumbnail', 'show_stars', 'show_only_reference' keys if missing
                    if key in (
                        "sort",
                        "thumbnail",
                        "show_stars",
                        "show_only_reference",
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
            return {"status": "success", "updated": updated, "config": self._config}

        @self.api.get("/sort_mechanisms")
        async def get_pictures_sort_mechanisms():
            """Return available sorting mechanisms for pictures."""
            return get_sort_mechanisms()

        @self.api.get("/face_thumbnail/{character_id}")
        async def get_face_thumbnail(character_id: str):
            """
            Return a face-cropped thumbnail for the highest scored picture of the character.
            If no scored picture, fallback to first image. If no face bbox, fallback to normal thumbnail.
            Cropped region is resized to fit within 96x96, preserving aspect ratio.
            """
            logger.debug(f"Generating face thumbnail for character_id: {character_id}")
            pics = self.vault.pictures.find(character_id=character_id)
            logger.debug(f"Found {len(pics)} pictures for character_id: {character_id}")
            if not pics:
                raise HTTPException(status_code=404, detail="No pictures for character")

            # Sort by score descending, then by created_at
            def score_key(picture):
                return (
                    picture.score if picture.score is not None else -1,
                    picture.created_at,
                )

            pics.sort(key=score_key, reverse=True)
            pic = pics[0]

            if pic.thumbnail is None:
                logger.info(f"No thumbnail available for picture_id: {pic.id}")
            else:
                logger.debug(f"Thumbnail available for picture_id: {pic.id}")

            face_bbox = pic.face_bbox
            if not face_bbox:
                logger.debug(
                    f"No face_bbox attribute on picture for character_id: {character_id}"
                )
            # Load thumbnail image
            if not pic.thumbnail:
                raise HTTPException(status_code=404, detail="No thumbnail available")
            try:
                thumb_img = Image.open(io.BytesIO(pic.thumbnail))
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid thumbnail image")
            # If face_bbox is available, crop to it
            if face_bbox and len(face_bbox) == 4:
                logger.debug(f"Cropping thumbnail to face bbox: {face_bbox}")
                x1, y1, x2, y2 = [int(round(v)) for v in face_bbox]
                w, h = thumb_img.size
                x1 = max(0, min(w, x1))
                x2 = max(0, min(w, x2))
                y1 = max(0, min(h, y1))
                y2 = max(0, min(h, y2))
                if x2 > x1 and y2 > y1:
                    thumb_img = thumb_img.crop((x1, y1, x2, y2))
            # Resize so height=96px, width scaled proportionally
            target_height = 96
            w, h = thumb_img.size
            if h != target_height:
                scale = target_height / h
                new_w = int(round(w * scale))
                thumb_img = thumb_img.resize((new_w, target_height), Image.LANCZOS)
            buf = io.BytesIO()
            thumb_img.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")

        @self.api.get("/search")
        async def search_pictures(
            query: str = Query(""), top_n: int = Query(5), threshold: float = Query(0.3)
        ):
            """
            Combined hybrid search: fuzzy tag/description and embedding, weighted by query length.
            Query params: ?query=...&top_n=...&threshold=...
            """

            def pic_to_dict(pic, likeness_score=None):
                d = {
                    "id": pic.id,
                    "character_id": pic.character_id,
                    "description": pic.description,
                    "tags": pic.tags,
                    "created_at": pic.created_at,
                    "is_reference": getattr(pic, "is_reference", 0),
                }
                if likeness_score is not None:
                    d["likeness_score"] = likeness_score
                return d

            q = query.strip().lower()
            if not q:
                return []

            # Split on any whitespace or punctuation
            q_split = re.split(r"[\s\W]+", q)
            q_split = [w for w in q_split if w]
            n_words = len(q_split)

            # Fuzzy search (tag/description/character name)
            all_pics = self.vault.pictures.find()
            fuzzy_scores = {}
            for pic in all_pics:
                tag_scores = []
                # Add character name to tags for fuzzy search
                tags_and_name = list(pic.tags)
                char_name = None
                char_id = getattr(pic, "character_id", None)
                if char_id is not None:
                    try:
                        char_obj = self.vault.characters[int(char_id)]
                        if getattr(char_obj, "name", None):
                            char_name = char_obj.name
                    except Exception:
                        char_name = None
                if char_name:
                    names = char_name.split(" ")
                    for name in names:
                        tags_and_name.append(name)
                for q_word in q_split:
                    max_score = 0
                    logger.debug(f"Query word: {q_word}")
                    for tag in tags_and_name:
                        score = fuzz.ratio(q_word, str(tag).lower()) / 100
                        score *= min(len(q_word), len(tag)) / max(
                            len(q_word), len(tag), 1
                        )
                        if score > max_score:
                            max_score = score
                    tag_scores.append(max_score)
                    desc_score = (
                        fuzz.ratio(q_word, (pic.description or "").lower()) / 100.0
                    )
                logger.info(
                    f"PicID={pic.id} Desc='{pic.description}' Desc score={desc_score}, Tag scores={tag_scores}"
                )
                avg_score = sum(tag_scores) / len(tag_scores) if tag_scores else 0
                max_score = max(tag_scores) if tag_scores else 0
                total_score = max(0.4 * avg_score + 0.6 * max_score, desc_score)
                logger.info(
                    f"PicID={pic.id} Desc='{pic.description}' Desc score={desc_score}, Tag scores={tag_scores}, total score={total_score}"
                )
                fuzzy_scores[pic.id] = total_score

            # Embedding search
            # For 1-2 words, expand query for better semantic results
            if n_words <= 3:
                expanded = f"A photo of {q}" if n_words >= 1 else q
                semantic_results = self.vault.pictures.find_by_text(
                    expanded,
                    top_n=top_n * 3,
                    include_scores=True,
                    threshold=threshold / 2,
                )
                if not semantic_results:
                    semantic_results = self.vault.pictures.find_by_text(
                        q, top_n=top_n * 3, include_scores=True, threshold=threshold / 2
                    )
            else:
                semantic_results = self.vault.pictures.find_by_text(
                    q, top_n=top_n * 3, include_scores=True, threshold=threshold / 2
                )
            semantic_scores = {pic.id: score for pic, score in semantic_results}

            # Weighting: more words = more semantic weight
            # <=2 words: 80% fuzzy, 20% semantic
            # 3 words: 50/50
            # 4 words: 30% fuzzy, 70% semantic
            # 5+ 20% fuzzy, 80% semantic
            if n_words <= 2:
                fuzzy_w, sem_w = 0.9, 0.1
            elif n_words == 3:
                fuzzy_w, sem_w = 0.6, 0.4
            elif n_words == 4:
                fuzzy_w, sem_w = 0.3, 0.7
            else:
                fuzzy_w, sem_w = 0.2, 0.8

            # Merge scores
            all_ids = set(fuzzy_scores.keys()) | set(semantic_scores.keys())
            combined = []
            for pic_id in all_ids:
                fuzzy_score = fuzzy_scores.get(pic_id, 0)
                sem_score = semantic_scores.get(pic_id, 0)
                combined_score = fuzzy_w * fuzzy_score + sem_w * sem_score
                logger.info(
                    f"Got combined score of {combined_score} for PicID={pic_id} (Fuzzy={fuzzy_score}, Semantic={sem_score})"
                )
                pic = next((p for p in all_pics if p.id == pic_id), None)
                if pic:
                    if combined_score < threshold:
                        continue
                    # Diagnostics: log why this picture matched
                    tags_and_name = list(pic.tags)
                    char_name = None
                    char_id = getattr(pic, "character_id", None)
                    if char_id is not None:
                        try:
                            char_obj = self.vault.characters[int(char_id)]
                            if getattr(char_obj, "name", None):
                                char_name = char_obj.name
                        except Exception:
                            char_name = None
                    if char_name:
                        tags_and_name.append(char_name)
                    logger.debug(
                        f"[SEARCH DIAG] Query='{q}' | PicID={pic.id} | Tags+Name={tags_and_name} | Desc='{pic.description}' | Fuzzy={fuzzy_score:.2f} | Embedding={sem_score:.2f} | Combined={combined_score:.2f}"
                    )
                    combined.append((pic, combined_score, fuzzy_score, sem_score))

            # Sort by combined score, then by created_at
            combined.sort(key=lambda x: (-x[1], x[0].created_at or ""))
            # Optionally, include fuzzy/semantic scores for debugging:
            # return [{**pic_to_dict(pic), "score": score, "fuzzy": fuzzy, "semantic": sem} for pic, score, fuzzy, sem in combined[:top_n]]
            return [
                pic_to_dict(pic, likeness_score=score)
                for pic, score, _, _ in combined[:top_n]
            ]

        @self.api.get("/characters/reference_pictures/{id}")
        async def get_reference_pictures(id: str):
            """
            Get all reference pictures for a character (is_reference=1).
            """
            try:
                reference_pics = self.vault.pictures.find(
                    is_reference=1, character_id=id
                )
                logger.info(
                    f"Found {len(reference_pics)} reference pictures for character id={id}"
                )
                return {
                    "reference_pictures": [
                        pic.to_dict(exclude=["file_path"]) for pic in reference_pics
                    ]
                }
            except KeyError:
                raise HTTPException(status_code=404, detail="Character not found")

        @self.api.patch("/characters/{id}")
        async def patch_character(id: int, request: Request):
            data = await request.json()
            name = data.get("name")
            description = data.get("description")
            try:
                char = self.vault.characters[id]
            except KeyError:
                raise HTTPException(status_code=404, detail="Character not found")
            updated = False
            if name is not None and name != char.name:
                char.name = name
                updated = True
                # Drop embeddings for all pictures with this character_id
                pics = self.vault.pictures.find(character_id=id)
                for pic in pics:
                    pic.embedding = None

                self.vault.pictures.update(pics)
            if description is not None and description != char.description:
                char.description = description
                updated = True
            if updated:
                self.vault.characters.update(char)
            return {"status": "success", "character": char.__dict__}

        @self.api.delete("/characters/{id}")
        async def delete_character(id: int):
            # Delete the character
            try:
                self.vault.characters.delete(id)
                return {"status": "success", "deleted_id": id}
            except KeyError:
                raise HTTPException(status_code=404, detail="Character not found")

        @self.api.get("/characters")
        async def get_characters(name: str = Query(None)):
            """List all characters or filter by name."""
            chars = (
                self.vault.characters.find(name=name)
                if name
                else self.vault.characters.find()
            )
            return [c.__dict__ for c in chars]

        @self.api.post("/characters")
        async def create_character(payload: dict = Body(...)):
            try:
                character = CharacterModel(**payload)
                self.vault.characters.add(character)

                return {"status": "success", "character": asdict(character)}
            except Exception as e:
                logger.error(f"Error creating character: {e}")
                raise HTTPException(status_code=400, detail="Invalid character data")

        @self.api.get("/characters/{id}")
        async def get_character_by_id(id: int):
            try:
                char = self.vault.characters[id]
            except KeyError:
                raise HTTPException(status_code=404, detail="Character not found")
            return char.__dict__

        @self.api.get("/")
        async def read_root():
            version = self.get_version()
            return {"message": "Pixelurgy Vault REST API", "version": version}

        @self.api.get("/pictures/{id}")
        async def get_picture(
            id: str, info: bool = Query(False), embedding: bool = Query(False)
        ):
            if not isinstance(id, str):
                logger.error(f"Invalid id type: {type(id)} value: {id}")
                raise HTTPException(status_code=400, detail="Invalid picture id type")
            try:
                pic = self.vault.pictures[id]
            except KeyError:
                logger.error(f"Picture not found for id={id}")
                raise HTTPException(status_code=404, detail="Picture not found")
            if info:
                # Return metadata only
                result = pic.to_dict(exclude=["file_path", "thumbnail"])
                return result
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

        @self.api.get("/thumbnails/{id}")
        async def get_thumbnail(id: str):
            try:
                pic = self.vault.pictures[id]
                thumbnail_bytes = pic.thumbnail
                if not thumbnail_bytes:
                    logger.error(f"No thumbnail available for picture id={pic.id}")
                    raise HTTPException(
                        status_code=404, detail="No thumbnail available"
                    )
                return Response(content=thumbnail_bytes, media_type="image/png")
            except KeyError:
                logger.error(f"Picture not found for id={id} (thumbnail request)")
                raise HTTPException(status_code=404, detail="Picture not found")

        @self.api.patch("/pictures/{id}")
        async def patch_picture(id: str, request: Request):
            """
            Update fields of a picture using query parameters, e.g., /pictures/{id}?score=5
            Also supports JSON body with fields to update, e.g., { "tags": ["tag1", "tag2"] }.
            """
            params = dict(request.query_params)

            # If PATCH is called with a JSON body, use it
            content_type = request.headers.get("content-type", "")
            json_body = None
            if "application/json" in content_type:
                try:
                    json_body = await request.json()
                except Exception:
                    json_body = None

            try:
                pic = self.vault.pictures[id]
            except KeyError:
                raise HTTPException(status_code=404, detail="Picture not found")

            logger.debug(f"Updating picture id={id}")
            # If JSON body is provided, use it
            if json_body and isinstance(json_body, dict):
                params = json_body | params

            logger.debug(f"Updating picture id={id}")
            updated = False
            # Update fields
            for key, value in params.items():
                if key == "is_reference":
                    # Special case: convert to int
                    try:
                        cast_val = value.lower() in ("1", "true", "yes")
                    except Exception:
                        cast_val = False
                else:
                    try:
                        cast_val = int(value)
                    except Exception:
                        cast_val = value

                if hasattr(pic, key):
                    logger.debug(
                        f"Updating picture id={id} field={key} to value={cast_val}"
                    )
                    old_val = getattr(pic, key)
                    setattr(pic, key, cast_val)
                    # Drop embedding if character_id changes
                    if key == "character_id" and old_val != cast_val:
                        pic.embedding = None
                    updated = True
            if updated:
                self.vault.pictures.update(pic)
            return {"status": "success", "picture": pic.to_dict()}

        @self.api.get("/favicon.ico")
        def favicon():
            favicon_path = os.path.join(os.path.dirname(__file__), "favicon.ico")
            return FileResponse(favicon_path)

        @self.api.post("/pictures")
        async def import_pictures(
            file: List[UploadFile] = File(None),
            character_id: str = Form(None),
        ):
            """
            Import new pictures. Accepts:
            - image: bytes upload (single file)
            Detects media type and sets ID as uuid + extension.
            """

            dest_folder = self.vault.image_root
            logger.info("Importing pictures to folder: " + str(dest_folder))
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

            import_results, new_pictures = self.create_picture_imports(
                uploaded_files, dest_folder, character_id
            )

            logger.info(
                f"Importing {len(new_pictures)} new pictures out of {len(uploaded_files)} uploaded."
            )

            # Import all at once
            if new_pictures:
                self.vault.pictures.add(new_pictures)

            if not new_pictures:
                logger.error("No new pictures to import; all are duplicates.")
                raise HTTPException(
                    status_code=400, detail="All pictures are duplicates"
                )

            return {"results": import_results}

        @self.api.get("/picture_ids")
        async def list_picture_ids(
            request: Request,
            sort: str = Query("unsorted"),
            query: str = Query(None),
        ):
            """
            Get only picture IDs for the current view (no thumbnails, embeddings, or other heavy data).
            Much faster than /pictures endpoint for selecting all images.
            Respects all filter parameters (character_id, tags, is_reference, etc.) and search.
            """
            from pixelurgy_vault.pictures import SortMechanism

            query_params = dict(request.query_params)
            query_params.pop("sort", None)
            query_params.pop("query", None)

            # Convert tags to list if present
            if "tags" in query_params and isinstance(query_params["tags"], str):
                try:
                    query_params["tags"] = json.loads(query_params["tags"])
                except Exception:
                    query_params["tags"] = [query_params["tags"]]

            # Handle search likeness sort (semantic search)
            if sort == SortMechanism.SEARCH_LIKENESS.value and query:
                pics = self.vault.pictures.find_by_text(query, top_n=sys.maxsize)
            else:
                pics = self.vault.pictures.find(**query_params)

                if sort in [
                    SortMechanism.SCORE_DESC.value,
                    SortMechanism.SCORE_ASC.value,
                ]:
                    reverse = sort == SortMechanism.SCORE_DESC.value
                    pics.sort(
                        key=lambda p: p.score if p.score is not None else -1,
                        reverse=reverse,
                    )
                elif sort in [
                    SortMechanism.DATE_DESC.value,
                    SortMechanism.DATE_ASC.value,
                ]:
                    reverse = sort == SortMechanism.DATE_DESC.value
                    pics.sort(key=lambda p: p.created_at or "", reverse=reverse)

            # Return only IDs - much faster than full to_dict()
            return [pic.id for pic in pics]

        @self.api.get("/pictures")
        async def list_pictures(
            request: Request,
            info: bool = Query(False),
            sort: str = Query("unsorted"),
            offset: int = Query(0),
            limit: int = Query(sys.maxsize),
            query: str = Query(None),
        ):
            from pixelurgy_vault.pictures import SortMechanism

            query_params = dict(request.query_params)
            query_params.pop("info", None)
            query_params.pop("sort", None)
            query_params.pop("offset", None)
            query_params.pop("limit", None)
            query_params.pop("query", None)
            # Convert tags to list if present
            if "tags" in query_params and isinstance(query_params["tags"], str):
                try:
                    query_params["tags"] = json.loads(query_params["tags"])
                except Exception:
                    query_params["tags"] = [query_params["tags"]]

            # Handle search likeness sort (semantic search)
            if sort == SortMechanism.SEARCH_LIKENESS.value and query:
                # Use semantic search, return top-N (limit) results
                if limit == sys.maxsize:
                    pics = self.vault.pictures.find_by_text(query, top_n=sys.maxsize)
                else:
                    pics = self.vault.pictures.find_by_text(query, top_n=offset + limit)
                    pics = pics[offset : offset + limit]
            else:
                pics = self.vault.pictures.find(**query_params)

                if sort in [
                    SortMechanism.SCORE_DESC.value,
                    SortMechanism.SCORE_ASC.value,
                ]:
                    reverse = sort == SortMechanism.SCORE_DESC.value
                    pics.sort(
                        key=lambda p: p.score if p.score is not None else -1,
                        reverse=reverse,
                    )
                elif sort in [
                    SortMechanism.DATE_DESC.value,
                    SortMechanism.DATE_ASC.value,
                ]:
                    reverse = sort == SortMechanism.DATE_DESC.value
                    pics.sort(key=lambda p: p.created_at or "", reverse=reverse)
                elif sort in [
                    SortMechanism.SHARPNESS_DESC.value,
                    SortMechanism.SHARPNESS_ASC.value,
                ]:
                    reverse = sort == SortMechanism.SHARPNESS_DESC.value
                    pics.sort(
                        key=lambda p: json.loads(p.quality).get("sharpness", -1)
                        if p.quality
                        else -1,
                        reverse=reverse,
                    )
                elif sort in [
                    SortMechanism.EDGE_DENSITY_DESC.value,
                    SortMechanism.EDGE_DENSITY_ASC.value,
                ]:
                    reverse = sort == SortMechanism.EDGE_DENSITY_DESC.value
                    pics.sort(
                        key=lambda p: json.loads(p.quality).get("edge_density", -1)
                        if p.quality
                        else -1,
                        reverse=reverse,
                    )
                elif sort in [
                    SortMechanism.NOISE_LEVEL_DESC.value,
                    SortMechanism.NOISE_LEVEL_ASC.value,
                ]:
                    reverse = sort == SortMechanism.NOISE_LEVEL_DESC.value
                    pics.sort(
                        key=lambda p: json.loads(p.quality).get("noise_level", -1)
                        if p.quality
                        else -1,
                        reverse=reverse,
                    )
                elif sort == SortMechanism.HAS_DESCRIPTION.value:
                    # Pictures with descriptions first
                    pics.sort(key=lambda p: 0 if p.description else 1)
                elif sort == SortMechanism.NO_DESCRIPTION.value:
                    # Pictures without descriptions first
                    pics.sort(key=lambda p: 1 if p.description else 0)
                # else: unsorted
                if limit != sys.maxsize:
                    pics = pics[offset : offset + limit]

            return [pic.to_dict() for pic in pics]

        @self.api.delete("/pictures/{id}")
        async def delete_picture(id: str):
            """
            Delete a picture by id
            """
            self.vault.pictures.delete(id)
            return {
                "status": "success",
            }

        @self.api.get("/category/summary")
        async def get_category_summary(character_id: str = Query(None)):
            """
            Return summary statistics for a single category:
            - If character_id is omitted: all pictures
            - If character_id is null/None/empty: unassigned pictures
            - If character_id is set: that character's pictures
            """

            # Determine which set to query
            if character_id is None:
                # All
                pics = self.vault.pictures.find()
                char_id = None
            elif character_id == "null":
                # Unassigned
                pics = self.vault.pictures.find(character_id="null")
                char_id = None
            else:
                pics = self.vault.pictures.find(character_id=character_id)
                char_id = character_id

            image_count = len(pics)

            reference_image_count = sum(1 for p in pics if p.is_reference == 1)
            last_updated = max(
                (p.created_at for p in pics if p.created_at), default=None
            )
            # Thumbnail URL (reuse existing endpoint)
            thumb_url = None
            if char_id not in (None, "", "null"):
                thumb_url = f"/face_thumbnail/{char_id}"
            summary = {
                "character_id": char_id,
                "image_count": image_count,
                "reference_image_count": reference_image_count,
                "last_updated": last_updated,
                "thumbnail_url": thumb_url,
            }
            return summary

    def create_picture_imports(self, uploaded_files, dest_folder, character_id):
        """
        Given a list of (img_bytes, src_path, ext), create Picture objects for new images,
        skipping duplicates based on pixel_sha hash.
        Returns (new_pictures, existing_pictures)
        """

        def create_sha(img_bytes):
            return PictureUtils.calculate_hash_from_bytes(img_bytes)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            shas = list(
                executor.map(create_sha, (img_bytes for img_bytes, _ in uploaded_files))
            )

        existing_pictures = self.vault.pictures.fetch_by_shas(shas)

        logger.info(
            "Got "
            + str(len(existing_pictures))
            + " existing pictures to skip and importing {}".format(
                len(uploaded_files) - len(existing_pictures)
            )
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
                pic_id = str(uuid.uuid4()) + ext
                logger.info(f"Importing picture from uploaded bytes as id={pic_id}")
                return PictureUtils.create_picture_from_bytes(
                    image_root_path=dest_folder,
                    image_bytes=img_bytes,
                    picture_id=pic_id,
                    character_id=character_id,
                    pixel_sha=sha,
                )

            with concurrent.futures.ThreadPoolExecutor() as executor:
                new_pictures = list(executor.map(create_one_picture, importable))
        else:
            new_pictures = []

        # Order new pictures according to original upload order
        results = []
        index = 0
        for _, sha in zip(uploaded_files, shas):
            if sha in existing_map:
                pic = existing_map[sha]
                results.append(
                    {"status": "duplicate", "picture_id": pic.id, "file": pic.file_path}
                )
            else:
                pic = new_pictures[index]
                results.append(
                    {"status": "success", "picture_id": pic.id, "file": pic.file_path}
                )
                index += 1

        return results, new_pictures

    def get_version(self):
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
