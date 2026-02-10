import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.config import settings
from app.routes import upload, audit, export, ws, inspection


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.upload_path  # ensure uploads dir exists
    await init_db()
    yield


app = FastAPI(title="AMIA – Automated Mechanical Inspection Auditor", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(inspection.router, prefix="/api")
app.include_router(ws.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
