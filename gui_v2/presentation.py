RADIO_LANGUAGE_NAMES = {
    "no": "Norsk",
    "nb": "Norsk bokmål",
    "nn": "Norsk nynorsk",
    "sv": "Svensk",
    "da": "Dansk",
    "en": "Engelsk",
    "de": "Tysk",
    "fr": "Fransk",
    "es": "Spansk",
    "fi": "Finsk",
    "is": "Islandsk",
    "he": "Hebraisk",
    "smi": "Samisk",
}


def radio_language_name(value):
    value = (value or "").strip()
    if not value:
        return ""
    normalized = value.casefold().replace("_", "-")
    return RADIO_LANGUAGE_NAMES.get(
        normalized,
        RADIO_LANGUAGE_NAMES.get(normalized.split("-", 1)[0], value),
    )


def observed_genres(values):
    genres = {
        genre.strip()
        for raw_value in values
        for genre in (raw_value or "").split(";")
        if genre.strip()
    }
    return sorted(genres, key=str.casefold)


def compact_names(values, *, visible=2):
    names = [str(value) for value in values]
    if len(names) <= visible:
        return " · ".join(names)
    return f"{' · '.join(names[:visible])} · +{len(names) - visible}"
