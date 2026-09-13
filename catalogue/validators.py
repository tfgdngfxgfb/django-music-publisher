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


def validate_language(value):
    # Syntax only, not a registry assertion. Accept language-script-region tags.
    if value and not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", value):
        raise ValidationError("Bruk en språkkode som nb, en eller zh-Hant.")
