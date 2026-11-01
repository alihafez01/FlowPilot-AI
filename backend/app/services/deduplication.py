import hashlib
import math
import re
import unicodedata

from urllib.parse import urlsplit

from rapidfuzz import fuzz


EARTH_RADIUS_KM = 6371.0088
BRANCH_MATCH_RADIUS_KM = 0.15
STREET_TERMS = {
    "street", "st", "road", "rd", "avenue", "ave",
    "boulevard", "blvd", "lane", "ln", "drive", "dr",
    "court", "ct", "way", "highway", "hwy", "place", "pl",
}


def normalize_company_name(name: str) -> str:
    if not name:
        return ""

    text = unicodedata.normalize("NFKC", name)
    text = text.casefold()
    text = re.sub(r"&", " and ", text)
    text = re.sub(
        r"\b(incorporated|inc|llc|ltd|limited|corp|corporation)\b",
        " ",
        text,
    )
    text = re.sub(r"[^a-z0-9]+", " ", text)

    return " ".join(text.split())


def normalize_location(location: str | None) -> str:
    if not location:
        return ""

    text = unicodedata.normalize("NFKC", location)
    text = text.casefold()

    return " ".join(
        re.sub(r"[^a-z0-9]+", " ", text).split()
    )


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None

    raw = value.strip().lower()

    if not raw:
        return None

    try:
        parsed = urlsplit(
            raw if "://" in raw else f"https://{raw}"
        )
        host = parsed.hostname
    except ValueError:
        return None

    if (
        not host
        or any(char.isspace() for char in host)
        or ".." in host
    ):
        return None

    if parsed.username or parsed.password:
        return None

    host = host.rstrip(".")

    if host.startswith("www."):
        host = host[4:]

    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None

    return host or None


def create_identity_key(
    name: str,
    website: str | None = None,
    provider: str | None = None,
    provider_id: str | None = None,
    location: str | None = None,
) -> str:
    """Create an exact identity key using the strongest available identifier."""
    domain = normalize_domain(website)

    if domain:
        return f"domain:{domain}"

    if provider and provider_id:
        return f"{provider.casefold()}:{provider_id}"

    normalized_name = normalize_company_name(name)
    normalized_place = normalize_location(location)

    if not normalized_name or not normalized_place:
        raise ValueError(
            "Name and location are required when no stable ID exists."
        )

    raw_key = f"{normalized_name}|{normalized_place}"
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    return f"name_location:{digest}"


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def coordinate_distance_km(
    latitude_a,
    longitude_a,
    latitude_b,
    longitude_b,
) -> float | None:
    """Return great-circle distance; return None for incomplete/invalid points."""
    lat1 = _as_float(latitude_a)
    lon1 = _as_float(longitude_a)
    lat2 = _as_float(latitude_b)
    lon2 = _as_float(longitude_b)

    if None in (lat1, lon1, lat2, lon2):
        return None

    if not (-90 <= lat1 <= 90 and -90 <= lat2 <= 90):
        return None
    if not (-180 <= lon1 <= 180 and -180 <= lon2 <= 180):
        return None

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(delta_lambda / 2) ** 2
    )
    central_angle = 2 * math.atan2(
        math.sqrt(haversine),
        math.sqrt(max(0.0, 1.0 - haversine)),
    )

    return EARTH_RADIUS_KM * central_angle


def _has_specific_address(value: str) -> bool:
    text = normalize_location(value)
    has_number = bool(re.search(r"\b\d{1,6}\b", text))
    has_street = any(
        re.search(rf"\b{term}\b", text)
        for term in STREET_TERMS
    )
    return has_number and has_street


def _location_match_reason(first: dict, second: dict) -> str | None:
    distance = coordinate_distance_km(
        first.get("latitude"),
        first.get("longitude"),
        second.get("latitude"),
        second.get("longitude"),
    )

    if distance is not None:
        if distance <= BRANCH_MATCH_RADIUS_KM:
            return (
                f"Coordinates are within {BRANCH_MATCH_RADIUS_KM * 1000:.0f} m "
                f"({distance * 1000:.1f} m apart)."
            )
        # Two known, distant locations are separate branches even when the
        # business name and city-level location text are identical.
        return None

    first_location = normalize_location(
        first.get("location_text") or first.get("location")
    )
    second_location = normalize_location(
        second.get("location_text") or second.get("location")
    )

    if (
        first_location
        and second_location
        and first_location == second_location
        and _has_specific_address(
            first.get("location_text") or first.get("location") or ""
        )
        and _has_specific_address(
            second.get("location_text") or second.get("location") or ""
        )
    ):
        return "Coordinates unavailable; normalized street addresses match."

    return None


def find_duplicate_candidates(
    companies: list[dict],
    threshold: float = 92.0,
) -> list[dict]:
    """Return fuzzy duplicate suggestions without merging any records.

    When both records have coordinates, only nearby records can be suggested.
    This prevents separate branches in the same city from being flagged as
    duplicates. If coordinates are missing, exact normalized location text is
    still required as a conservative fallback.
    """
    candidates = []

    for index, first in enumerate(companies):
        first_id = first.get("id")
        first_name = normalize_company_name(first.get("name", ""))

        if not first_name:
            continue

        for second in companies[index + 1:]:
            second_id = second.get("id")
            if first_id is not None and first_id == second_id:
                continue

            second_name = normalize_company_name(second.get("name", ""))
            if not second_name:
                continue

            location_reason = _location_match_reason(first, second)
            if not location_reason:
                continue

            score = fuzz.token_set_ratio(first_name, second_name)
            if score < threshold:
                continue

            candidates.append(
                {
                    "company_a_id": first_id,
                    "company_b_id": second_id,
                    "similarity_score": round(score, 2),
                    "match_reason": (
                        "Similar normalized company names; "
                        + location_reason
                    ),
                }
            )

    return candidates
