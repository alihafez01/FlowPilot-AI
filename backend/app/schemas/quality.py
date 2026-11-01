
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


EmailStatus = Literal[
    "missing",
    "invalid",
    "syntax_valid",
    "mx_valid",
    "dns_fallback_valid",
    "no_mail_route",
    "dns_error",
]

Priority = Literal["high", "medium", "low"]


class EmailValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, max_length=320)
    contact_id: UUID | None = None
    check_dns: bool = True

    @field_validator("email")
    @classmethod
    def clean_email(cls, value):
        if value is None:
            return None
        return value.strip()


class EmailValidationResult(BaseModel):
    email: str | None
    normalized_email: str | None = None
    email_dedupe_key: str | None = None
    domain: str | None = None

    status: EmailStatus
    dns_status: Literal[
        "not_checked",
        "mx_valid",
        "dns_fallback_valid",
        "no_mail_route",
        "dns_error",
    ]

    mx_hosts: list[str] = Field(default_factory=list)
    is_role_account: bool = False

    # DNS does not prove that a mailbox exists.
    mailbox_verified: bool = False
    reason: str | None = None


# class LeadScoringRequest(BaseModel):
#     model_config = ConfigDict(extra="forbid")

#     target_industries: list[str] = Field(
#         min_length=1,
#         max_length=10,
#     )

#     service_keywords: list[str] = Field(
#         min_length=1,
#         max_length=20,
#     )

#     evidence_keywords: list[str] = Field(
#         default_factory=list,
#         max_length=20,
#     )

#     @field_validator(
#         "target_industries",
#         "service_keywords",
#         "evidence_keywords",
#         mode="before",
#     )
#     @classmethod
#     def clean_terms(cls, value):
#         if not isinstance(value, list):
#             raise ValueError("Expected a list of text values.")

#         cleaned = []
#         seen = set()

#         for item in value:
#             if not isinstance(item, str):
#                 raise ValueError("Every item must be text.")

#             item = item.strip()

#             if not item:
#                 continue

#             if len(item) > 100:
#                 raise ValueError("Each term must be at most 100 characters.")

#             if item.casefold() not in seen:
#                 cleaned.append(item)
#                 seen.add(item.casefold())

#         return cleaned

class LeadScoringRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_industries: list[str] = Field(
        min_length=1,
        max_length=10,
    )

    opportunity_signals: list[str] = Field(
        default_factory=list,
        max_length=30,
    )

    @field_validator(
        "target_industries",
        "opportunity_signals",
        mode="before",
    )
    @classmethod
    def clean_terms(cls, value):
        if not isinstance(value, list):
            raise ValueError("Expected a list of text values.")

        cleaned = []
        seen = set()

        for item in value:
            if not isinstance(item, str):
                raise ValueError("Every item must be text.")

            item = item.strip()

            if not item:
                continue

            if len(item) > 100:
                raise ValueError("Terms cannot exceed 100 characters.")

            if item.casefold() not in seen:
                cleaned.append(item)
                seen.add(item.casefold())

        return cleaned


# class QualityRunRequest(LeadScoringRequest):
#     company_limit: int = Field(default=50, ge=1, le=100)

#     check_dns: bool = True

#     duplicate_threshold: float = Field(
#         default=92,
#         ge=85,
#         le=100,
#     )

class QualityRunRequest(LeadScoringRequest):
    company_limit: int = Field(default=50, ge=1, le=100)

    check_dns: bool = True

    duplicate_threshold: float = Field(
        default=92,
        ge=85,
        le=100,
    )


# class LeadScoreResult(BaseModel):
#     company_id: UUID

#     fit_score: int = Field(ge=0, le=100)
#     priority: Priority

#     industry_score: int = Field(ge=0, le=30)
#     service_score: int = Field(ge=0, le=25)
#     evidence_score: int = Field(ge=0, le=20)
#     completeness_score: int = Field(ge=0, le=10)
#     contact_score: int = Field(ge=0, le=15)

#     matched_industries: list[str] = Field(default_factory=list)
#     matched_services: list[str] = Field(default_factory=list)
#     matched_evidence: list[str] = Field(default_factory=list)

#     reasons: list[str] = Field(default_factory=list)

#     @classmethod
#     def validate_total(cls, values):
#         return values

class LeadScoreResult(BaseModel):
    company_id: UUID

    fit_score: int = Field(ge=0, le=100)
    priority: Priority

    industry_score: int = Field(ge=0, le=30)
    opportunity_score: int = Field(ge=0, le=25)
    evidence_score: int = Field(ge=0, le=20)
    completeness_score: int = Field(ge=0, le=10)
    contact_score: int = Field(ge=0, le=15)

    matched_industries: list[str] = Field(default_factory=list)
    matched_opportunity_signals: list[str] = Field(default_factory=list)

    reasons: list[str] = Field(default_factory=list)


class DuplicateCandidateResult(BaseModel):
    company_a_id: UUID
    company_b_id: UUID

    similarity_score: float = Field(ge=0, le=100)
    match_reason: str
