"""Conservative classification for search-result page types.

This module intentionally has no database or HTTP-client dependencies so the
classification rules can be tested independently.
"""

import re
from urllib.parse import urlsplit


DIRECTORY_HOSTS = {
    "agencies.semrush.com",
    "digitalagencynetwork.com",
    "builtinaustin.com",
    "themanifest.com",
}

LISTICLE_TITLE_PATTERNS = (
    r"\b(best|top|leading|directory|list of|ranked|verified picks?)\b.{0,120}\b(agencies|companies|firms|providers|businesses)\b",
    r"\b(agencies|companies|firms|providers)\s+in\s+[a-z][a-z .,-]{1,60}",
    r"\b\d+\s+(best|top)\b",
)


def _domain_is_in(domain: str, domains: set[str]) -> bool:
    return any(
        domain == item or domain.endswith("." + item)
        for item in domains
    )


def classify_page_kind(candidate: dict) -> str:
    """Return business_website, discovery_page, or directory_listing.

    The function errs on the side of not creating a lead from category pages.
    A business_website result is only a candidate; it is not proof of ownership
    or official status. That confidence is handled separately by discovery.
    """
    domain = (candidate.get("domain") or "").casefold()
    url = candidate.get("url") or ""
    path = urlsplit(url).path.casefold()
    title = candidate.get("title") or ""
    snippet = candidate.get("snippet") or ""
    combined = f"{title} {snippet}".casefold()

    if _domain_is_in(domain, DIRECTORY_HOSTS):
        if re.search(r"/(profile|company|agency)/[^/]+/?$", path):
            return "directory_listing"
        return "discovery_page"

    if domain == "inbeat.agency" and path.startswith("/blog/"):
        return "discovery_page"

    if any(
        re.search(pattern, combined, flags=re.IGNORECASE)
        for pattern in LISTICLE_TITLE_PATTERNS
    ):
        return "discovery_page"

    if re.search(
        r"/(directory|categories|category|companies/type|lists?)/",
        path,
    ):
        return "discovery_page"

    return "business_website"
