import pytest

from app.services.deduplication import (
    normalize_company_name,
    normalize_domain,
    create_identity_key,
    coordinate_distance_km,
    find_duplicate_candidates,
)


def test_company_name_normalization():
    assert normalize_company_name("Example Inc.") == "example"


def test_domain_normalization():
    assert normalize_domain("https://www.Example.com/about") == "example.com"


def test_domain_from_plain_text():
    assert normalize_domain("www.example.com") == "example.com"


def test_domain_input_rejects_invalid_url():
    assert normalize_domain("not a domain") is None


@pytest.mark.parametrize("value", ["example .com", "example..com"])
def test_domain_input_rejects_malformed_hosts(value):
    assert normalize_domain(value) is None


def test_identity_key_from_website():
    key = create_identity_key(
        name="Example Agency",
        website="https://www.example.com",
    )
    assert key == "domain:example.com"


def test_identity_key_from_provider():
    key = create_identity_key(
        name="Example Business",
        provider="osm",
        provider_id="node:12345",
    )
    assert key == "osm:node:12345"


def test_identity_key_requires_fallback_location():
    with pytest.raises(ValueError):
        create_identity_key(name="Example Business")


def test_name_location_duplicate_candidate_without_coordinates():
    companies = [
        {
            "id": "1",
            "name": "Example Digital Agency LLC",
            "location": "200 Main Street, Austin, Texas",
        },
        {
            "id": "2",
            "name": "Example Digital Agency",
            "location": "200 Main Street, Austin, Texas",
        },
    ]

    result = find_duplicate_candidates(companies, threshold=90)
    assert len(result) == 1
    assert result[0]["similarity_score"] >= 90
    assert "street addresses match" in result[0]["match_reason"]


def test_city_only_text_without_coordinates_is_not_enough_to_suggest_duplicate():
    companies = [
        {"id": "1", "name": "Example Agency", "location": "Austin, Texas"},
        {"id": "2", "name": "Example Agency", "location": "Austin, Texas"},
    ]
    assert find_duplicate_candidates(companies) == []


def test_different_locations_not_matched_without_coordinates():
    companies = [
        {"id": "1", "name": "Example Agency", "location": "Austin, Texas"},
        {"id": "2", "name": "Example Agency", "location": "Dallas, Texas"},
    ]
    assert find_duplicate_candidates(companies) == []


def test_same_name_and_nearby_coordinates_are_suggested():
    companies = [
        {
            "id": "1",
            "name": "Half Price Books",
            "location_text": "Austin, Texas",
            "latitude": 30.3237706,
            "longitude": -97.7265395,
        },
        {
            "id": "2",
            "name": "Half Price Books",
            "location_text": "Austin, Texas",
            "latitude": 30.3237710,
            "longitude": -97.7265390,
        },
    ]
    result = find_duplicate_candidates(companies)
    assert len(result) == 1
    assert "Coordinates are within" in result[0]["match_reason"]


def test_same_city_but_distant_branches_are_not_duplicates():
    # These are the two Half Price Books coordinates from the user's database.
    companies = [
        {
            "id": "1",
            "name": "Half Price Books",
            "location_text": "Austin, Travis County, Texas, United States",
            "latitude": 30.3237706,
            "longitude": -97.7265395,
        },
        {
            "id": "2",
            "name": "Half Price Books",
            "location_text": "Austin, Travis County, Texas, United States",
            "latitude": 30.4489286,
            "longitude": -97.7919328,
        },
    ]
    assert find_duplicate_candidates(companies) == []


def test_gamestop_branches_are_not_duplicates():
    companies = [
        {
            "id": "1",
            "name": "GameStop",
            "location_text": "Austin, Travis County, Texas, United States",
            "latitude": 30.3955717,
            "longitude": -97.7275147,
        },
        {
            "id": "2",
            "name": "GameStop",
            "location_text": "200, Brodie Lane",
            "latitude": 30.183679,
            "longitude": -97.8482015,
        },
    ]
    assert find_duplicate_candidates(companies) == []


def test_coordinate_distance_is_reasonable():
    distance = coordinate_distance_km(30.0, -97.0, 30.0, -97.0)
    assert distance == pytest.approx(0.0)


def test_invalid_or_incomplete_coordinates_return_none():
    assert coordinate_distance_km(91, 0, 30, -97) is None
    assert coordinate_distance_km(None, 0, 30, -97) is None
