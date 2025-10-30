from contextlib import asynccontextmanager
from fastapi import Body, FastAPI, File, Form, Request, UploadFile, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from .logging import get_logger, setup_logging
import uvicorn
import os
import json
import uuid
import argparse

from platformdirs import user_config_dir
from pixelurgy_vault.vault import Vault
from pixelurgy_vault.picture import Picture
from pixelurgy_vault.picture_iteration import PictureIteration

APP_NAME = "pixelurgy-vault"
CONFIG_PATH = os.path.join(user_config_dir(APP_NAME), "config.json")


# Logging will be set up after config is loaded
logger = None


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
        config (dict): Server configuration dictionary.
        vault (Vault): Vault instance for database operations.
        app (FastAPI): FastAPI application instance.
    """

    def __init__(
        self,
        config_path=CONFIG_PATH,
        vault_db_path=None,
        image_root=None,
        description=None,
        log_file=None,
    ):
        """
        Initialize the Server instance.

        Args:
            vault_db_path (str, optional): Path to the vault database file.
            image_root (str, optional): Path to the image root directory.
            description (str, optional): Vault description.
            log_file (str, optional): Path to the log file (or None for stdout).
        """
        self.config = self.init_config(
            config_path, vault_db_path, image_root, description, log_file
        )
        # Override config values with explicit arguments
        if vault_db_path is not None:
            self.config["db_path"] = vault_db_path
        if image_root is not None:
            self.config["image_root"] = image_root
        if description is not None:
            self.config["description"] = description
        if log_file is not None:
            self.config["log_file"] = log_file
        global logger
        setup_logging(self.config.get("log_file"))
        logger = get_logger(__name__)
        self.vault = Vault(
            db_path=self.config["db_path"],
            image_root=self.config["image_root"],
            description=self.config["description"],
        )

        self.app = FastAPI(lifespan=self.lifespan)
        # Enable CORS for frontend dev server
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Or restrict to ["http://localhost:5173"]
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self.setup_routes()

    @asynccontextmanager
    async def lifespan(self, app):
        # Startup logic (if needed)
        yield
        # Shutdown logic
        if hasattr(self, "vault"):
            self.vault.close()

    def init_config(
        self,
        config_path=CONFIG_PATH,
        vault_db_path=None,
        image_root=None,
        description="Pixelurgy Vault default configuration",
        log_file=None,
    ):
        """
        Initialize and load the server configuration from file, creating defaults if necessary.

        Returns:
            dict: Configuration dictionary.
        """
        config_dir = os.path.dirname(config_path)
        os.makedirs(config_dir, exist_ok=True)
        if not os.path.exists(config_path):
            config = {
                "db_path": vault_db_path or os.path.join(config_dir, "vault.db"),
                "image_root": image_root or os.path.join(config_dir, "images"),
                "description": description,
                "log_file": log_file,
                "port": 9537,
            }
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
        else:
            with open(config_path, "r") as f:
                config = json.load(f)
            # Do not override log_file here; let __init__ handle it
        return config

    def setup_routes(self):
        from pixelurgy_vault.pictures import get_sort_mechanisms

        @self.app.get("/pictures/sort_mechanisms")
        async def get_pictures_sort_mechanisms():
            """Return available sorting mechanisms for pictures."""
            return get_sort_mechanisms()

        @self.app.get("/face_thumbnail/{character_id}")
        async def get_face_thumbnail(character_id: str):
            """
            Return a face-cropped thumbnail for the highest scored picture of the character.
            If no scored picture, fallback to first image. If no face bbox, fallback to normal thumbnail.
            """
            import io
            from PIL import Image

            # Find all pictures for this character
            pics = self.vault.pictures.find(character_id=character_id)
            if not pics:
                raise HTTPException(status_code=404, detail="No pictures for character")
            # Find master iterations for these pictures
            its = []
            for pic in pics:
                master_its = self.vault.iterations.find(picture_id=pic.id, is_master=1)
                if master_its:
                    it = master_its[0]
                    its.append((it, pic))
            if not its:
                raise HTTPException(
                    status_code=404, detail="No master iterations for character"
                )

            # Sort by score descending, then by created_at
            def score_key(tup):
                it, pic = tup
                return (it.score if it.score is not None else -1, pic.created_at)

            its.sort(key=score_key, reverse=True)
            it, pic = its[0]
            # Try to get face_bbox from the picture
            face_bbox = None
            if hasattr(pic, "face_bbox") and pic.face_bbox:
                try:
                    face_bbox = (
                        json.loads(pic.face_bbox)
                        if isinstance(pic.face_bbox, str)
                        else pic.face_bbox
                    )
                except Exception:
                    face_bbox = None
            # Load thumbnail image
            if not it.thumbnail:
                raise HTTPException(status_code=404, detail="No thumbnail available")
            try:
                thumb_img = Image.open(io.BytesIO(it.thumbnail))
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid thumbnail image")
            # If face_bbox is available, crop to it
            if face_bbox and len(face_bbox) == 4:
                x1, y1, x2, y2 = [int(round(v)) for v in face_bbox]
                w, h = thumb_img.size
                x1 = max(0, min(w, x1))
                x2 = max(0, min(w, x2))
                y1 = max(0, min(h, y1))
                y2 = max(0, min(h, y2))
                if x2 > x1 and y2 > y1:
                    thumb_img = thumb_img.crop((x1, y1, x2, y2))
            # Resize to 96x96 for sidebar (twice the previous size)
            thumb_img = thumb_img.resize((96, 96), Image.LANCZOS)
            buf = io.BytesIO()
            thumb_img.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")

        @self.app.post("/log-frontend-event")
        async def log_frontend_event(event: dict = Body(...)):
            """
            Log frontend-reported events such as failed image loads or missing descriptions.
            Body: { "event_type": str, "picture_id": str, "character_id": str, ... }
            """
            logger.info(f"Frontend event: {json.dumps(event)}")
            return {"status": "logged"}

        @self.app.get("/pictures/search")
        def search_pictures(
            query: str = Query(""), top_n: int = Query(5), threshold: float = Query(0.3)
        ):
            """
            Combined hybrid search: fuzzy tag/description and embedding, weighted by query length.
            Query params: ?query=...&top_n=...&threshold=...
            """
            from rapidfuzz import fuzz

            def pic_to_dict(pic):
                return {
                    "id": pic.id,
                    "character_id": pic.character_id,
                    "description": pic.description,
                    "tags": pic.tags,
                    "created_at": pic.created_at,
                    "is_reference": getattr(pic, "is_reference", 0),
                }

            q = query.strip()
            n_words = len(q.split())
            if not q:
                return []

            # Fuzzy search (tag/description)
            all_pics = self.vault.pictures.find()
            fuzzy_scores = {}
            for pic in all_pics:
                best_tag_score = 0
                for tag in pic.tags:
                    score = fuzz.partial_ratio(q.lower(), str(tag).lower())
                    if score > best_tag_score:
                        best_tag_score = score
                desc_score = fuzz.partial_ratio(
                    q.lower(), (pic.description or "").lower()
                )
                max_score = max(best_tag_score, desc_score)
                if max_score > 60:  # threshold for fuzzy match
                    fuzzy_scores[pic.id] = max_score / 100.0  # normalize to 0-1

            # Embedding search
            # For 1-2 words, expand query for better semantic results
            if n_words <= 3:
                expanded = f"A photo of {q}" if n_words >= 1 else q
                semantic_results = self.vault.pictures.find_by_text(
                    expanded, top_n=top_n * 3, include_scores=True, threshold=threshold
                )
                if not semantic_results:
                    semantic_results = self.vault.pictures.find_by_text(
                        q, top_n=top_n * 3, include_scores=True, threshold=threshold
                    )
            else:
                semantic_results = self.vault.pictures.find_by_text(
                    q, top_n=top_n * 3, include_scores=True, threshold=threshold
                )
            semantic_scores = {pic.id: score for pic, score in semantic_results}

            # Weighting: more words = more semantic weight
            # 1 word: 90% fuzzy, 10% semantic
            # 2 words: 70% fuzzy, 30% semantic
            # 3 words: 50/50
            # 4+ words: 30% fuzzy, 70% semantic (min 10% fuzzy, max 90% semantic)
            if n_words <= 1:
                fuzzy_w, sem_w = 0.9, 0.1
            elif n_words == 2:
                fuzzy_w, sem_w = 0.7, 0.3
            elif n_words == 3:
                fuzzy_w, sem_w = 0.5, 0.5
            elif n_words == 4:
                fuzzy_w, sem_w = 0.3, 0.7
            else:
                fuzzy_w, sem_w = 0.1, 0.9

            # Merge scores
            all_ids = set(fuzzy_scores.keys()) | set(semantic_scores.keys())
            combined = []
            for pic_id in all_ids:
                fuzzy_score = fuzzy_scores.get(pic_id, 0)
                sem_score = semantic_scores.get(pic_id, 0)
                combined_score = fuzzy_w * fuzzy_score + sem_w * sem_score
                pic = next((p for p in all_pics if p.id == pic_id), None)
                if pic:
                    combined.append((pic, combined_score, fuzzy_score, sem_score))

            # Sort by combined score, then by created_at
            combined.sort(key=lambda x: (-x[1], x[0].created_at or ""))
            # Optionally, include fuzzy/semantic scores for debugging:
            # return [{**pic_to_dict(pic), "score": score, "fuzzy": fuzzy, "semantic": sem} for pic, score, fuzzy, sem in combined[:top_n]]
            return [pic_to_dict(pic) for pic, _, _, _ in combined[:top_n]]

        @self.app.get("/characters/reference_pictures/{id}")
        def get_reference_pictures(id: str):
            """
            Get all reference pictures for a character (is_reference=1, master iteration only).
            """
            pics = self.vault.pictures.find(character_id=id)
            reference_pics = [
                pic for pic in pics if getattr(pic, "is_reference", 0) == 1
            ]
            pic_ids = [pic.id for pic in reference_pics]
            iter_map = {}
            if pic_ids:
                cursor = self.vault.connection.cursor()
                qmarks = ",".join(["?"] * len(pic_ids))
                cursor.execute(
                    f"SELECT id, picture_id FROM picture_iterations WHERE is_master=1 AND picture_id IN ({qmarks})",
                    tuple(pic_ids),
                )
                for row in cursor.fetchall():
                    iter_map[row[1]] = row[0]
            results = []
            for pic in reference_pics:
                iteration_id = iter_map.get(pic.id)
                if iteration_id:
                    results.append(
                        {
                            "picture_id": pic.id,
                            "iteration_id": iteration_id,
                            "description": pic.description,
                            "tags": pic.tags,
                            "created_at": pic.created_at,
                        }
                    )
            return {"reference_pictures": results}

        @self.app.post("/characters/reference_pictures")
        async def add_reference_picture(
            character_id: str = Form(...),
            description: str = Form(None),
            tags: str = Form(None),
            image: UploadFile = File(...),
        ):
            """
            Add a reference picture for a character. Creates a new Picture with is_reference=1 and a master iteration.
            """
            tags_list = json.loads(tags) if tags else []
            img_bytes = await image.read()
            pic_id = str(uuid.uuid4())
            picture = Picture(
                id=pic_id,
                character_id=character_id,
                description=description,
                tags=tags_list,
                is_reference=1,
            )
            dest_folder = self.vault.get_image_root()
            _, iteration = PictureIteration.create_from_bytes(
                image_root_path=dest_folder,
                image_bytes=img_bytes,
                picture_id=pic_id,
                is_master=True,
            )
            self.vault.pictures.import_pictures([picture])
            self.vault.iterations.import_iterations([iteration])
            return {
                "picture_id": pic_id,
                "iteration_id": iteration.id,
                "description": description,
                "tags": tags_list,
            }

        @self.app.patch("/characters/{id}")
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
            if description is not None and description != char.description:
                char.description = description
                updated = True
            if updated:
                self.vault.characters.update(char)
            return {"status": "success", "character": char.__dict__}

        @self.app.delete("/characters/{id}")
        def delete_character(id: int):
            # Remove character_id from all pictures and picture_iterations
            cursor = self.vault.connection.cursor()
            cursor.execute(
                "UPDATE pictures SET character_id = NULL WHERE character_id = ?", (id,)
            )
            cursor.execute(
                "UPDATE picture_iterations SET character_id = NULL WHERE character_id = ?",
                (id,),
            )
            self.vault.connection.commit()
            # Delete the character
            try:
                self.vault.characters.delete(id)
            except KeyError:
                raise HTTPException(status_code=404, detail="Character not found")
            return {"status": "success", "deleted_id": id}

        @self.app.get("/characters")
        def get_characters(name: str = Query(None)):
            """List all characters or filter by name."""
            chars = (
                self.vault.characters.find(name=name)
                if name
                else self.vault.characters.list()
            )
            return [c.__dict__ for c in chars]

        @self.app.post("/characters")
        def create_character(
            name: str = Body(...),
            description: str = Body(None),
        ):
            from pixelurgy_vault.characters import Character

            char = Character(id=None, name=name, description=description)
            self.vault.characters.add(char)
            return {"status": "success", "character": char.__dict__}

        @self.app.get("/characters/{id}")
        def get_character_by_id(id: int):
            try:
                char = self.vault.characters[id]
            except KeyError:
                raise HTTPException(status_code=404, detail="Character not found")
            return char.__dict__

        @self.app.get("/iterations/{iteration_id}")
        async def get_iteration(iteration_id: str):
            import base64

            try:
                it = self.vault.iterations[iteration_id]
            except KeyError:
                raise HTTPException(status_code=404, detail="Iteration not found")
            # Base64 encode thumbnail if present
            thumbnail_b64 = (
                base64.b64encode(it.thumbnail).decode("ascii") if it.thumbnail else None
            )
            logger.debug(f"Serving iteration {iteration_id} with score {it.score}")
            return {
                "id": it.id,
                "picture_id": it.picture_id,
                "file_path": it.file_path,
                "format": it.format,
                "width": it.width,
                "height": it.height,
                "size_bytes": it.size_bytes,
                "created_at": it.created_at,
                "is_master": it.is_master,
                "derived_from": it.derived_from,
                "transform_metadata": it.transform_metadata,
                "thumbnail": thumbnail_b64,
                "quality": it.quality.__dict__ if it.quality else None,
                "score": it.score,
                "pixel_sha": getattr(it, "pixel_sha", None),
            }

        @self.app.post("/iterations/")
        async def upload_iteration(
            picture_id: str = Body(...),
            file: UploadFile = File(None),
            file_path: str = Body(None),
            is_master: int = Body(0),
            derived_from: str = Body(None),
            transform_metadata: str = Body(None),
        ):
            # Check that picture_id exists
            try:
                _ = self.vault.pictures[picture_id]
            except KeyError:
                raise HTTPException(status_code=404, detail="picture_id does not exist")

            dest_folder = self.vault.get_image_root()
            os.makedirs(dest_folder, exist_ok=True)

            if file is not None:
                img_bytes = await file.read()
                _, iteration = PictureIteration.create_from_bytes(
                    image_root_path=dest_folder,
                    image_bytes=img_bytes,
                    picture_id=picture_id,
                    derived_from=derived_from,
                    transform_metadata=transform_metadata,
                    is_master=bool(is_master),
                )
            elif file_path:
                _, iteration = PictureIteration.create_from_file(
                    image_root_path=dest_folder,
                    source_file_path=file_path,
                    picture_id=picture_id,
                    derived_from=derived_from,
                    transform_metadata=transform_metadata,
                    is_master=bool(is_master),
                )
            else:
                raise HTTPException(
                    status_code=400, detail="No file upload or file_path provided"
                )

            self.vault.iterations.import_iterations([iteration])
            return {"status": "success", "iteration_id": iteration.id}

        @self.app.get("/")
        def read_root():
            version = self.get_version()
            return {"message": "Pixelurgy Vault REST API", "version": version}

        @self.app.get("/pictures/{id}")
        def get_picture(
            id: str, info: bool = Query(False), embedding: bool = Query(False)
        ):
            if not isinstance(id, str):
                logger.error(f"Invalid id type: {type(id)} value: {id}")
                raise HTTPException(status_code=404, detail="Invalid picture id")
            try:
                pic = self.vault.pictures[id]
            except KeyError:
                logger.error(f"Picture not found for id={id}")
                raise HTTPException(status_code=404, detail="Picture not found")
            if info:
                # Return metadata only
                result = {
                    "id": pic.id,
                    "character_id": pic.character_id,
                    "description": pic.description,
                    "tags": pic.tags,
                    "created_at": pic.created_at,
                    "has_embedding": pic.has_embedding,
                }
                return result
            # Otherwise, deliver the master iteration image file
            logger.debug(f"Fetching master iteration for picture id={pic.id}")
            master_its = self.vault.iterations.find(picture_id=pic.id, is_master=1)
            logger.debug(
                f"Found a master iteration with score {master_its[0].score if master_its else 'N/A'}"
            )
            if not master_its:
                logger.error(f"Master iteration not found for picture id={pic.id}")
                raise HTTPException(
                    status_code=404, detail="Master iteration not found"
                )
            it = master_its[0]
            if not it.file_path or not os.path.isfile(it.file_path):
                logger.error(
                    f"File path missing or does not exist for iteration id={it.id}, file_path={it.file_path}"
                )
                raise HTTPException(
                    status_code=404, detail=f"File not found for iteration id={it.id}"
                )
            return FileResponse(it.file_path)

        @self.app.get("/thumbnails/{id}")
        async def get_thumbnail(id: str):
            try:
                pic = self.vault.pictures[id]
            except KeyError:
                logger.error(f"Picture not found for id={id} (thumbnail request)")
                raise HTTPException(status_code=404, detail="Picture not found")

            master_its = self.vault.iterations.find(picture_id=pic.id, is_master=1)
            if not master_its:
                logger.error(
                    f"Master iteration not found for picture id={pic.id} (thumbnail request)"
                )
                raise HTTPException(
                    status_code=404, detail="Master iteration not found"
                )
            thumbnail_bytes = master_its[0].thumbnail
            if not thumbnail_bytes:
                logger.error(f"No thumbnail available for picture id={pic.id}")
                raise HTTPException(status_code=404, detail="No thumbnail available")
            return Response(content=thumbnail_bytes, media_type="image/png")

        @self.app.patch("/pictures/{id}")
        async def patch_picture(id: str, request: Request):
            """
            Update fields of a picture using query parameters, e.g., /pictures/{id}?score=5
            If 'score' is provided, update the master iteration's score.
            Otherwise, update fields on the picture.
            Also supports JSON body for updating tags: {"tags": ["tag1", ...]}
            """
            params = dict(request.query_params)
            # If PATCH is called with a JSON body, use it for tags
            content_type = request.headers.get("content-type", "")
            json_body = None
            if "application/json" in content_type:
                try:
                    json_body = await request.json()
                except Exception:
                    json_body = None
            # Handle score update for master iteration
            if params.get("score") is not None:
                try:
                    score_val = int(params["score"])
                except Exception:
                    raise HTTPException(status_code=400, detail="Invalid score value")
                master_its = self.vault.iterations.find(picture_id=id, is_master=1)
                if not master_its:
                    raise HTTPException(
                        status_code=404, detail="Master iteration not found"
                    )
                master_it = master_its[0]
                master_it.score = score_val
                self.vault.iterations.import_iterations([master_it])
                return {
                    "status": "success",
                    "iteration_id": master_it.id,
                    "score": score_val,
                }
            # Otherwise, update fields on the picture
            try:
                pic = self.vault.pictures[id]
            except KeyError:
                raise HTTPException(status_code=404, detail="Picture not found")
            updated = False
            # If tags are provided in JSON body, replace tags
            if json_body and "tags" in json_body:
                tags = json_body["tags"]
                if not isinstance(tags, list):
                    raise HTTPException(status_code=400, detail="tags must be a list")
                pic.tags = tags
                updated = True
            # Otherwise, update fields from query params
            for key, value in params.items():
                if key == "score":
                    continue
                try:
                    cast_val = int(value)
                except Exception:
                    cast_val = value
                if hasattr(pic, key):
                    setattr(pic, key, cast_val)
                    updated = True
                # If updating character_id, also update all iterations
                if key == "character_id":
                    # Log character id and name
                    char_name = None
                    try:
                        char_obj = self.vault.characters[cast_val]
                        char_name = getattr(char_obj, "name", None)
                    except Exception:
                        char_name = None
                    logger.info(
                        f"[PATCH] Assigning picture {id} to character_id={cast_val}, name={char_name}"
                    )
                    cursor = self.vault.connection.cursor()
                    cursor.execute(
                        "UPDATE picture_iterations SET character_id = ? WHERE picture_id = ?",
                        (cast_val, id),
                    )
                    self.vault.connection.commit()
            if updated:
                self.vault.pictures.update_pictures([pic])
            return {"status": "success", "picture": pic.__dict__}

        @self.app.get("/favicon.ico")
        def favicon():
            favicon_path = os.path.join(os.path.dirname(__file__), "favicon.ico")
            return FileResponse(favicon_path)

        @self.app.post("/check_hashes")
        async def check_hashes(hashes: list = Body(...)):
            existing = [h for h in hashes if h in self.vault.iterations]
            return {"existing": existing}

        @self.app.post("/pictures")
        async def import_pictures(
            request: Request,
            character_id: str = Form(None),
            description: str = Form(None),
            tags: str = Form(None),
            image: UploadFile = File(None),
            file_path: str = Form(None),
            recursive: bool = Form(False),
        ):
            """
            Import new pictures with master iterations. Accepts:
            - image: bytes upload (single file)
            - file_path: path to file or directory (if directory, imports all images recursively if recursive=True)
            """

            dest_folder = self.vault.get_image_root()
            os.makedirs(dest_folder, exist_ok=True)
            tags_list = json.loads(tags) if tags else []
            results = []
            files_to_import = []
            # Collect files to import
            if image is not None:
                img_bytes = await image.read()
                files_to_import.append((img_bytes, None))
            elif file_path:
                if os.path.isdir(file_path):
                    for root, _, files in os.walk(file_path):
                        for fname in files:
                            fpath = os.path.join(root, fname)
                            with open(fpath, "rb") as f:
                                files_to_import.append((f.read(), fpath))
                        if not recursive:
                            break
                else:
                    with open(file_path, "rb") as f:
                        files_to_import.append((f.read(), file_path))
            else:
                raise HTTPException(
                    status_code=400, detail="No image or file_path provided"
                )

            new_pictures = []
            new_iterations = []
            for img_bytes, src_path in files_to_import:
                # Calculate SHA for deduplication
                sha = (
                    PictureIteration.calculate_hash_from_file_path(src_path)
                    if src_path
                    else PictureIteration.create_from_bytes(
                        dest_folder, img_bytes, "temp"
                    )[1].id
                )
                # Check for existing iteration
                try:
                    _ = self.vault.iterations[sha]
                    results.append(
                        {
                            "status": "error",
                            "reason": "duplicate iteration",
                            "sha": sha,
                            "file": src_path,
                        }
                    )
                    continue
                except KeyError:
                    pass
                # Create new Picture and master iteration
                pic_id = str(uuid.uuid4())
                picture = Picture(
                    id=pic_id,
                    character_id=character_id,
                    description=description,
                    tags=tags_list,
                )
                _, iteration = PictureIteration.create_from_bytes(
                    image_root_path=dest_folder,
                    image_bytes=img_bytes,
                    picture_id=pic_id,
                    is_master=True,
                )
                new_pictures.append(picture)
                new_iterations.append(iteration)
                results.append(
                    {
                        "status": "success",
                        "picture_id": pic_id,
                        "iteration_id": iteration.id,
                        "file": src_path,
                    }
                )
            # Import all at once
            if new_pictures:
                self.vault.pictures.import_pictures(new_pictures)
            if new_iterations:
                self.vault.iterations.import_iterations(new_iterations)
            return {"results": results}

        @self.app.get("/pictures")
        async def list_pictures(
            request: Request,
            info: bool = Query(False),
            sort: str = Query("unsorted"),
            offset: int = Query(0),
            limit: int = Query(100),
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
                pics = self.vault.pictures.find_by_text(query, top_n=offset + limit)
                pics = pics[offset : offset + limit]
            else:
                pics = self.vault.pictures.find(**query_params)
                # Batch fetch all master iteration scores for sorting if needed
                if sort in [
                    SortMechanism.SCORE_DESC.value,
                    SortMechanism.SCORE_ASC.value,
                ]:
                    pic_ids = [pic.id for pic in pics]
                    score_map = {}
                    if pic_ids:
                        cursor = self.vault.connection.cursor()
                        qmarks = ",".join(["?"] * len(pic_ids))
                        cursor.execute(
                            f"SELECT picture_id, score FROM picture_iterations WHERE is_master=1 AND picture_id IN ({qmarks})",
                            tuple(pic_ids),
                        )
                        for row in cursor.fetchall():
                            score_map[row[0]] = row[1]
                    for pic in pics:
                        setattr(pic, "_score", score_map.get(pic.id))
                    reverse = sort == SortMechanism.SCORE_DESC.value
                    pics.sort(
                        key=lambda p: (
                            p._score if p._score is not None else float("-inf")
                        ),
                        reverse=reverse,
                    )
                elif sort in [
                    SortMechanism.DATE_DESC.value,
                    SortMechanism.DATE_ASC.value,
                ]:
                    reverse = sort == SortMechanism.DATE_DESC.value
                    pics.sort(key=lambda p: p.created_at or "", reverse=reverse)
                # else: unsorted
                pics = pics[offset : offset + limit]

            if info:
                # Batch fetch all master iteration scores for these pictures
                pic_ids = [pic.id for pic in pics]
                score_map = {}
                if pic_ids:
                    cursor = self.vault.connection.cursor()
                    qmarks = ",".join(["?"] * len(pic_ids))
                    cursor.execute(
                        f"SELECT picture_id, score FROM picture_iterations WHERE is_master=1 AND picture_id IN ({qmarks})",
                        tuple(pic_ids),
                    )
                    for row in cursor.fetchall():
                        score_map[row[0]] = row[1]
                result = []
                for pic in pics:
                    score = score_map.get(pic.id)
                    result.append(
                        {
                            "id": pic.id,
                            "character_id": pic.character_id,
                            "description": pic.description,
                            "tags": pic.tags,
                            "created_at": pic.created_at,
                            "score": score,
                            "is_reference": getattr(pic, "is_reference", 0),
                        }
                    )
                return result
            else:
                # Return the master iteration for each picture (is_master=1)
                results = []
                for pic in pics:
                    master_its = self.vault.iterations.find(
                        picture_id=pic.id, is_master=1
                    )
                    if master_its:
                        it = master_its[0]
                        results.append(
                            {
                                "id": it.id,
                                "picture_id": it.picture_id,
                                "file_path": it.file_path,
                                "format": it.format,
                                "width": it.width,
                                "height": it.height,
                                "size_bytes": it.size_bytes,
                                "created_at": it.created_at,
                                "is_master": it.is_master,
                                "derived_from": it.derived_from,
                                "transform_metadata": it.transform_metadata,
                                "thumbnail": it.thumbnail,
                                "quality": it.quality.__dict__ if it.quality else None,
                                "score": it.score,
                                "pixel_sha": getattr(it, "pixel_sha", None),
                            }
                        )
                return results

        @self.app.delete("/pictures/{id}")
        async def delete_picture(id: str):
            """
            Delete a picture by id, remove all its iterations, and delete all associated files from the file system and database.
            """

            # 1. Check if picture exists
            try:
                self.vault.pictures[id]
            except KeyError:
                logger.error(f"Picture not found for id={id} (delete request)")
                raise HTTPException(status_code=404, detail="Picture not found")

            # 2. Find all iterations for this picture
            iterations = self.vault.iterations.find(picture_id=id)
            # 3. Delete all files for each iteration
            errors = []
            for it in iterations:
                # Delete image file
                if it.file_path and os.path.exists(it.file_path):
                    try:
                        os.remove(it.file_path)
                    except Exception as e:
                        logger.error(f"Failed to delete file {it.file_path}: {e}")
                        errors.append(f"Failed to delete file {it.file_path}: {e}")
                # Delete thumbnail if stored as a separate file (not in DB)
                # (Currently, thumbnail is stored in DB as bytes, so nothing to do)

            # 4. Delete all iterations from DB
            cursor = self.vault.iterations._connection.cursor()
            cursor.execute("DELETE FROM picture_iterations WHERE picture_id = ?", (id,))
            self.vault.iterations._connection.commit()

            # 5. Delete the picture from DB
            del self.vault.pictures[id]

            return {
                "status": "success",
                "deleted_picture_id": id,
                "deleted_iterations": [it.id for it in iterations],
                "errors": errors,
            }

        @self.app.get("/category/summary")
        def get_category_summary(character_id: str = Query(None)):
            """
            Return summary statistics for a single category:
            - If character_id is omitted: all pictures
            - If character_id is null/None/empty: unassigned pictures
            - If character_id is set: that character's pictures
            """

            cursor = self.vault.connection.cursor()
            # Determine which set to query
            if character_id is None:
                # All pictures
                cursor.execute("SELECT * FROM pictures")
                char_id = None
            elif (
                character_id == ""
                or character_id.lower() == "none"
                or character_id == "null"
            ):
                # Unassigned
                cursor.execute("SELECT * FROM pictures WHERE character_id IS NULL")
                char_id = None
            else:
                cursor.execute(
                    "SELECT * FROM pictures WHERE character_id = ?", (character_id,)
                )
                char_id = character_id
            pics = cursor.fetchall()
            image_count = len(pics)
            reference_image_count = sum(1 for p in pics if p["is_reference"] == 1)
            last_updated = max(
                (p["created_at"] for p in pics if p["created_at"]), default=None
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


def main():
    global CONFIG_PATH, APP_NAME
    parser = argparse.ArgumentParser(description=f"Run the {APP_NAME}.")
    parser.add_argument(
        "--port", type=int, default=9537, help="Port to run the server on."
    )
    parser.add_argument(
        "--config", type=str, default=CONFIG_PATH, help="Path to server config file."
    )
    parser.add_argument(
        "--log-file", type=str, default=None, help="Path to server log file."
    )
    args = parser.parse_args()
    print(args)

    # If --config is provided, use it
    config_path = args.config

    server = Server(config_path=config_path, log_file=args.log_file)

    uvicorn.run(server.app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
