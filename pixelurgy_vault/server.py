from fastapi import FastAPI
from fastapi.responses import FileResponse
import uvicorn

import os

app = FastAPI()


@app.get("/")
def read_root():
    return {"message": "Pixelurgy Vault REST API"}


@app.get("/favicon.ico")
def favicon():
    favicon_path = os.path.join(os.path.dirname(__file__), "favicon.ico")
    return FileResponse(favicon_path)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
