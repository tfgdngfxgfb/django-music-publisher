from django.core.exceptions import ValidationError

from catalogue.validators import validate_language

# OneTagger also uses controlled group labels which are not language codes.
# Keep the allowlist explicit and preserve the original FLAC value in provenance.
RADIO_LANGUAGE_GROUPS = frozenset({"Afrikanske språk"})


def validate_radio_language(value):
    if value in RADIO_LANGUAGE_GROUPS:
        return
    try:
        validate_language(value)
    except ValidationError as error:
        raise ValidationError(
            "Bruk en språkkode som nb eller en, eller en støttet "
            "OneTagger-samlebetegnelse."
        ) from error
