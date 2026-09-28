"""Table-driven merchant cleanup tests (SPEC.md §5.4, Phase 2 checklist: >= 30 patterns).

Descriptions follow common public card/ACH descriptor formats; none come from a real
statement. Reference numbers, phone numbers and IDs are made up.
"""

import pytest

from spendsight.enrich.merchants import normalize_key

CASES = [
    # Payment-processor prefixes
    ("SQ *BLUE BOTTLE COFFEE", "BLUE BOTTLE COFFEE"),
    ("SQ*BLUE BOTTLE COFFEE", "BLUE BOTTLE COFFEE"),
    ("  sq *blue   bottle coffee  ", "BLUE BOTTLE COFFEE"),
    ("TST* SWEETGREEN 123", "SWEETGREEN"),
    ("TST*SHAKE SHACK #1234", "SHAKE SHACK"),
    ("PAYPAL *SPOTIFY", "SPOTIFY"),
    ("PAYPAL *EBAY INC 4025550100 CA", "EBAY INC"),
    ("SP * ALLBIRDS", "ALLBIRDS"),
    ("SP ALLBIRDS", "ALLBIRDS"),
    ("DD *DOORDASH CHIPOTLE", "DOORDASH CHIPOTLE"),
    # Store numbers and locations
    ("STARBUCKS STORE 12345", "STARBUCKS"),
    ("STARBUCKS STORE 12345 SEATTLE WA", "STARBUCKS"),
    ("WAL-MART #1234", "WAL-MART"),
    ("TARGET        00012345", "TARGET"),
    ("SHELL OIL 57444353 SAN JOSE CA", "SHELL OIL"),
    ("CHEVRON 0201234", "CHEVRON"),
    ("MCDONALD'S F12345", "MCDONALD'S"),
    ("TRADER JOE S #123  QPS", "TRADER JOE S"),
    ("CVS/PHARMACY #01234", "CVS/PHARMACY"),
    ("COSTCO WHSE #0123", "COSTCO WHSE"),
    ("BLUE BOTTLE COFFEE    OAKLAND      CA", "BLUE BOTTLE COFFEE"),
    ("7-ELEVEN 12345", "7-ELEVEN"),
    # Reference codes, billing domains, phone numbers
    ("AMZN MKTP US*2K4AB1C23", "AMZN MKTP US"),
    ("AMAZON.COM*2K4AB1C23 AMZN.COM/BILL WA", "AMAZON.COM"),
    ("NETFLIX.COM 866-555-0100 CA", "NETFLIX.COM"),
    ("HULU 877-5550100 CA", "HULU"),
    ("APPLE.COM/BILL 866-555-0199 CA", "APPLE.COM"),
    ("UBER *TRIP HELP.UBER.COM CA", "UBER TRIP"),
    ("UBER *EATS", "UBER EATS"),
    ("GOOGLE *YOUTUBE PREMIUM G.CO/HELPPAY#", "GOOGLE YOUTUBE PREMIUM"),
    # Checking: debit-card wrappers and trailing dates
    ("WHOLEFDS MKT 10234 AUSTIN TX 01/05", "WHOLEFDS MKT"),
    ("CARD PURCHASE 01/05 WHOLEFDS MKT 10234 AUSTIN TX", "WHOLEFDS MKT"),
    ("RECURRING CARD PURCHASE 01/05 NETFLIX.COM", "NETFLIX.COM"),
    ("CARD PURCHASE WITH PIN 01/05 SAFEWAY #1234", "SAFEWAY"),
    # Checking: ACH, P2P, ATM, checks, fees
    ("ACME PROPERTY MGMT WEB PMTS PPD ID: 0000000000", "ACME PROPERTY MGMT WEB PMTS"),
    ("CHASE CREDIT CRD AUTOPAY PPD ID: 0000000000", "CHASE CREDIT CRD AUTOPAY"),
    ("VENMO PAYMENT 1000000000 WEB ID: 0000000000", "VENMO PAYMENT"),
    ("ZELLE PAYMENT TO JANE SAMPLE 12345678", "ZELLE PAYMENT TO JANE SAMPLE"),
    ("Payment to Chase card ending in 0000 01/15", "PAYMENT TO CHASE CARD ENDING IN"),
    ("ATM WITHDRAWAL 000123 01/20 123 MAIN ST", "ATM WITHDRAWAL"),
    ("CHECK 1001", "CHECK"),
    ("MONTHLY SERVICE FEE", "MONTHLY SERVICE FEE"),
    ("LATE FEE", "LATE FEE"),
    # Card-side payments are left alone
    ("Payment Thank You-Mobile", "PAYMENT THANK YOU-MOBILE"),
    ("AUTOMATIC PAYMENT - THANK", "AUTOMATIC PAYMENT - THANK"),
    # Nothing left after cleanup: fall back rather than return an empty key
    ("SQ *", "SQ *"),
]


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_normalize_key(raw: str, expected: str) -> None:
    assert normalize_key(raw) == expected


def test_at_least_thirty_patterns() -> None:
    assert len(CASES) >= 30


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_normalizing_a_key_is_stable(raw: str, expected: str) -> None:
    # Keys are stored; re-running cleanup on a key must not change it again.
    assert normalize_key(normalize_key(raw)) == normalize_key(raw)


def test_variants_share_a_key() -> None:
    variants = [
        "SQ *BLUE BOTTLE COFFEE",
        "BLUE BOTTLE COFFEE    OAKLAND      CA",
        "blue bottle coffee",
    ]
    assert len({normalize_key(v) for v in variants}) == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ACME CO", "ACME CO"),  # CO as "company", not Colorado
        ("SHOP IN", "SHOP IN"),
        ("SOMEPLACE CA 12345", "SOMEPLACE"),  # state exposed by truncation is dropped
        ("GOOD EATS    DENVER      CO", "GOOD EATS"),  # fixed-width field: CO is a state
    ],
)
def test_ambiguous_state_codes(raw: str, expected: str) -> None:
    assert normalize_key(raw) == expected
    assert normalize_key(expected) == expected
