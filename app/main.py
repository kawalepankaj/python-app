from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

# 1. Config & Environment Configuration
APP_NAME = os.getenv("APP_NAME", "sample-fastapi-app")
APP_ENV = os.getenv("APP_ENV", "local")
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# 2. Global Logging Setup (moved outside lifespan for global coverage)
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# 3. Prometheus Metrics Setup
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests.",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ["method", "path"],
)


class AppInfo(BaseModel):
    name: str
    environment: str
    version: str
    status: str


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "starting app=%s env=%s version=%s",
        APP_NAME,
        APP_ENV,
        APP_VERSION,
    )
    yield


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if APP_ENV != "production" else None,
    redoc_url=None,
)


@app.middleware("http")
async def metrics_and_security_headers(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    
    # FIX: Safely parse route path and prevent metric explosion on 404s
    route = request.scope.get("route")
    if route and hasattr(route, "path"):
        path = route.path
    else:
        path = "not_found"

    REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
    REQUEST_LATENCY.labels(request.method, path).observe(duration)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/", response_model=AppInfo)
async def root() -> AppInfo:
    return AppInfo(
        name=APP_NAME,
        environment=APP_ENV,
        version=APP_VERSION,
        status="running",
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {
        "status": "ready",
        "app": APP_NAME,
        "environment": APP_ENV,
    }


# FIX: Removed 'async' because generate_latest() is blocking
@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)