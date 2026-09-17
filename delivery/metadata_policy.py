from enum import StrEnum


class MetadataVisibility(StrEnum):
    PUBLIC = "public"
    DELIVERABLE = "deliverable"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"


TAG_VISIBILITY = {
    "TITLE": MetadataVisibility.PUBLIC,
    "ARTIST": MetadataVisibility.PUBLIC,
    "ALBUM": MetadataVisibility.PUBLIC,
    "ALBUMARTIST": MetadataVisibility.PUBLIC,
    "TRACKNUMBER": MetadataVisibility.PUBLIC,
    "DISCNUMBER": MetadataVisibility.PUBLIC,
    "DATE": MetadataVisibility.PUBLIC,
    "YEAR": MetadataVisibility.PUBLIC,
    "ISRC": MetadataVisibility.DELIVERABLE,
    "GENRE": MetadataVisibility.DELIVERABLE,
    "LANGUAGE": MetadataVisibility.DELIVERABLE,
    "P7UUID": MetadataVisibility.INTERNAL,
    "RATING": MetadataVisibility.INTERNAL,
    "ENERGY": MetadataVisibility.INTERNAL,
    "KANAL": MetadataVisibility.INTERNAL,
    "TARGET": MetadataVisibility.INTERNAL,
    "GENDER": MetadataVisibility.INTERNAL,
    "ROTATION": MetadataVisibility.INTERNAL,
    "RIGHTS_EVIDENCE": MetadataVisibility.CONFIDENTIAL,
}


def external_radio_tags(raw_tags):
    """Allow known public/deliverable tags; unknown tags stay private."""
    allowed = {MetadataVisibility.PUBLIC, MetadataVisibility.DELIVERABLE}
    return {
        str(key).upper(): [str(value) for value in values]
        for key, values in raw_tags.items()
        if TAG_VISIBILITY.get(
            str(key).upper(), MetadataVisibility.CONFIDENTIAL
        )
        in allowed
    }
