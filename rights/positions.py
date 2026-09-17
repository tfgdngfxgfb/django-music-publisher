"""Pure legal identity and Release-context selection shared by 6H and 6I."""

from rights.scope import (
    RELEVANT_STATUSES,
    claim_applies_to_release,
    claim_countries,
)


def legal_position_identity(claim):
    cached = getattr(claim, "_prefetched_objects_cache", {})
    if "territories" not in cached:
        raise ValueError("Load territories before evaluating legal identity.")
    fields = (
        "recording_id",
        "right_type",
        "rights_holder_id",
        "grantor_id",
        "share",
        "territory_mode",
        "valid_from",
        "valid_until",
        "release_scope_id",
    )
    return tuple(getattr(claim, field) for field in fields) + (
        tuple(sorted(str(t.pk) for t in cached["territories"])),
    )


def claims_applicable_in_release_context(claims, release, local):
    """Relevant local positions; date and confirmation remain explicit callers' choices."""
    local_id = getattr(local, "pk", local)
    return tuple(
        c
        for c in claims
        if local_id is not None
        and c.rights_holder_id == local_id
        and c.status in RELEVANT_STATUSES
        and claim_applies_to_release(c, release)
        and claim_countries(c)
    )
