
import os
import re
import uuid

import psycopg

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.services.email_validation import (
    validate_email_address,
    create_dns_resolver,
)
from app.services.deduplication import (
    find_duplicate_candidates,
)
from app.services.lead_scoring import score_lead
from app.schemas.quality import QualityRunRequest


DATABASE_URL = os.environ["DATABASE_URL"]

EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])"
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
    r"(?![\w.-])",
    re.IGNORECASE,
)

IGNORED_EMAILS = {
    "example@example.com",
    "test@example.com",
    "noreply@example.com",
    "no-reply@example.com",
}


def extract_email_candidates(text: str) -> list[str]:
    if not text:
        return []

    found = []

    for match in EMAIL_PATTERN.findall(text):
        email = match.strip(".,;:()[]{}<>").lower()

        local_part = email.split("@", 1)[0]

        if local_part in {
            "noreply",
            "no-reply",
            "do-not-reply",
            "donotreply",
        }:
            continue

        if email in IGNORED_EMAILS:
            continue

        if email not in found:
            found.append(email)

    return found


def extract_public_contacts(connection) -> int:
    """
    Extract business emails from already collected public evidence.

    Website-derived emails are restricted to the company's domain.
    OSM emails must be explicitly present in business contact tags.
    """

    sources = connection.execute(
        """
        SELECT
            c.id AS company_id,
            c.normalized_domain,
            s.source_url,
            s.source_type,
            s.extracted_content,
            s.metadata
        FROM companies c
        JOIN company_sources s
          ON s.company_id = c.id
        WHERE c.record_type = 'business'
          AND (
                (s.source_type = 'official_website'
                 AND s.source_status = 'likely_official')
                OR s.source_type = 'osm_listing'
          )
        """
    ).fetchall()

    inserted = 0

    for source in sources:

        if source["source_type"] == "osm_listing":
            metadata = source["metadata"] or {}

            raw_values = [
                metadata.get("contact:email"),
                metadata.get("email"),
            ]

            text = " ".join(
                str(value)
                for value in raw_values
                if value
            )

        else:
            text = source["extracted_content"] or ""

        for email in extract_email_candidates(text):
            result = validate_email_address(
                email,
                check_dns=False,
            )

            normalized = result["normalized_email"]

            if not normalized:
                continue

            domain = normalized.rsplit("@", 1)[-1].casefold()

            # Website pages: accept only same-domain business emails.
            if source["source_type"] == "official_website":
                company_domain = (
                    source["normalized_domain"] or ""
                ).casefold()

                if not (
                    company_domain
                    and (
                        domain == company_domain
                        or domain.endswith("." + company_domain)
                    )
                ):
                    continue

            dedupe_key = normalized.casefold()

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO contacts (
                        company_id,
                        email,
                        normalized_email,
                        email_dedupe_key,
                        email_validation_status,
                        email_dns_status,
                        source_url
                    )
                    VALUES (
                        %s, %s, %s, %s,
                        'unknown', 'not_checked', %s
                    )
                    ON CONFLICT (email_dedupe_key)
                    DO NOTHING
                    RETURNING id
                    """,
                    (
                        source["company_id"],
                        email,
                        normalized,
                        dedupe_key,
                        source["source_url"],
                    ),
                )

                if cursor.fetchone():
                    inserted += 1

    return inserted




from psycopg import errors


def run_quality_pipeline(request: QualityRunRequest) -> dict:
    """
    Validate existing public contacts, score companies,
    and persist fuzzy duplicate suggestions.
    """

    resolver = create_dns_resolver()

    contacts_inserted = 0
    emails_checked = 0
    email_status_counts = {}
    scored_leads = []
    duplicate_suggestions = []

    try:
        with psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
        ) as connection:

            # Stage 1: Import contact emails from existing evidence.
            contacts_inserted = extract_public_contacts(connection)

            # Select a bounded set of companies and their contacts.
            rows = connection.execute(
                """
                WITH selected_companies AS (
                    SELECT *
                    FROM companies
                    WHERE record_type = 'business'
                    ORDER BY created_at DESC
                    LIMIT %s
                )
                SELECT
                    c.id,
                    c.name,
                    c.normalized_domain,
                    c.website_url,
                    c.website_status,
                    c.industry,
                    c.description,
                    c.services,
                    c.source_urls,
                    c.location_text,
                    c.latitude,
                    c.longitude,
                    c.phone,

                    ct.id AS contact_id,
                    ct.email,
                    ct.email_validation_status,
                    NULL::text AS contact_phone,

                    COALESCE(src.evidence_text, '') AS evidence_text,
                    COALESCE(src.official_source_count, 0) AS official_source_count,
                    COALESCE(src.extracted_source_count, 0) AS extracted_source_count

                FROM selected_companies c

                LEFT JOIN contacts ct
                    ON ct.company_id = c.id

                LEFT JOIN LATERAL (
                    /*
                     * A source URL can be recorded once per discovery run.
                     * Count and score each distinct URL only once, selecting
                     * its latest stored version to prevent repeated runs from
                     * inflating evidence quality.
                     */
                    SELECT
                        string_agg(
                            concat_ws(
                                ' ',
                                NULLIF(title, ''),
                                NULLIF(snippet, ''),
                                NULLIF(extracted_content, '')
                            ),
                            ' ' ORDER BY source_url
                        ) AS evidence_text,

                        COUNT(*)::INTEGER AS official_source_count,

                        COUNT(*) FILTER (
                            WHERE LENGTH(COALESCE(extracted_content, '')) >= 250
                        )::INTEGER AS extracted_source_count

                    FROM (
                        SELECT DISTINCT ON (source_url)
                            source_url,
                            title,
                            snippet,
                            extracted_content
                        FROM company_sources
                        WHERE company_id = c.id
                          AND source_type = 'official_website'
                          AND source_status = 'likely_official'
                        ORDER BY source_url, last_seen_at DESC, id DESC
                    ) AS unique_official_sources
                ) src ON TRUE

                ORDER BY c.created_at DESC, ct.created_at DESC
                """,
                (request.company_limit,),
            ).fetchall()

            unique_companies = {}

            # Stage 2: Validate every available contact.
            for row in rows:

                company = {
                    "id": row["id"],
                    "name": row["name"],
                    "industry": row["industry"],
                    "description": row["description"],
                    "services": row["services"],
                    "source_urls": row["source_urls"],
                    "location_text": row["location_text"],
                    "phone": row["phone"],
                }

                unique_companies[str(row["id"])] = {
                    "id": row["id"],
                    "name": row["name"],
                    "location_text": row["location_text"],
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                    "normalized_domain": row["normalized_domain"],
                }

                contact = None

                if row["contact_id"]:
                    contact = {
                        "id": row["contact_id"],
                        "email": row["email"],
                        "phone": row["contact_phone"],
                        "email_validation_status": "unknown",
                    }

                    validation = validate_email_address(
                        row["email"],
                        check_dns=request.check_dns,
                        resolver=resolver,
                    )

                    emails_checked += 1

                    status = validation["status"]
                    dedupe_key = (
                        validation["normalized_email"].casefold()
                        if validation["normalized_email"]
                        else None
                    )

                    # Exact duplicate email attached to another contact.
                    duplicate_contact = None

                    if dedupe_key:
                        duplicate_contact = connection.execute(
                            """
                            SELECT id, company_id
                            FROM contacts
                            WHERE email_dedupe_key = %s
                              AND id <> %s
                            LIMIT 1
                            """,
                            (
                                dedupe_key,
                                contact["id"],
                            ),
                        ).fetchone()

                    if duplicate_contact:
                        status = "risky"
                        contact["email_validation_status"] = status

                        validation["reason"] = (
                            "The same normalized email is already "
                            "associated with another contact record."
                        )

                    else:
                        contact["email_validation_status"] = status

                        connection.execute(
                            "SAVEPOINT contact_email_update"
                        )

                        try:
                            connection.execute(
                                """
                                UPDATE contacts
                                SET normalized_email = %s,
                                    email_dedupe_key = %s,
                                    email_validation_status = %s,
                                    email_dns_status = %s,
                                    email_validation_reason = %s,
                                    email_validated_at = now(),
                                    is_role_account = %s,
                                    updated_at = now()
                                WHERE id = %s
                                """,
                                (
                                    validation["normalized_email"],
                                    dedupe_key,
                                    status,
                                    validation["dns_status"],
                                    validation["reason"],
                                    validation["is_role_account"],
                                    contact["id"],
                                ),
                            )
                            connection.execute(
                                "RELEASE SAVEPOINT contact_email_update"
                            )

                        except errors.UniqueViolation:
                            connection.execute(
                                "ROLLBACK TO SAVEPOINT contact_email_update"
                            )
                            connection.execute(
                                "RELEASE SAVEPOINT contact_email_update"
                            )

                            status = "risky"
                            contact["email_validation_status"] = "risky"

                            validation["reason"] = (
                                "Concurrent email deduplication conflict."
                            )

                    email_status_counts[status] = (
                        email_status_counts.get(status, 0) + 1
                    )

                    contact["email_validation"] = validation

                # Company profile phone is useful even if no email exists.
                scoring_contact = {
                    "email_validation_status": (
                        contact["email_validation_status"]
                        if contact
                        else "missing"
                    ),
                    "phone": (
                        (contact or {}).get("phone")
                        or row["phone"]
                    ),
                }

                # Stage 3: Deterministic lead qualification.
                score = score_lead(
                    company=company,
                    target_industries=request.target_industries,
                    opportunity_signals=request.opportunity_signals,
                    contact=scoring_contact,
                    evidence_text=row["evidence_text"] or "",
                    official_source_count=row["official_source_count"] or 0,
                    extracted_source_count=row["extracted_source_count"] or 0,
                ) 

                connection.execute(
                    """
                    INSERT INTO lead_analyses (
                        company_id,
                        contact_id,
                        fit_score,
                        priority,
                        fit_reasons,
                        evidence,
                        industry_score,
                        opportunity_score,
                        evidence_score,
                        completeness_score,
                        contact_score,
                        scoring_version,
                        model_name,
                        prompt_version
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        'v2',
                        'deterministic_rules',
                        'not_applicable'
                    )
                    """,
                    (
                        row["id"],
                        row["contact_id"],
                        score["fit_score"],
                        score["priority"],
                        Jsonb(score["reasons"]),
                        Jsonb({
                            "matched_industries": score["matched_industries"],
                            "matched_opportunity_signals": (
                                score["matched_opportunity_signals"]
                            ),
                            "source_urls": row["source_urls"],
                        }),
                        score["industry_score"],
                        score["opportunity_score"],
                        score["evidence_score"],
                        score["completeness_score"],
                        score["contact_score"],
                    ),
                )

                scored_leads.append({
                    "company_id": str(row["id"]),
                    "company_name": row["name"],
                    "contact_id": (
                        str(row["contact_id"])
                        if row["contact_id"]
                        else None
                    ),
                    "email_status": (
                        contact["email_validation_status"]
                        if contact
                        else "missing"
                    ),
                    **score,
                })

            # Stage 4: Fuzzy duplicate suggestions.
            suggestions = find_duplicate_candidates(
                list(unique_companies.values()),
                threshold=request.duplicate_threshold,
            )

            for suggestion in suggestions:
                first, second = sorted(
                    [
                        suggestion["company_a_id"],
                        suggestion["company_b_id"],
                    ],
                    key=str,
                )

                connection.execute(
                    """
                    INSERT INTO company_duplicate_candidates (
                        company_a_id,
                        company_b_id,
                        similarity_score,
                        match_reason
                    )
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (company_a_id, company_b_id)
                    DO UPDATE SET
                        similarity_score = EXCLUDED.similarity_score,
                        match_reason = EXCLUDED.match_reason
                    WHERE company_duplicate_candidates.status = 'pending'
                    """,
                    (
                        first,
                        second,
                        suggestion["similarity_score"],
                        suggestion["match_reason"],
                    ),
                )

                duplicate_suggestions.append({
                    **suggestion,
                    "company_a_id": str(first),
                    "company_b_id": str(second),
                })

        return {
            "status": "completed",
            "scoring_version": "v2",
            "companies_processed": len(unique_companies),
            "contacts_inserted_from_public_sources": contacts_inserted,
            "emails_checked": emails_checked,
            "email_status_counts": email_status_counts,
            "duplicate_suggestions": len(duplicate_suggestions),
            "duplicate_candidates": duplicate_suggestions,
            "lead_results": scored_leads,
        }

    except psycopg.Error:
        raise RuntimeError(
            "Quality pipeline database operation failed."
        ) from None
