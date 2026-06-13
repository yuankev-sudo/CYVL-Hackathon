"""FastAPI entry point."""
from dotenv import load_dotenv
load_dotenv()  # loads .env before any module reads os.getenv

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from api.routes import router

app = FastAPI(title="ClearPath", version="0.1.0")
app.include_router(router, prefix="/api")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")

@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")
