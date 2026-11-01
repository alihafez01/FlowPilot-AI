
# from app.schemas.quality import LeadScoreResult


# EMAIL_POINTS = {
#     "mx_valid": 10,
#     "dns_fallback_valid": 8,
#     "syntax_valid": 5,
#     "dns_error": 2,
#     "no_mail_route": 0,
#     "invalid": 0,
#     "missing": 0,
#     "unknown": 0,
#     "not_checked": 0,
#     "risky": 0,
#     "verified": 10,
# }


# def as_list(value):
#     if value is None:
#         return []

#     if isinstance(value, list):
#         return value

#     return []


# def matched_terms(terms, text):
#     folded = (text or "").casefold()

#     return [
#         term
#         for term in terms
#         if term.casefold() in folded
#     ]


# def score_lead(
#     company: dict,
#     target_industries: list[str],
#     service_keywords: list[str],
#     evidence_keywords: list[str] | None = None,
#     contact: dict | None = None,
#     evidence_text: str = "",
# ) -> dict:

#     evidence_keywords = evidence_keywords or []

#     reasons = []

#     # 1. Industry match: 30 points
#     industry_text = str(company.get("industry") or "")

#     matched_industries = matched_terms(
#         target_industries,
#         industry_text,
#     )

#     industry_score = (
#         30 if matched_industries else 0
#     )

#     reasons.append(
#         "Industry matches the target ICP."
#         if industry_score
#         else "Industry match not established."
#     )

#     # 2. Service relevance: 25 points
#     services = as_list(company.get("services"))

#     service_text = " ".join(
#         str(item) for item in services
#     )

#     matched_services = matched_terms(
#         service_keywords,
#         service_text,
#     )

#     service_score = (
#         round(
#             25 * len(matched_services) / len(service_keywords)
#         )
#         if service_keywords
#         else 0
#     )

#     reasons.append(
#         f"{len(matched_services)} relevant service keywords matched."
#         if matched_services
#         else "No target service keyword matched."
#     )

#     # 3. Business evidence: 20 points
#     combined_evidence = " ".join(
#         [
#             str(company.get("name") or ""),
#             str(company.get("description") or ""),
#             evidence_text or "",
#         ]
#     )

#     matched_evidence = matched_terms(
#         evidence_keywords,
#         combined_evidence,
#     )

#     evidence_score = (
#         round(
#             20 * len(matched_evidence) / len(evidence_keywords)
#         )
#         if evidence_keywords
#         else 0
#     )

#     reasons.append(
#         f"{len(matched_evidence)} evidence keywords supported."
#         if matched_evidence
#         else "No additional evidence keyword confirmed."
#     )

#     # 4. Profile completeness: 10 points
#     completeness_score = 0

#     if company.get("description"):
#         completeness_score += 4

#     if (
#         company.get("location_text")
#         or company.get("address")
#     ):
#         completeness_score += 3

#     if (
#         company.get("source_urls")
#         or company.get("source_url")
#     ):
#         completeness_score += 3

#     # Missing website does not reduce this score.
#     reasons.append(
#         f"Profile completeness: {completeness_score}/10."
#     )

#     # 5. Contact quality: 15 points
#     contact = contact or {}

#     email_status = (
#         contact.get("email_validation_status")
#         or contact.get("email_status")
#         or "missing"
#     )

#     contact_score = EMAIL_POINTS.get(
#         email_status,
#         0,
#     )

#     if contact.get("phone"):
#         contact_score += 5

#     contact_score = min(15, contact_score)

#     reasons.append(
#         f"Contact quality: {contact_score}/15."
#     )

#     total_score = (
#         industry_score
#         + service_score
#         + evidence_score
#         + completeness_score
#         + contact_score
#     )

#     total_score = max(0, min(100, total_score))

#     if total_score >= 70:
#         priority = "high"
#     elif total_score >= 40:
#         priority = "medium"
#     else:
#         priority = "low"

#     result = LeadScoreResult(
#         company_id=company["id"],
#         fit_score=total_score,
#         priority=priority,
#         industry_score=industry_score,
#         service_score=service_score,
#         evidence_score=evidence_score,
#         completeness_score=completeness_score,
#         contact_score=contact_score,
#         matched_industries=matched_industries,
#         matched_services=matched_services,
#         matched_evidence=matched_evidence,
#         reasons=reasons,
#     )

#     return result.model_dump(mode="json")







import re

from app.schemas.quality import LeadScoreResult


EMAIL_POINTS = {
    "mx_valid": 10,
    "dns_fallback_valid": 8,
    "syntax_valid": 5,
    "dns_error": 2,
    "verified": 10,
    "no_mail_route": 0,
    "invalid": 0,
    "missing": 0,
    "unknown": 0,
    "not_checked": 0,
    "risky": 0,
}


GENERIC_INDUSTRIES = {
    "company",
    "business",
    "establishment",
    "unknown",
    "yes",
}


GENERATED_DESCRIPTION_PREFIXES = (
    "openstreetmap business category:",
    "business category:",
)


def normalize_text(value):
    if not isinstance(value, str):
        return ""

    return " ".join(value.casefold().split())


def as_list(value):
    if isinstance(value, list):
        return value

    return []


def matched_terms(terms, text):
    normalized_text = normalize_text(text)

    return [
        term
        for term in terms
        if normalize_text(term) in normalized_text
    ]


def is_meaningful_description(description):
    text = normalize_text(description)

    if not text or len(text) < 80:
        return False

    if any(
        text.startswith(prefix)
        for prefix in GENERATED_DESCRIPTION_PREFIXES
    ):
        return False

    return True


def has_specific_address(location):
    """
    City or country alone is not a specific business address.
    """
    text = normalize_text(location)

    street_terms = (
        "street",
        "st",
        "road",
        "rd",
        "avenue",
        "ave",
        "boulevard",
        "blvd",
        "lane",
        "ln",
        "drive",
        "dr",
    )

    has_number = bool(
        re.search(r"\b\d{1,6}\b", text)
    )

    has_street = any(
        re.search(rf"\b{term}\b", text)
        for term in street_terms
    )

    return has_number and has_street


def score_lead(
    company,
    target_industries,
    opportunity_signals=None,
    contact=None,
    evidence_text="",
    official_source_count=0,
    extracted_source_count=0,
):
    opportunity_signals = opportunity_signals or []
    contact = contact or {}

    reasons = []

    # 1. Industry / ICP match: 30 points
    industry = normalize_text(
        company.get("industry")
    ).replace("_", " ")

    matched_industries = matched_terms(
        target_industries,
        industry,
    )

    industry_score = (
        30 if matched_industries else 0
    )

    reasons.append(
        "Target industry matched."
        if industry_score
        else "Target industry match not established."
    )

    # 2. Opportunity relevance: 25 points
    # Only original public source evidence is used.
    matched_opportunities = matched_terms(
        opportunity_signals,
        evidence_text,
    )

    # Use an explicit 13/25 split. Python round(12.5) uses
    # banker's rounding and returns 12, which is not the intended rubric.
    if not matched_opportunities:
        opportunity_score = 0
    elif len(matched_opportunities) == 1:
        opportunity_score = 13
    else:
        opportunity_score = 25

    reasons.append(
        f"{len(matched_opportunities)} opportunity signals found "
        "in source evidence."
        if matched_opportunities
        else "No opportunity signal found in source evidence."
    )

    # 3. Evidence quality: 20 points
    evidence_score = 0

    if extracted_source_count >= 1:
        evidence_score += 10

    if len(evidence_text.strip()) >= 1000:
        evidence_score += 5

    if official_source_count >= 2:
        evidence_score += 5

    reasons.append(
        f"Source evidence quality: {evidence_score}/20."
    )

    # 4. Profile completeness: 10 points
    completeness_score = 0

    if company.get("name"):
        completeness_score += 2

    if (
        industry
        and industry not in GENERIC_INDUSTRIES
    ):
        completeness_score += 2

    if is_meaningful_description(
        company.get("description")
    ):
        completeness_score += 2

    location = (
        company.get("location_text")
        or company.get("address")
    )

    if has_specific_address(location):
        completeness_score += 2

    if as_list(company.get("source_urls")):
        completeness_score += 2

    reasons.append(
        f"Profile completeness: {completeness_score}/10."
    )

    # 5. Contact quality: 15 points
    email_status = (
        contact.get("email_validation_status")
        or contact.get("email_status")
        or "missing"
    )

    contact_score = EMAIL_POINTS.get(
        email_status,
        0,
    )

    if contact.get("phone"):
        contact_score += 5

    contact_score = min(15, contact_score)

    reasons.append(
        f"Contact quality: {contact_score}/15."
    )

    total = (
        industry_score
        + opportunity_score
        + evidence_score
        + completeness_score
        + contact_score
    )

    total = max(0, min(100, total))

    if total >= 70:
        priority = "high"
    elif total >= 40:
        priority = "medium"
    else:
        priority = "low"

    result = LeadScoreResult(
        company_id=company["id"],
        fit_score=total,
        priority=priority,
        industry_score=industry_score,
        opportunity_score=opportunity_score,
        evidence_score=evidence_score,
        completeness_score=completeness_score,
        contact_score=contact_score,
        matched_industries=matched_industries,
        matched_opportunity_signals=matched_opportunities,
        reasons=reasons,
    )

    return result.model_dump(mode="json")