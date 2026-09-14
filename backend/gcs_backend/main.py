"""FastAPI app: lifespan khởi động các task nền (mục 7.1).

Chạy:  uvicorn gcs_backend.main:app --port 8000
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.rest import router, ws_router
from .config import get_settings
from .runtime import ApiError, Runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    rt = Runtime(get_settings())
    app.state.rt = rt
    await rt.start()
    try:
        yield
    finally:
        await rt.stop()


app = FastAPI(title="GCS ESP-NOW 3D", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.message, "data": exc.detail})


app.include_router(router)
app.include_router(ws_router)

# Phục vụ frontend đã build (web/dist) nếu có — một tiến trình duy nhất khi vận hành
_dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "web", "dist"))
if os.path.isdir(_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(_dist, "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def _spa(path: str):
        f = os.path.join(_dist, path)
        return FileResponse(f if path and os.path.isfile(f) else os.path.join(_dist, "index.html"))
