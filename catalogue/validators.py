import re

from django.core.exceptions import ValidationError


def normalize_isrc(value):
    """Accept human-readable ISRC punctuation; retain no invented values."""
    normalized = value.strip().upper().replace("-", "").replace(" ", "")
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}", normalized):
        raise ValidationError(
            "Skriv inn en ISRC med 12 tegn, for eksempel NO-ABC-26-00001."
        )
    return normalized


def normalize_trade_item_number(value, scheme):
    normalized = value.strip().replace("-", "").replace(" ", "")
    lengths = {"UPC": {12}, "EAN": {8, 13}, "GTIN": {8, 12, 13, 14}}
    if not normalized.isdigit() or len(normalized) not in lengths[scheme]:
        expected = "/".join(str(length) for length in sorted(lengths[scheme]))
        raise ValidationError(f"{scheme} må inneholde {expected} sifre.")
    digits = [int(character) for character in normalized]
    check_sum = sum(
        digit * (3 if index % 2 == 0 else 1)
        for index, digit in enumerate(reversed(digits[:-1]))
    )
    if (10 - check_sum % 10) % 10 != digits[-1]:
        raise ValidationError(f"{scheme} har ugyldig kontrollsiffer.")
    return normalized


def validate_language(value):
    # Syntax only, not a registry assertion. Accept language-script-region tags.
    if value and not re.fullmatch(
        r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", value
    ):
        raise ValidationError("Bruk en språkkode som nb, en eller zh-Hant.")
