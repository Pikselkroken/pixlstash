from fastapi import FastAPI, Body, Query
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
    def __init__(self):
        self.config = self.init_config()
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
        def import_picture(
            file_path: str = Body(...),
            character_id: str = Body(...),
            title: str = Body(...),
            description: str = Body(None),
            tags: list = Body(None),
        ):
            # Determine extension
            ext = os.path.splitext(file_path)[1]
            # Destination folder: image_root/character_id
            dest_folder = os.path.join(self.vault.get_image_root(), character_id)
            os.makedirs(dest_folder, exist_ok=True)
            dest_filename = f"{title}{ext}"
            dest_path = os.path.join(dest_folder, dest_filename)
            shutil.copy2(file_path, dest_path)
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
        def list_pictures():
            pics = self.vault.pictures.find()
            return [
                {
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
