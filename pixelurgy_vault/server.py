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
            title: str = Body(None),
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
            if content_type.startswith("multipart/form-data") and image is not None:
                # Handle image upload
                ext = os.path.splitext(image.filename)[1]
                character_id = (
                    character_id_form if character_id is None else character_id
                )
                description = description_form if description is None else description
                tags = json.loads(tags_form) if tags_form else []
                dest_folder = os.path.join(self.vault.get_image_root(), character_id)
                os.makedirs(dest_folder, exist_ok=True)
                dest_filename = (
                    f"{os.path.splitext(os.path.basename(image.filename))[0]}{ext}"
                )
                dest_path = os.path.join(dest_folder, dest_filename)
                with open(dest_path, "wb") as f:
                    f.write(await image.read())
            elif file_path:
                # Handle file path string
                ext = os.path.splitext(file_path)[1]
                dest_folder = os.path.join(self.vault.get_image_root(), character_id)
                os.makedirs(dest_folder, exist_ok=True)
                dest_filename = (
                    f"{os.path.splitext(os.path.basename(file_path))[0]}{ext}"
                )
                dest_path = os.path.join(dest_folder, dest_filename)
                shutil.copy2(file_path, dest_path)
            else:
                return {"error": "No image or file_path provided"}

            # Calculate width, height, and format automatically
            with Image.open(dest_path) as img:
                width, height = img.size
                format = img.format
            # Create Picture object
            pic = Picture(
                file_path=dest_path,
                character_id=character_id,
                title=title,
                description=description,
                tags=tags,
                width=width,
                height=height,
                format=format,
            )
            self.vault.pictures.import_picture(pic)
            return {"status": "success", "id": pic.id, "file_path": dest_path}

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
