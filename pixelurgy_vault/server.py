from fastapi import Body, FastAPI, File, Form, Request, UploadFile, Query
from fastapi.responses import FileResponse

import uvicorn
import os
import json
import uuid

from platformdirs import user_config_dir
from pixelurgy_vault.vault import Vault
from pixelurgy_vault.picture import Picture
from pixelurgy_vault.picture_iteration import PictureIteration

APP_NAME = "pixelurgy-vault"
CONFIG_FILENAME = "config.json"


class Server:
    def __init__(self, vault_db_path=None, image_root=None, description=None):
        self.config = self.init_config()
        self.config["db_path"] = vault_db_path or self.config.get("db_path")
        self.config["image_root"] = image_root or self.config.get("image_root")
        self.config["description"] = description or self.config.get("description")
        self.vault = Vault(
            db_path=self.config["db_path"],
            image_root=self.config["image_root"],
            description=self.config["description"],
        )
        self.app = FastAPI()
        self.setup_routes()

    def init_config(self):
        config_dir = user_config_dir(APP_NAME)
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, CONFIG_FILENAME)
        if not os.path.exists(config_path):
            config = {
                "db_path": os.path.join(config_dir, "vault.db"),
                "image_root": os.path.join(config_dir, "images"),
                "description": "Pixelurgy Vault default configuration",
            }
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
        else:
            with open(config_path, "r") as f:
                config = json.load(f)
        return config

    def setup_routes(self):
        @self.app.get("/iterations/{iteration_id}")
        async def get_iteration(iteration_id: str):
            try:
                it = self.vault.pictures.get_iteration(iteration_id)
            except KeyError:
                return {"error": "Iteration not found"}
            # Return all fields as dict
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
                "thumbnail": it.thumbnail,
                "quality": it.quality.__dict__ if it.quality else None,
                "score": it.score,
                "pixel_sha": getattr(it, "pixel_sha", None),
            }

        @self.app.post("/iterations/")
        async def upload_iteration(
            picture_id: str = Body(...),
            file: UploadFile = File(...),
            is_master: int = Body(0),
            derived_from: str = Body(None),
            transform_metadata: str = Body(None),
        ):
            # Check that picture_id exists
            try:
                _ = self.vault.pictures[picture_id]
            except KeyError:
                return {"error": "picture_id does not exist"}
            # Read image bytes
            img_bytes = await file.read()
            # Use PictureIteration.create_from_bytes if available, else minimal fields

            dest_folder = self.vault.get_image_root()
            os.makedirs(dest_folder, exist_ok=True)
            # Save file to disk
            file_path = os.path.join(dest_folder, file.filename)
            with open(file_path, "wb") as f:
                f.write(img_bytes)

            iteration = PictureIteration(
                id=str(uuid.uuid4()),
                picture_id=picture_id,
                file_path=file_path,
                format=os.path.splitext(file.filename)[-1].lstrip("."),
                width=None,
                height=None,
                size_bytes=len(img_bytes),
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                is_master=is_master,
                derived_from=derived_from,
                transform_metadata=transform_metadata,
                thumbnail=None,
                quality=None,
                score=None,
                pixel_sha=None,
            )
            self.vault.iterations.import_iterations([iteration])
            return {"status": "success", "iteration_id": iteration.id}

        @self.app.get("/")
        def read_root():
            version = self.get_version()
            return {"message": "Pixelurgy Vault REST API", "version": version}

        @self.app.get("/pictures/{id}")
        def get_picture(id: str, info: bool = Query(False)):
            try:
                pic = self.vault.pictures[id]
            except KeyError:
                return {"error": "Picture not found"}
            if info:
                # Return metadata only
                return {
                    "id": pic.id,
                    "character_id": pic.character_id,
                    "description": pic.description,
                    "tags": pic.tags,
                    "created_at": pic.created_at,
                }
            # Otherwise, deliver the master iteration image file
            master_its = self.vault.iterations.find(picture_id=pic.id, is_master=1)
            if not master_its:
                return {"error": "Master iteration not found"}
            it = master_its[0]
            return FileResponse(it.file_path)

        @self.app.get("/favicon.ico")
        def favicon():
            favicon_path = os.path.join(os.path.dirname(__file__), "favicon.ico")
            return FileResponse(favicon_path)

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
                return {"error": "No image or file_path provided"}

            new_pictures = []
            new_iterations = []
            for img_bytes, src_path in files_to_import:
                # Calculate SHA for deduplication
                sha = (
                    PictureIteration.calculate_sha256_from_file_path(src_path)
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
        async def list_pictures(request: Request, info: bool = Query(False)):
            query_params = dict(request.query_params)
            # Remove 'info' from query_params if present
            query_params.pop("info", None)
            # Convert tags to list if present
            if "tags" in query_params and isinstance(query_params["tags"], str):
                import json

                try:
                    query_params["tags"] = json.loads(query_params["tags"])
                except Exception:
                    query_params["tags"] = [query_params["tags"]]
            pics = self.vault.pictures.find(**query_params)
            if info:
                # Return only Picture info (metadata)
                return [
                    {
                        "id": pic.id,
                        "character_id": pic.character_id,
                        "description": pic.description,
                        "tags": pic.tags,
                        "created_at": pic.created_at,
                    }
                    for pic in pics
                ]
            else:
                # Return the master iteration for each picture (is_master=1)
                results = []
                for pic in pics:
                    # Find master iteration for this picture
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


if __name__ == "__main__":
    server = Server()
    uvicorn.run(server.app, host="127.0.0.1", port=8765)

# Expose FastAPI app for testing
app = Server().app
