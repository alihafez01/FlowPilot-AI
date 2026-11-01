from app.services.source_classification import classify_page_kind


def candidate(url, domain, title, snippet=""):
    return {
        "url": url,
        "domain": domain,
        "title": title,
        "snippet": snippet,
    }


def test_semrush_category_page_is_not_a_company_website():
    item = candidate(
        "https://agencies.semrush.com/list/marketing-automation/austin",
        "agencies.semrush.com",
        "Best B2B Services Digital Marketing Agencies in Austin 2026",
    )
    assert classify_page_kind(item) == "discovery_page"


def test_built_in_company_category_is_discovery_page():
    item = candidate(
        "https://www.builtinaustin.com/companies/type/enterprise-web-companies",
        "builtinaustin.com",
        "Top Enterprise Web Companies in Austin, TX 2026",
    )
    assert classify_page_kind(item) == "discovery_page"


def test_digital_agency_network_list_is_discovery_page():
    item = candidate(
        "https://digitalagencynetwork.com/agencies/austin",
        "digitalagencynetwork.com",
        "Best Digital Marketing Agencies in Austin",
    )
    assert classify_page_kind(item) == "discovery_page"


def test_manifest_category_page_is_discovery_page():
    item = candidate(
        "https://themanifest.com/digital-marketing/agencies/austin",
        "themanifest.com",
        "The Best Digital Marketing Companies in Austin - Oct 2026",
    )
    assert classify_page_kind(item) == "discovery_page"


def test_inbeat_blog_listicle_is_discovery_page():
    item = candidate(
        "https://inbeat.agency/blog/top-digital-agencies-austin",
        "inbeat.agency",
        "Digital Marketing Agencies in Austin: Verified Picks",
    )
    assert classify_page_kind(item) == "discovery_page"


def test_generic_listicle_on_unknown_domain_is_discovery_page():
    item = candidate(
        "https://example.org/best-agencies-austin",
        "example.org",
        "Best Web Development Agencies in Austin",
    )
    assert classify_page_kind(item) == "discovery_page"


def test_directory_profile_is_not_a_business_website():
    item = candidate(
        "https://agencies.semrush.com/profile/example-agency",
        "agencies.semrush.com",
        "Example Agency Profile",
    )
    assert classify_page_kind(item) == "directory_listing"


def test_direct_business_website_remains_a_candidate():
    item = candidate(
        "https://thriveagency.com/austin-digital-marketing-agency",
        "thriveagency.com",
        "Austin Digital Marketing Agency",
        "Digital marketing services for growing businesses.",
    )
    assert classify_page_kind(item) == "business_website"
