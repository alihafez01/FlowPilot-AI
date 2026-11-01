
import dns.exception
import dns.resolver

from email_validator import (
    EmailNotValidError,
    validate_email,
)


ROLE_ACCOUNTS = {
    "info",
    "contact",
    "sales",
    "support",
    "hello",
    "admin",
    "office",
    "marketing",
    "enquiries",
    "inquiries",
    "business",
}


def create_dns_resolver():
    """
    Create a resolver with bounded DNS timeouts and local caching.
    """
    resolver = dns.resolver.Resolver()

    resolver.timeout = 2
    resolver.lifetime = 4

    resolver.cache = dns.resolver.LRUCache()

    return resolver


def check_domain_mail_route(
    domain: str,
    resolver=None,
) -> dict:
    """
    Check whether the domain has an MX record or a valid
    A/AAAA fallback.

    This never verifies an individual mailbox.
    """
    resolver = resolver or create_dns_resolver()

    try:
        mx_answers = resolver.resolve(domain, "MX")

        mx_hosts = [
            str(item.exchange).rstrip(".")
            for item in mx_answers
        ]

        # RFC 7505 Null MX: domain explicitly accepts no email.
        if mx_hosts and all(host == "" for host in mx_hosts):
            return {
                "status": "no_mail_route",
                "mx_hosts": [],
                "reason": "Domain publishes a Null MX record.",
            }

        valid_mx = [
            host for host in mx_hosts if host
        ]

        if valid_mx:
            return {
                "status": "mx_valid",
                "mx_hosts": valid_mx,
                "reason": "Domain has MX records.",
            }

        return {
            "status": "dns_error",
            "mx_hosts": [],
            "reason": "DNS returned an unexpected MX response.",
        }

    except dns.resolver.NoAnswer:
        # No MX record: check RFC-compatible A/AAAA fallback.
        pass

    except dns.resolver.NXDOMAIN:
        return {
            "status": "no_mail_route",
            "mx_hosts": [],
            "reason": "Email domain does not exist in DNS.",
        }

    except (
        dns.exception.Timeout,
        dns.resolver.NoNameservers,
        dns.exception.DNSException,
    ):
        return {
            "status": "dns_error",
            "mx_hosts": [],
            "reason": "DNS query failed or timed out.",
        }

    for record_type in ("A", "AAAA"):
        try:
            answer = resolver.resolve(domain, record_type)

            # Accept only globally routable addresses.
            addresses = [
                str(item.address)
                for item in answer
            ]

            import ipaddress

            valid_addresses = []

            for address in addresses:
                parsed = ipaddress.ip_address(address)

                if parsed.is_global:
                    valid_addresses.append(address)

            if valid_addresses:
                return {
                    "status": "dns_fallback_valid",
                    "mx_hosts": [],
                    "reason": (
                        f"No MX record; public {record_type} "
                        "record is available."
                    ),
                }

        except (
            dns.resolver.NoAnswer,
            dns.resolver.NXDOMAIN,
        ):
            continue

        except (
            dns.exception.Timeout,
            dns.resolver.NoNameservers,
            dns.exception.DNSException,
        ):
            return {
                "status": "dns_error",
                "mx_hosts": [],
                "reason": "Fallback DNS query failed.",
            }

    return {
        "status": "no_mail_route",
        "mx_hosts": [],
        "reason": "No usable MX, A or AAAA mail route found.",
    }


def validate_email_address(
    raw_email: str | None,
    check_dns: bool = True,
    resolver=None,
) -> dict:
    """
    Validate email syntax, normalize the email, then optionally
    check domain DNS mail routing.
    """
    if not raw_email or not raw_email.strip():
        return {
            "email": raw_email,
            "normalized_email": None,
            "email_dedupe_key": None,
            "domain": None,
            "status": "missing",
            "dns_status": "not_checked",
            "mx_hosts": [],
            "is_role_account": False,
            "mailbox_verified": False,
            "reason": "No email address was provided.",
        }

    raw_email = raw_email.strip()

    try:
        validated = validate_email(
            raw_email,
            check_deliverability=False,
            allow_display_name=False,
        )

    except EmailNotValidError as exc:
        return {
            "email": raw_email,
            "normalized_email": None,
            "email_dedupe_key": None,
            "domain": None,
            "status": "invalid",
            "dns_status": "not_checked",
            "mx_hosts": [],
            "is_role_account": False,
            "mailbox_verified": False,
            "reason": str(exc),
        }

    normalized = validated.normalized
    local_part, domain = normalized.rsplit("@", 1)

    is_role_account = (
        local_part.split("+", 1)[0].casefold()
        in ROLE_ACCOUNTS
    )

    if not check_dns:
        return {
            "email": raw_email,
            "normalized_email": normalized,
            "email_dedupe_key": normalized.casefold(),
            "domain": domain,
            "status": "syntax_valid",
            "dns_status": "not_checked",
            "mx_hosts": [],
            "is_role_account": is_role_account,
            "mailbox_verified": False,
            "reason": "Syntax passed; DNS was not checked.",
        }

    dns_result = check_domain_mail_route(
        domain,
        resolver=resolver,
    )

    dns_status = dns_result["status"]

    # Syntax and DNS are separate signals; never call a DNS error
    # a syntactically invalid email.
    return {
        "email": raw_email,
        "normalized_email": normalized,
        "email_dedupe_key": normalized.casefold(),
        "domain": domain,
        "status": dns_status,
        "dns_status": dns_status,
        "mx_hosts": dns_result["mx_hosts"],
        "is_role_account": is_role_account,
        "mailbox_verified": False,
        "reason": dns_result["reason"],
    }
