from fastapi import FastAPI
from fastapi.responses import FileResponse
import uvicorn
import os
import json
from platformdirs import user_config_dir
from pixelurgy_vault.vault import Vault

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
            return {"message": "Pixelurgy Vault REST API"}

        @self.app.get("/favicon.ico")
        def favicon():
            favicon_path = os.path.join(os.path.dirname(__file__), "favicon.ico")
            return FileResponse(favicon_path)


if __name__ == "__main__":
    server = Server()
    uvicorn.run(server.app, host="127.0.0.1", port=8765)
