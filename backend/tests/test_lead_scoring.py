from uuid import uuid4

from app.services.lead_scoring import score_lead


def test_high_quality_lead_reaches_high_priority():
    company = {
        "id": uuid4(),
        "name": "Example Web Agency",
        "industry": "web development agency",
        "description": "A specialist company delivering e-commerce websites and practical automation solutions for growing local businesses.",
        "services": ["web development", "AI automation"],
        "location_text": "120 Main Street, Austin, TX",
        "source_urls": ["https://example.com"],
        "phone": "+15125550100",
    }

    result = score_lead(
        company=company,
        target_industries=["web development agency"],
        opportunity_signals=["e-commerce", "automation"],
        evidence_text=("The company offers e-commerce and automation solutions. " * 30),
        official_source_count=2,
        extracted_source_count=1,
        contact={
            "email_validation_status": "mx_valid",
            "phone": "+15125550100",
        },
    )

    assert result["fit_score"] == 100
    assert result["priority"] == "high"
    assert result["opportunity_score"] == 25


def test_irrelevant_lead_gets_low_score():
    company = {
        "id": uuid4(),
        "name": "Example Restaurant",
        "industry": "restaurant",
        "description": "",
        "services": [],
        "location_text": "",
        "source_urls": [],
        "phone": None,
    }

    result = score_lead(
        company=company,
        target_industries=["SaaS company"],
        opportunity_signals=["AI automation"],
    )

    # A non-generic industry value is still profile metadata, but it does
    # not match the requested SaaS industry.
    assert result["fit_score"] == 4
    assert result["priority"] == "low"


def test_website_less_business_is_not_penalized_for_no_website():
    company = {
        "id": uuid4(),
        "name": "Example Retail",
        "industry": "retail business",
        "description": "A local retail store serving customers with specialist products and in-person assistance.",
        "services": ["retail"],
        "location_text": "Austin, Texas",
        "source_urls": ["https://www.openstreetmap.org/node/123"],
        "phone": "+15125550100",
    }

    result = score_lead(
        company=company,
        target_industries=["retail business"],
        opportunity_signals=[],
        contact={
            "email_validation_status": "missing",
            "phone": "+15125550100",
        },
    )

    assert result["industry_score"] == 30
    assert result["opportunity_score"] == 0
    assert result["completeness_score"] == 8
    assert result["contact_score"] == 5
    assert result["fit_score"] == 43


def test_osm_generated_description_and_city_are_not_full_completeness():
    company = {
        "id": uuid4(),
        "name": "Papaya",
        "industry": "clothes",
        "description": (
            "OpenStreetMap business category: clothes. "
            "Location: Austin, Travis County, Texas, United States."
        ),
        "services": ["clothes"],
        "location_text": "Austin, Travis County, Texas, United States",
        "source_urls": ["https://www.openstreetmap.org/node/123"],
        "phone": None,
    }

    result = score_lead(
        company=company,
        target_industries=["clothes"],
        opportunity_signals=["online ordering"],
    )

    assert result["completeness_score"] == 6
    assert result["evidence_score"] == 0
    assert result["opportunity_score"] == 0
    assert result["fit_score"] == 36


def test_exactly_one_opportunity_signal_gets_13_points():
    company = {"id": uuid4(), "name": "Example", "industry": "other"}

    result = score_lead(
        company=company,
        target_industries=["retail"],
        opportunity_signals=["online ordering", "appointment booking"],
        evidence_text="The website provides online ordering for customers.",
    )

    assert result["opportunity_score"] == 13
    assert result["matched_opportunity_signals"] == ["online ordering"]


def test_two_or_more_opportunity_signals_get_25_points():
    company = {"id": uuid4(), "name": "Example", "industry": "other"}

    result = score_lead(
        company=company,
        target_industries=["retail"],
        opportunity_signals=["online ordering", "appointment booking"],
        evidence_text=(
            "The website provides online ordering and automated appointment booking."
        ),
    )

    assert result["opportunity_score"] == 25
    assert len(result["matched_opportunity_signals"]) == 2


def test_score_never_exceeds_100():
    company = {
        "id": uuid4(),
        "name": "Example Agency",
        "industry": "web development agency",
        "description": "An agency providing AI automation and web development services to businesses.",
        "location_text": "10 Main Street, Austin, TX",
        "source_urls": ["https://example.com"],
    }

    result = score_lead(
        company=company,
        target_industries=["web development agency"],
        opportunity_signals=["AI automation", "web development"],
        evidence_text="AI automation web development." * 100,
        official_source_count=3,
        extracted_source_count=2,
        contact={"email_validation_status": "mx_valid", "phone": "+15125550100"},
    )

    assert 0 <= result["fit_score"] <= 100
