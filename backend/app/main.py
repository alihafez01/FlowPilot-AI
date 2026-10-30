import json
import logging
import os
import urllib.error
import urllib.request
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from app.schemas.discovery import ICPConfig
from app.services.discovery import (
    DiscoveryServiceError,
    execute_discovery,
    get_discovery_run,
    list_companies,
    list_company_sources,
)
from app.services.osm import OSMError


APP_NAME = "FlowPilot AI API"
APP_VERSION = os.getenv("APP_VERSION", "0.1.0")

DATABASE_URL = os.environ["DATABASE_URL"]

OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://ollama:11434",
).rstrip("/")

OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "qwen2.5:1.5b",
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("flowpilot")

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=(
        "Local-first AI business automation backend "
        "with evidence-based company discovery."
    ),
)


@app.get("/", tags=["system"])
def root() -> dict:
    return {
        "service": APP_NAME,
        "version": APP_VERSION,
        "docs": "/docs",
        "health": "/health/live",
    }


@app.get("/health/live", tags=["health"])
def liveness() -> dict:
    return {
        "status": "ok",
        "service": "flowpilot-api",
        "version": APP_VERSION,
    }


@app.get("/health/ready", tags=["health"])
def readiness():
    try:
        with psycopg.connect(
            DATABASE_URL,
            connect_timeout=3,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT to_regclass('public.companies')"
                )
                companies_table = cursor.fetchone()[0]

        if companies_table is None:
            raise HTTPException(
                status_code=503,
                detail="database_schema_not_initialized",
            )

        return {
            "status": "ready",
            "database": "connected",
            "schema": "initialized",
        }

    except HTTPException:
        raise

    except psycopg.Error:
        logger.warning("Database readiness check failed.")
        raise HTTPException(
            status_code=503,
            detail="database_unavailable",
        ) from None


@app.get("/health/ollama", tags=["health"])
def ollama_status():
    request = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/tags",
        headers={"Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.load(response)

    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        OSError,
    ):
        raise HTTPException(
            status_code=503,
            detail="ollama_unavailable",
        ) from None

    model_names = {
        item.get("name")
        for item in payload.get("models", [])
        if isinstance(item, dict)
    }

    model_available = OLLAMA_MODEL in model_names

    return JSONResponse(
        status_code=200 if model_available else 503,
        content={
            "service": "ollama",
            "connected": True,
            "configured_model": OLLAMA_MODEL,
            "model_available": model_available,
            "installed_models": sorted(model_names),
        },
    )


@app.post(
    "/api/v1/discovery/search",
    tags=["discovery"],
)
def discover_companies(icp: ICPConfig):
    """
    Search for candidate companies, extract selected website pages,
    and persist results and sources.
    """
    try:
        return execute_discovery(icp)

    except DiscoveryServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.message,
        ) from None

    except OSMError as exc:
        logger.warning("OSM discovery failed: %s", str(exc))
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from None


@app.get(
    "/api/v1/discovery/runs/{run_id}",
    tags=["discovery"],
)
def read_discovery_run(run_id: UUID):
    try:
        result = get_discovery_run(run_id)
    except psycopg.Error:
        logger.exception("Could not read discovery run.")
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve discovery run.",
        ) from None

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Discovery run not found.",
        )

    return result


@app.get(
    "/api/v1/companies",
    tags=["companies"],
)
def read_companies(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    website_validation_status: str | None = Query(
        default=None,
        pattern="^(candidate|likely_official|needs_review|rejected)$",
    ),
    website_status: str | None = Query(
        default=None,
        pattern="^(with_website|without_website_candidate|unknown)$",
    ),
):
    try:
        items = list_companies(
            limit=limit,
            offset=offset,
            validation_status=website_validation_status,
            website_status=website_status,
        )

        return {
            "count": len(items),
            "limit": limit,
            "offset": offset,
            "companies": items,
        }

    except psycopg.Error:
        logger.exception("Could not retrieve companies.")
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve companies.",
        ) from None


@app.get(
    "/api/v1/companies/{company_id}/sources",
    tags=["companies"],
)
def read_company_sources(
    company_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
):
    try:
        sources = list_company_sources(
            company_id=company_id,
            limit=limit,
        )

        return {
            "company_id": str(company_id),
            "count": len(sources),
            "sources": sources,
        }

    except psycopg.Error:
        logger.exception("Could not retrieve company sources.")
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve company sources.",
        ) from None