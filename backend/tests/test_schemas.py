
import pytest

from pydantic import ValidationError

from app.schemas.quality import (
    EmailValidationRequest,
    QualityRunRequest,
)


def test_email_request_strips_whitespace():
    request = EmailValidationRequest(
        email="  info@example.com  "
    )

    assert request.email == "info@example.com"


def test_email_request_allows_missing_address():
    request = EmailValidationRequest()

    assert request.email is None


def test_quality_run_default_values():
    request = QualityRunRequest(
        target_industries=["Retail"],
        opportunity_signals=["AI automation"],
    )

    assert request.target_industries == ["Retail"]
    assert request.opportunity_signals == ["AI automation"]
    assert request.company_limit == 50
    assert request.check_dns is True
    assert request.duplicate_threshold == 92


def test_quality_run_rejects_invalid_limit():
    with pytest.raises(ValidationError):
        QualityRunRequest(
            target_industries=["Retail"],
            service_keywords=["AI automation"],
            company_limit=500,
        )


def test_quality_run_rejects_empty_industries():
    with pytest.raises(ValidationError):
        QualityRunRequest(
            target_industries=[],
            service_keywords=["AI automation"],
        )


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        QualityRunRequest(
            target_industries=["Retail"],
            service_keywords=["AI automation"],
            unknown_setting=True,
        )
