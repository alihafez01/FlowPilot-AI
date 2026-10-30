import hashlib
import ipaddress
import os
import re
import uuid

from html import unescape
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.schemas.discovery import ICPConfig
from app.services.tavily import TavilyAPIError, TavilyClient


BLOCKED_DOMAINS = {
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "tiktok.com",
    "crunchbase.com",
    "clutch.co",
    "upcity.com",
    "goodfirms.co",
    "g2.com",
    "yelp.com",
    "yellowpages.com",
    "mapquest.com",
    "zoominfo.com",
    "rocketreach.co",
    "glassdoor.com",
    "indeed.com",
    "reddit.com",
    "wikipedia.org",
}

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "ref",
    "ref_source",
}


class DiscoveryServiceError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def get_database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()

    if not value:
        raise DiscoveryServiceError(
            500,
            "DATABASE_URL is not configured.",
        )

    return value


def normalize_source_url(raw_url: str) -> str | None:
    """Accept only ordinary HTTP(S) website URLs."""
    if not raw_url or not isinstance(raw_url, str):
        return None

    try:
        parsed = urlsplit(raw_url.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None

    if parsed.scheme.lower() not in {"http", "https"}:
        return None

    if not host or parsed.username or parsed.password:
        return None

    host = host.lower().rstrip(".")

    if (
        host == "localhost"
        or host.endswith((".local", ".localhost", ".invalid", ".test"))
        or "." not in host
    ):
        return None

    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass

    if port not in (None, 80, 443):
        return None

    query_items = []

    for key, value in parse_qsl(
        parsed.query,
        keep_blank_values=True,
    ):
        lower_key = key.lower()

        if (
            lower_key.startswith("utm_")
            or lower_key in TRACKING_PARAMETERS
        ):
            continue

        query_items.append((key, value))

    cleaned_query = urlencode(query_items, doseq=True)

    return urlunsplit(
        (
            parsed.scheme.lower(),
            host,
            parsed.path or "/",
            cleaned_query,
            "",
        )
    )


def domain_from_url(url: str) -> str | None:
    normalized_url = normalize_source_url(url)

    if not normalized_url:
        return None

    host = urlsplit(normalized_url).hostname

    if not host:
        return None

    if host.startswith("www."):
        host = host[4:]

    return host.lower()


def domain_matches(domain: str, filter_domain: str) -> bool:
    return (
        domain == filter_domain
        or domain.endswith("." + filter_domain)
    )


def is_excluded_domain(
    domain: str,
    excluded_domains: list[str],
) -> bool:
    all_excluded = BLOCKED_DOMAINS.union(
        item.lower().removeprefix("www.")
        for item in excluded_domains
    )

    return any(
        domain_matches(domain, blocked)
        for blocked in all_excluded
    )


def build_search_queries(icp: ICPConfig) -> list[str]:
    """Build a small, deterministic search plan from the ICP."""
    queries = []
    combinations = [
        (industry, geography)
        for industry in icp.target_industries
        for geography in icp.target_geographies
    ]

    for industry, geography in combinations[:icp.max_queries]:
        terms = [
            industry,
            geography,
            *icp.company_types[:2],
            *icp.service_keywords[:3],
            *icp.additional_keywords[:2],
            "official website",
        ]

        query = " ".join(
            term.strip()
            for term in terms
            if term and term.strip()
        )

        if query not in queries:
            queries.append(query[:450])

    return queries[:icp.max_queries]


def clean_content(raw_text: str | None) -> str:
    if not raw_text:
        return ""

    text = unescape(raw_text)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]{1,200}>", " ", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`~>#]", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def matching_terms(
    terms: list[str],
    text: str,
) -> list[str]:
    folded_text = text.casefold()
    matches = []

    for term in terms:
        if term.casefold() in folded_text and term not in matches:
            matches.append(term)

    return matches


def infer_company_name(title: str, domain: str) -> str:
    candidate = (title or "").strip()

    candidate = re.split(
        r"\s(?:\||—|–)\s",
        candidate,
        maxsplit=1,
    )[0].strip()

    generic_patterns = (
        r"\btop\s+\d+\b",
        r"\bbest\s+\w+\s+(companies|agencies|firms)\b",
        r"\blist of\b",
        r"\bdirectory\b",
    )

    if (
        len(candidate) < 3
        or any(
            re.search(pattern, candidate, flags=re.IGNORECASE)
            for pattern in generic_patterns
        )
    ):
        candidate = domain.split(".")[0].replace("-", " ").title()

    return candidate[:180] or domain


def classify_website(
    candidate: dict,
    icp: ICPConfig,
) -> str:
    """
    Heuristic classification only.

    A successful extraction is evidence, not proof of domain ownership.
    """
    if candidate["extraction_status"] == "not_requested":
        return "candidate"

    if candidate["extraction_status"] != "extracted":
        return "needs_review"

    content = candidate["extracted_content"] or ""

    combined = " ".join(
        [
            candidate.get("title", ""),
            candidate.get("snippet", ""),
            content,
        ]
    ).casefold()

    relevant_terms = (
        icp.target_industries
        + icp.service_keywords
    )

    has_relevant_term = any(
        term.casefold() in combined
        for term in relevant_terms
    )

    domain_tokens = [
        token
        for token in re.split(
            r"[^a-z0-9]+",
            candidate["domain"].split(".")[0].casefold(),
        )
        if len(token) >= 3
    ]

    has_brand_evidence = any(
        token in combined
        for token in domain_tokens
    )

    if (
        len(content) >= 250
        and has_relevant_term
        and has_brand_evidence
    ):
        return "likely_official"

    return "needs_review"


def _extract_usage(response: dict, fallback: int) -> int:
    usage = response.get("usage") or {}

    try:
        return max(0, int(usage.get("credits", fallback)))
    except (TypeError, ValueError):
        return fallback


def _update_run(
    database_url: str,
    run_id: uuid.UUID,
    status: str,
    results_count: int,
    companies_upserted: int,
    extracted_count: int,
    credits: int,
    errors: list[dict],
) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            UPDATE discovery_runs
            SET status = %s,
                results_count = %s,
                companies_upserted = %s,
                extracted_count = %s,
                tavily_credits = %s,
                errors = %s,
                finished_at = now()
            WHERE id = %s
            """,
            (
                status,
                results_count,
                companies_upserted,
                extracted_count,
                credits,
                Jsonb(errors),
                run_id,
            ),
        )


def execute_discovery(icp: ICPConfig) -> dict:
    api_key = os.getenv("TAVILY_API_KEY", "").strip()

    if not api_key:
        raise DiscoveryServiceError(
            503,
            "Tavily API key missing. Configure it in your local .env file.",
        )

    database_url = get_database_url()
    run_id = uuid.uuid4()
    queries = build_search_queries(icp)

    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            INSERT INTO discovery_runs (
                id, status, icp_config, queries
            )
            VALUES (%s, 'running', %s, %s)
            """,
            (
                run_id,
                Jsonb(icp.model_dump()),
                Jsonb(queries),
            ),
        )

    errors = []
    credits = 0
    search_successes = 0

    # Keyed by normalized source URL, so repeated search results collapse.
    candidates_by_url: dict[str, dict] = {}

    try:
        with TavilyClient() as client:
            for query in queries:
                try:
                    response = client.search(
                        query=query,
                        max_results=icp.max_results_per_query,
                        include_domains=icp.include_domains,
                    )

                    search_successes += 1
                    credits += _extract_usage(response, fallback=1)

                except TavilyAPIError as exc:
                    errors.append(
                        {
                            "stage": "search",
                            "query": query,
                            "code": f"tavily_http_{exc.status_code}",
                            "message": exc.message,
                        }
                    )
                    continue

                for result in response.get("results", []):
                    if not isinstance(result, dict):
                        continue

                    source_url = normalize_source_url(
                        result.get("url", "")
                    )

                    if not source_url:
                        continue

                    domain = domain_from_url(source_url)

                    if not domain or is_excluded_domain(
                        domain,
                        icp.exclude_domains,
                    ):
                        continue

                    if icp.include_domains and not any(
                        domain_matches(domain, allowed)
                        for allowed in icp.include_domains
                    ):
                        continue

                    score = result.get("score", 0.0)

                    try:
                        score = float(score)
                    except (TypeError, ValueError):
                        score = 0.0

                    candidate = {
                        "url": source_url,
                        "domain": domain,
                        "title": str(result.get("title") or "")[:300],
                        "snippet": str(result.get("content") or "")[:3000],
                        "score": max(0.0, min(1.0, score)),
                        "published_date": str(
                            result.get("published_date") or ""
                        )[:100],
                        "query": query,
                        "extracted_content": None,
                        "extraction_status": "not_requested",
                    }

                    existing = candidates_by_url.get(source_url)

                    if existing is None or candidate["score"] > existing["score"]:
                        candidates_by_url[source_url] = candidate

            candidates = list(candidates_by_url.values())

            # Extract only the highest-ranked result per domain.
            best_by_domain = {}

            for candidate in candidates:
                domain = candidate["domain"]
                existing = best_by_domain.get(domain)

                if existing is None or candidate["score"] > existing["score"]:
                    best_by_domain[domain] = candidate

            extraction_targets = sorted(
                best_by_domain.values(),
                key=lambda item: item["score"],
                reverse=True,
            )[:icp.extract_top_n]

            if extraction_targets:
                target_urls = [
                    candidate["url"]
                    for candidate in extraction_targets
                ]

                try:
                    extraction_response = client.extract(target_urls)
                    credits += _extract_usage(
                        extraction_response,
                        fallback=1,
                    )

                    extracted_by_url = {}

                    for item in extraction_response.get("results", []):
                        if not isinstance(item, dict):
                            continue

                        normalized = normalize_source_url(
                            item.get("url", "")
                        )

                        if not normalized:
                            continue

                        extracted_by_url[normalized] = clean_content(
                            item.get("raw_content")
                        )

                    failed_urls = {
                        normalize_source_url(item.get("url", ""))
                        for item in extraction_response.get(
                            "failed_results", []
                        )
                        if isinstance(item, dict)
                    }

                    for candidate in extraction_targets:
                        url = candidate["url"]

                        if url in extracted_by_url and extracted_by_url[url]:
                            candidate["extracted_content"] = (
                                extracted_by_url[url][:20000]
                            )
                            candidate["extraction_status"] = "extracted"

                        elif url in failed_urls:
                            candidate["extraction_status"] = "failed"

                    # Keep extracted data attached to its unique source URL.
                    for candidate in candidates:
                        extracted = extracted_by_url.get(candidate["url"])

                        if extracted:
                            candidate["extracted_content"] = extracted[:20000]
                            candidate["extraction_status"] = "extracted"

                except TavilyAPIError as exc:
                    errors.append(
                        {
                            "stage": "extract",
                            "code": f"tavily_http_{exc.status_code}",
                            "message": exc.message,
                        }
                    )

                    for candidate in extraction_targets:
                        candidate["extraction_status"] = "failed"

    except TavilyAPIError as exc:
        errors.append(
            {
                "stage": "configuration",
                "code": f"tavily_http_{exc.status_code}",
                "message": exc.message,
            }
        )

    candidates = list(candidates_by_url.values())
    grouped_by_domain: dict[str, list[dict]] = {}

    for candidate in candidates:
        grouped_by_domain.setdefault(candidate["domain"], []).append(
            candidate
        )

    # If every search call failed, keep a durable failed run.
    if search_successes == 0:
        _update_run(
            database_url,
            run_id,
            "failed",
            len(candidates),
            0,
            0,
            credits,
            errors,
        )

        return {
            "run_id": str(run_id),
            "status": "failed",
            "queries_executed": len(queries),
            "source_results": len(candidates),
            "companies_upserted": 0,
            "extracted_urls": 0,
            "tavily_credits": credits,
            "errors": errors,
            "companies": [],
        }

    extracted_count = sum(
        1
        for candidate in candidates
        if candidate["extraction_status"] == "extracted"
    )

    company_previews = []

    try:
        with psycopg.connect(database_url) as connection:
            for domain, domain_candidates in grouped_by_domain.items():
                best = max(
                    domain_candidates,
                    key=lambda item: item["score"],
                )

                extracted_text = best.get("extracted_content") or ""
                evidence_text = " ".join(
                    [
                        best["title"],
                        best["snippet"],
                        extracted_text,
                    ]
                )

                industry = next(
                    (
                        item
                        for item in icp.target_industries
                        if item.casefold() in evidence_text.casefold()
                    ),
                    None,
                )

                matched_services = matching_terms(
                    icp.service_keywords,
                    evidence_text,
                )

                website_status = classify_website(best, icp)
                company_name = infer_company_name(
                    best["title"],
                    domain,
                )

                description = (
                    extracted_text[:1500]
                    if extracted_text
                    else best["snippet"][:1500]
                )

                source_urls = sorted(
                    {
                        item["url"]
                        for item in domain_candidates
                    }
                )

                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO companies (
                            name,
                            normalized_domain,
                            website_url,
                            industry,
                            description,
                            services,
                            source_urls,
                            website_validation_status
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (normalized_domain)
                        DO UPDATE SET
                            website_url = EXCLUDED.website_url,
                            industry = COALESCE(
                                EXCLUDED.industry,
                                companies.industry
                            ),
                            description = COALESCE(
                                NULLIF(EXCLUDED.description, ''),
                                companies.description
                            ),
                            services = (
                                SELECT COALESCE(
                                    jsonb_agg(DISTINCT item),
                                    '[]'::jsonb
                                )
                                FROM jsonb_array_elements_text(
                                    companies.services || EXCLUDED.services
                                ) AS elements(item)
                            ),
                            source_urls = (
                                SELECT COALESCE(
                                    jsonb_agg(DISTINCT item),
                                    '[]'::jsonb
                                )
                                FROM jsonb_array_elements_text(
                                    companies.source_urls || EXCLUDED.source_urls
                                ) AS elements(item)
                            ),
                            website_validation_status = CASE
                                WHEN EXCLUDED.website_validation_status
                                    = 'likely_official'
                                    OR companies.website_validation_status
                                    = 'likely_official'
                                    THEN 'likely_official'
                                WHEN EXCLUDED.website_validation_status
                                    = 'needs_review'
                                    OR companies.website_validation_status
                                    = 'needs_review'
                                    THEN 'needs_review'
                                ELSE 'candidate'
                            END
                        RETURNING id
                        """,
                        (
                            company_name,
                            domain,
                            f"https://{domain}",
                            industry,
                            description,
                            Jsonb(matched_services),
                            Jsonb(source_urls),
                            website_status,
                        ),
                    )

                    company_id = cursor.fetchone()[0]

                    for candidate in domain_candidates:
                        content = candidate.get("extracted_content")
                        content_hash = (
                            hashlib.sha256(
                                content.encode("utf-8")
                            ).hexdigest()
                            if content
                            else None
                        )

                        source_status = (
                            classify_website(candidate, icp)
                            if candidate["extraction_status"] == "extracted"
                            else (
                                "needs_review"
                                if candidate["extraction_status"] == "failed"
                                else "candidate"
                            )
                        )

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
                                published_date,
                                source_score,
                                source_status,
                                extraction_status,
                                extracted_content,
                                content_sha256
                            )
                            VALUES (
                                %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s
                            )
                            ON CONFLICT (
                                company_id,
                                discovery_run_id,
                                source_url
                            )
                            DO UPDATE SET
                                title = EXCLUDED.title,
                                snippet = EXCLUDED.snippet,
                                source_score = EXCLUDED.source_score,
                                source_status = EXCLUDED.source_status,
                                extraction_status = EXCLUDED.extraction_status,
                                extracted_content = COALESCE(
                                    EXCLUDED.extracted_content,
                                    company_sources.extracted_content
                                ),
                                content_sha256 = COALESCE(
                                    EXCLUDED.content_sha256,
                                    company_sources.content_sha256
                                ),
                                last_seen_at = now()
                            """,
                            (
                                company_id,
                                run_id,
                                candidate["url"],
                                domain,
                                candidate["query"],
                                candidate["title"],
                                candidate["snippet"],
                                candidate["published_date"],
                                candidate["score"],
                                source_status,
                                candidate["extraction_status"],
                                content,
                                content_hash,
                            ),
                        )

                company_previews.append(
                    {
                        "id": str(company_id),
                        "name": company_name,
                        "domain": domain,
                        "website_url": f"https://{domain}",
                        "industry": industry,
                        "matched_services": matched_services,
                        "website_validation_status": website_status,
                        "source_count": len(domain_candidates),
                    }
                )

    except psycopg.Error:
        _update_run(
            database_url,
            run_id,
            "failed",
            len(candidates),
            0,
            extracted_count,
            credits,
            errors + [
                {
                    "stage": "database",
                    "code": "database_persistence_error",
                    "message": "Could not persist the discovery results.",
                }
            ],
        )

        raise DiscoveryServiceError(
            500,
            "Database persistence failed. Check API container logs.",
        ) from None

    status = "partial" if errors else "completed"

    _update_run(
        database_url,
        run_id,
        status,
        len(candidates),
        len(company_previews),
        extracted_count,
        credits,
        errors,
    )

    return {
        "run_id": str(run_id),
        "status": status,
        "queries_executed": search_successes,
        "source_results": len(candidates),
        "companies_upserted": len(company_previews),
        "extracted_urls": extracted_count,
        "tavily_credits": credits,
        "errors": errors,
        "companies": company_previews,
    }


def get_discovery_run(run_id: uuid.UUID) -> dict | None:
    with psycopg.connect(
        get_database_url(),
        row_factory=dict_row,
    ) as connection:
        row = connection.execute(
            "SELECT * FROM discovery_runs WHERE id = %s",
            (run_id,),
        ).fetchone()

    return dict(row) if row else None


def list_companies(
    limit: int,
    offset: int,
    validation_status: str | None = None,
) -> list[dict]:
    sql = """
        SELECT
            id,
            name,
            normalized_domain,
            website_url,
            industry,
            description,
            services,
            source_urls,
            website_validation_status,
            created_at,
            updated_at
        FROM companies
    """
    parameters = []

    if validation_status:
        sql += " WHERE website_validation_status = %s"
        parameters.append(validation_status)

    sql += " ORDER BY updated_at DESC LIMIT %s OFFSET %s"
    parameters.extend([limit, offset])

    with psycopg.connect(
        get_database_url(),
        row_factory=dict_row,
    ) as connection:
        rows = connection.execute(sql, parameters).fetchall()

    return [dict(row) for row in rows]


def list_company_sources(
    company_id: uuid.UUID,
    limit: int = 50,
) -> list[dict]:
    with psycopg.connect(
        get_database_url(),
        row_factory=dict_row,
    ) as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                company_id,
                discovery_run_id,
                source_url,
                source_domain,
                source_query,
                title,
                snippet,
                published_date,
                source_score,
                source_status,
                extraction_status,
                LEFT(extracted_content, 5000) AS extracted_content,
                content_sha256,
                first_seen_at,
                last_seen_at
            FROM company_sources
            WHERE company_id = %s
            ORDER BY last_seen_at DESC
            LIMIT %s
            """,
            (company_id, limit),
        ).fetchall()

    return [dict(row) for row in rows]