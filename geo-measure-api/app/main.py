"""FastAPI application entrypoint."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import files
from app.config import settings
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(
    title="Geospatial File Measurement API",
    description="Upload a Shapefile (.zip) or KML and get area / length measurements per feature.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(files.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
