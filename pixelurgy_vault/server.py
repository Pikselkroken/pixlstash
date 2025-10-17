from fastapi import Body, FastAPI, File, Form, Request, UploadFile, Query
from fastapi.responses import FileResponse

import uvicorn
import os
import json
from platformdirs import user_config_dir
from pixelurgy_vault.vault import Vault
from pixelurgy_vault.picture import Picture
import shutil
from PIL import Image
import numpy as np
import io


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
                    "file_path": pic.file_path,
                    "character_id": pic.character_id,
                    "title": pic.title,
                    "description": pic.description,
                    "tags": pic.tags,
                    "width": pic.width,
                    "height": pic.height,
                    "format": pic.format,
                    "created_at": pic.created_at,
                    "quality": pic.quality.__dict__ if pic.quality else None,
                }
            # Otherwise, deliver the image file
            return FileResponse(pic.file_path)

        @self.app.get("/favicon.ico")
        def favicon():
            favicon_path = os.path.join(os.path.dirname(__file__), "favicon.ico")
            return FileResponse(favicon_path)

        @self.app.post("/pictures")
        async def import_picture(
            request: Request,
            file_path: str = Body(None),
            character_id: str = Body(None),
            description: str = Body(None),
            tags: list = Body(None),
            image: UploadFile = File(None),
            character_id_form: str = Form(None),
            description_form: str = Form(None),
            tags_form: str = Form(None),
        ):
            if character_id is None and character_id_form is None:
                return {"error": "character_id is required"}

            # Detect content type and dispatch
            content_type = request.headers.get("content-type", "")

            pictures = []

            character_id = character_id_form if character_id is None else character_id
            dest_folder = self.vault.get_image_root()
            description = description_form if description is None else description
            tags = json.loads(tags_form) if tags_form else []

            os.makedirs(dest_folder, exist_ok=True)

            if content_type.startswith("multipart/form-data") and image is not None:
                img_bytes = await image.read()
                pictures.append(
                    Picture.create_picture_from_bytes(
                        image_root_path=dest_folder,
                        image_bytes=img_bytes,
                        character_id=character_id,
                        description=description,
                        tags=tags,
                    )
                )
            elif file_path:
                # Handle local file path input
                source_paths = []
                if os.path.isdir(file_path):
                    # Handle directory of images
                    for entry in os.listdir(file_path):
                        full_path = os.path.join(file_path, entry)
                        source_paths.append(full_path)
                else:
                    source_paths.append(file_path)

                for file_path in source_paths:
                    pictures.append(
                        Picture.create_picture_from_file(
                            image_root_path=dest_folder,
                            file_path=file_path,
                            character_id=character_id,
                            description=description,
                            tags=tags,
                        )
                    )
            else:
                return {"error": "No image or file_path provided"}

            self.vault.pictures.import_pictures(pictures)
            return {
                "status": "success",
                "ids": [pic.id for pic in pictures],
                "file_paths": [pic.file_path for pic in pictures],
            }

        @self.app.get("/pictures")
        async def list_pictures(request: Request):
            # Collect query parameters for filtering
            query_params = dict(request.query_params)
            # Convert tags to list if present
            if "tags" in query_params and isinstance(query_params["tags"], str):
                import json

                try:
                    query_params["tags"] = json.loads(query_params["tags"])
                except Exception:
                    query_params["tags"] = [query_params["tags"]]
            pics = self.vault.pictures.find(**query_params)
            return [
                {
                    "id": pic.id,
                    "file_path": pic.file_path,
                    "character_id": pic.character_id,
                    "description": pic.description,
                    "tags": pic.tags,
                    "width": pic.width,
                    "height": pic.height,
                    "format": pic.format,
                    "created_at": pic.created_at,
                    "quality": pic.quality.__dict__ if pic.quality else None,
                }
                for pic in pics
            ]

    def get_version(self):
        import os

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
