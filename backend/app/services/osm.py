
import hashlib
import os
import re
import threading
import time

import httpx
import psycopg

from psycopg.types.json import Jsonb

import uuid


DATABASE_URL = os.environ["DATABASE_URL"]

NOMINATIM_URL = os.getenv(
    "OSM_NOMINATIM_URL",
    "https://nominatim.openstreetmap.org",
).rstrip("/")

OVERPASS_URL = os.getenv(
    "OSM_OVERPASS_URL",
    "https://overpass-api.de/api/interpreter",
)

USER_AGENT = os.getenv(
    "OSM_USER_AGENT",
    "FlowPilotAI/0.1",
)

_geocode_lock = threading.Lock()
_last_geocode_request = 0.0


class OSMError(Exception):
    pass


def get_cached(key: str, max_age_hours: int):
    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute(
            """
            SELECT payload
            FROM osm_cache
            WHERE cache_key = %s
              AND fetched_at > now() - make_interval(hours => %s)
            """,
            (key, max_age_hours),
        ).fetchone()

    return row[0] if row else None


def save_cached(key: str, payload: dict):
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            """
            INSERT INTO osm_cache(cache_key, payload)
            VALUES (%s, %s)
            ON CONFLICT(cache_key)
            DO UPDATE SET
                payload = EXCLUDED.payload,
                fetched_at = now()
            """,
            (key, Jsonb(payload)),
        )


def geocode_location(location: str) -> dict:
    global _last_geocode_request

    query_key = hashlib.sha256(
        location.strip().casefold().encode()
    ).hexdigest()

    cache_key = f"nominatim:{query_key}"

    cached = get_cached(cache_key, 24 * 30)

    if cached:
        return cached

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    # Respect the public Nominatim request interval.
    with _geocode_lock:
        elapsed = time.monotonic() - _last_geocode_request

        if elapsed < 1.1:
            time.sleep(1.1 - elapsed)

        try:
            response = httpx.get(
                f"{NOMINATIM_URL}/search",
                params={
                    "q": location,
                    "format": "jsonv2",
                    "limit": 1,
                    "addressdetails": 1,
                },
                headers=headers,
                timeout=20,
            )
            _last_geocode_request = time.monotonic()

        except httpx.RequestError:
            raise OSMError(
                "Nominatim location lookup failed."
            ) from None

    if response.status_code != 200:
        raise OSMError(
            f"Nominatim returned HTTP {response.status_code}."
        )

    data = response.json()

    if not data:
        raise OSMError(
            f"No geographic result found for: {location}"
        )

    item = data[0]

    try:
        south, north, west, east = map(
            float,
            item["boundingbox"],
        )
    except (KeyError, TypeError, ValueError):
        raise OSMError(
            "Nominatim returned an invalid bounding box."
        ) from None

    # Keep the initial discovery area local and manageable.
    if (
        north <= south
        or east <= west
        or north - south > 0.6
        or east - west > 0.6
    ):
        raise OSMError(
            "Choose a smaller city or locality instead of a "
            "country or large region."
        )

    result = {
        "display_name": item.get("display_name", location),
        "bbox": {
            "south": south,
            "north": north,
            "west": west,
            "east": east,
        },
    }

    save_cached(cache_key, result)

    return result


def build_overpass_query(bbox: dict) -> str:
    s = bbox["south"]
    w = bbox["west"]
    n = bbox["north"]
    e = bbox["east"]

    return f"""
    [out:json][timeout:25];

    (
      nwr["name"]["shop"]
        [!"website"][!"contact:website"][!"url"]
        ({s},{w},{n},{e});

      nwr["name"]["office"]
        [!"website"][!"contact:website"][!"url"]
        ({s},{w},{n},{e});

      nwr["name"]["craft"]
        [!"website"][!"contact:website"][!"url"]
        ({s},{w},{n},{e});
    );

    out center tags;
    """


def get_osm_businesses(location: str, max_results: int = 50):
    geo = geocode_location(location)
    bbox = geo["bbox"]

    query_key = hashlib.sha256(
        (
            location.casefold()
            + str(bbox)
            + str(max_results)
        ).encode()
    ).hexdigest()

    cache_key = f"overpass:{query_key}"

    cached = get_cached(cache_key, 24)

    if cached is not None:
        return cached["businesses"], geo["display_name"]

    query = build_overpass_query(bbox)

    try:
        response = httpx.post(
            OVERPASS_URL,
            data={"data": query},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            timeout=45,
        )
    except httpx.RequestError:
        raise OSMError(
            "Could not connect to the Overpass service."
        ) from None

    if response.status_code in (429, 406):
        raise OSMError(
            "Overpass rate limit reached. Pause before retrying."
        )

    if response.status_code != 200:
        raise OSMError(
            f"Overpass returned HTTP {response.status_code}."
        )

    try:
        payload = response.json()
    except ValueError:
        raise OSMError(
            "Overpass returned invalid JSON."
        ) from None

    businesses = []

    for element in payload.get("elements", []):
        tags = element.get("tags") or {}

        name = tags.get("name")

        if not name:
            continue

        # Defensive website check, even though the query
        # already excludes these tags.
        if (
            tags.get("website")
            or tags.get("contact:website")
            or tags.get("url")
        ):
            continue

        center = element.get("center", {})

        latitude = element.get("lat", center.get("lat"))
        longitude = element.get("lon", center.get("lon"))

        address_parts = [
            tags.get("addr:housenumber"),
            tags.get("addr:street"),
            tags.get("addr:city"),
            tags.get("addr:postcode"),
            tags.get("addr:country"),
        ]

        address = ", ".join(
            part for part in address_parts if part
        )

        category = (
            tags.get("shop")
            or tags.get("office")
            or tags.get("craft")
            or "local_business"
        )

        phone = (
            tags.get("contact:phone")
            or tags.get("phone")
        )

        public_email = (
            tags.get("contact:email")
            or tags.get("email")
        )

        source_url = (
            f"https://www.openstreetmap.org/"
            f"{element['type']}/{element['id']}"
        )

        description = (
            f"OpenStreetMap business category: {category}. "
            f"Location: {address or geo['display_name']}."
        )

        businesses.append(
            {
                "identity_key": (
                    f"osm:{element['type']}:{element['id']}"
                ),
                "name": name[:180],
                "category": category,
                "address": address or geo["display_name"],
                "latitude": latitude,
                "longitude": longitude,
                "phone": phone,
                "email": public_email,
                "source_url": source_url,
                "source_domain": "openstreetmap.org",
                "description": description,
                "metadata": tags,
            }
        )

    businesses = businesses[:max_results]

    save_cached(
        cache_key,
        {"businesses": businesses},
    )

    return businesses, geo["display_name"]




import uuid


def execute_osm_discovery(icp):
    location = icp.target_geographies[0]
    run_id = uuid.uuid4()

    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            """
            INSERT INTO discovery_runs (
                id,
                status,
                icp_config,
                queries
            )
            VALUES (%s, 'running', %s, %s)
            """,
            (
                run_id,
                Jsonb(icp.model_dump()),
                Jsonb([f"OpenStreetMap: {location}"]),
            ),
        )

    try:
        businesses, display_location = get_osm_businesses(
            location,
            max_results=50,
        )

        saved = []

        with psycopg.connect(DATABASE_URL) as conn:
            for business in businesses:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO companies (
                            name,
                            normalized_domain,
                            identity_key,
                            website_url,
                            industry,
                            description,
                            services,
                            source_urls,
                            website_validation_status,
                            website_status,
                            location_text,
                            latitude,
                            longitude,
                            phone
                        )
                        VALUES (
                            %s, NULL, %s, NULL, %s, %s,
                            %s, %s, 'needs_review',
                            'without_website_candidate',
                            %s, %s, %s, %s
                        )
                        ON CONFLICT (identity_key)
                        WHERE identity_key IS NOT NULL
                        DO UPDATE SET
                            name = EXCLUDED.name,
                            description = EXCLUDED.description,
                            location_text = EXCLUDED.location_text,
                            latitude = EXCLUDED.latitude,
                            longitude = EXCLUDED.longitude,
                            phone = COALESCE(
                                EXCLUDED.phone,
                                companies.phone
                            ),
                            updated_at = now()
                        RETURNING id
                        """,
                        (
                            business["name"],
                            business["identity_key"],
                            business["category"],
                            business["description"],
                            Jsonb([business["category"]]),
                            Jsonb([business["source_url"]]),
                            business["address"],
                            business["latitude"],
                            business["longitude"],
                            business["phone"],
                        ),
                    )

                    company_id = cursor.fetchone()[0]

                    cursor.execute(
                        """
                        INSERT INTO company_sources (
                            company_id,
                            discovery_run_id,
                            source_url,
                            source_domain,
                            source_query,
                            title,
                            snippet,
                            source_status,
                            extraction_status,
                            source_type,
                            metadata
                        )
                        VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, 'needs_review',
                            'not_requested', 'osm_listing', %s
                        )
                        ON CONFLICT (
                            company_id,
                            discovery_run_id,
                            source_url
                        )
                        DO NOTHING
                        """,
                        (
                            company_id,
                            run_id,
                            business["source_url"],
                            business["source_domain"],
                            f"OpenStreetMap: {location}",
                            business["name"],
                            business["description"],
                            Jsonb(business["metadata"]),
                        ),
                    )

                saved.append(
                    {
                        "id": str(company_id),
                        "name": business["name"],
                        "website_url": None,
                        "website_status": "without_website_candidate",
                        "location": business["address"],
                        "source_type": "osm_listing",
                        "source_url": business["source_url"],
                    }
                )

        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                """
                UPDATE discovery_runs
                SET status = 'completed',
                    results_count = %s,
                    companies_upserted = %s,
                    extracted_count = 0,
                    tavily_credits = 0,
                    finished_at = now()
                WHERE id = %s
                """,
                (
                    len(businesses),
                    len(saved),
                    run_id,
                ),
            )

        return {
            "run_id": str(run_id),
            "status": "completed",
            "provider": "openstreetmap",
            "location": display_location,
            "website_filter": "without_website",
            "source_results": len(businesses),
            "companies_upserted": len(saved),
            "tavily_credits": 0,
            "companies": saved,
        }

    except Exception:
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                """
                UPDATE discovery_runs
                SET status = 'failed',
                    finished_at = now(),
                    errors = %s
                WHERE id = %s
                """,
                (
                    Jsonb([
                        {
                            "stage": "osm_discovery",
                            "message": "OSM discovery execution failed."
                        }
                    ]),
                    run_id,
                ),
            )

        raise
