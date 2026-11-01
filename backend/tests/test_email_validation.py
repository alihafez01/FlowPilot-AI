
import dns.resolver
import pytest

from app.services.email_validation import (
    validate_email_address,
    check_domain_mail_route,
)


class FakeMX:
    def __init__(self, exchange, preference=10):
        self.exchange = exchange
        self.preference = preference


class FakeAddress:
    def __init__(self, address):
        self.address = address


class FakeResolver:
    def __init__(self, responses):
        self.responses = responses

    def resolve(self, domain, record_type):
        value = self.responses.get(record_type)

        if isinstance(value, Exception):
            raise value

        if value is None:
            raise dns.resolver.NoAnswer()

        return value


def test_missing_email():
    result = validate_email_address(None)

    assert result["status"] == "missing"
    assert result["normalized_email"] is None


def test_invalid_email_syntax():
    result = validate_email_address(
        "this-is-not-an-email",
        check_dns=False,
    )

    assert result["status"] == "invalid"


def test_email_syntax_normalization():
    result = validate_email_address(
        "  SALES@example.com  ",
        check_dns=False,
    )

    assert result["status"] == "syntax_valid"
    assert result["email"] == "SALES@example.com"
    assert result["normalized_email"] == "sales@example.com"


def test_role_account_detection():
    result = validate_email_address(
        "info@example.com",
        check_dns=False,
    )

    assert result["is_role_account"] is True


def test_mx_record_success():
    resolver = FakeResolver({
        "MX": [FakeMX("mail.example.com.")],
    })

    result = check_domain_mail_route(
        "example.com",
        resolver=resolver,
    )

    assert result["status"] == "mx_valid"
    assert result["mx_hosts"] == ["mail.example.com"]


def test_null_mx():
    resolver = FakeResolver({
        "MX": [FakeMX(".")],
    })

    result = check_domain_mail_route(
        "example.com",
        resolver=resolver,
    )

    assert result["status"] == "no_mail_route"


def test_a_record_fallback():
    resolver = FakeResolver({
        "MX": dns.resolver.NoAnswer(),
        "A": [FakeAddress("8.8.8.8")],
    })

    result = check_domain_mail_route(
        "example.com",
        resolver=resolver,
    )

    assert result["status"] == "dns_fallback_valid"


def test_no_mail_route():
    resolver = FakeResolver({
        "MX": dns.resolver.NoAnswer(),
        "A": dns.resolver.NoAnswer(),
        "AAAA": dns.resolver.NoAnswer(),
    })

    result = check_domain_mail_route(
        "example.com",
        resolver=resolver,
    )

    assert result["status"] == "no_mail_route"


def test_dns_failure_is_not_syntax_failure():
    resolver = FakeResolver({
        "MX": dns.exception.Timeout(),
    })

    result = check_domain_mail_route(
        "example.com",
        resolver=resolver,
    )

    assert result["status"] == "dns_error"


@pytest.mark.parametrize(
    "email",
    [
        "info@example.com",
        "sales@example.org",
        "contact@example.net",
    ],
)
def test_valid_syntax_examples(email):
    result = validate_email_address(
        email,
        check_dns=False,
    )

    assert result["status"] == "syntax_valid"
