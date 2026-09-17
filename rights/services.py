from django.core.exceptions import ValidationError
from django.db import transaction

from catalogue.models import Recording
from rights_core.models import VerificationStatus

from .models import (
    ClaimTerritory,
    RightsClaim,
    RightsConfiguration,
    RightsDecision,
)
from .scope import find_ownership_conflict, prepare_claims

DECISION_TRANSITIONS = {
    VerificationStatus.UNVERIFIED: frozenset(
        (
            VerificationStatus.CONFIRMED,
            VerificationStatus.DISPUTED,
            VerificationStatus.REJECTED,
        )
    ),
    VerificationStatus.CONFIRMED: frozenset(
        (
            VerificationStatus.DISPUTED,
            VerificationStatus.REJECTED,
        )
    ),
    VerificationStatus.DISPUTED: frozenset(
        (
            VerificationStatus.CONFIRMED,
            VerificationStatus.REJECTED,
        )
    ),
    VerificationStatus.REJECTED: frozenset(
        (
            VerificationStatus.CONFIRMED,
            VerificationStatus.DISPUTED,
        )
    ),
    VerificationStatus.SUPERSEDED: frozenset(),
}


def _validate_territory_scope(mode, territories):
    territories = tuple(territories)
    if mode == RightsClaim.TerritoryMode.WORLD and territories:
        raise ValidationError(
            "Hele verden kan ikke kombineres med en territorieliste."
        )
    if mode != RightsClaim.TerritoryMode.WORLD and not territories:
        raise ValidationError("Velg minst ett territorium for dette omfanget.")
    return territories


def validate_confirmed_ownership_total(claim):
    if (
        claim.right_type != RightsClaim.RightType.OWNERSHIP
        or claim.share is None
    ):
        return
    others = RightsClaim.objects.filter(
        recording=claim.recording,
        right_type=RightsClaim.RightType.OWNERSHIP,
        status=VerificationStatus.CONFIRMED,
    ).exclude(pk=claim.pk)
    conflict = find_ownership_conflict(
        prepare_claims((claim, *others.prefetch_related("territories")))
    )
    if conflict:
        raise ValidationError(
            f"Bekreftede eierandeler overstiger 100 % i {conflict.territory} "
            f"på {conflict.on_date}: {conflict.total} %."
        )


@transaction.atomic
def create_rights_claim(*, territories=(), **values):
    recording_id = (
        values["recording"].pk
        if "recording" in values
        else values["recording_id"]
    )
    Recording.objects.select_for_update().get(pk=recording_id)
    values.pop("status", None)
    selected = _validate_territory_scope(
        values.get("territory_mode", RightsClaim.TerritoryMode.WORLD),
        territories,
    )
    claim = RightsClaim.objects.create(
        status=VerificationStatus.UNVERIFIED, **values
    )
    for territory in selected:
        ClaimTerritory.objects.create(claim=claim, territory=territory)
    claim.full_clean()
    return claim


@transaction.atomic
def decide_rights_claim(claim, decision, *, user, note=""):
    if decision not in {
        VerificationStatus.CONFIRMED,
        VerificationStatus.DISPUTED,
        VerificationStatus.REJECTED,
    }:
        raise ValueError("Ugyldig beslutning for rettighetskrav.")
    recording = Recording.objects.select_for_update().get(
        pk=RightsClaim.objects.values_list("recording_id", flat=True).get(
            pk=claim.pk
        )
    )
    claim = (
        RightsClaim.objects.select_for_update()
        .prefetch_related("territories")
        .get(pk=claim.pk)
    )
    if decision not in DECISION_TRANSITIONS[claim.status]:
        raise ValidationError(
            "Beslutningen er ikke tillatt fra denne statusen. Erstattede krav "
            "er avsluttet; bruk dokumentasjon for merknader uten statusendring."
        )
    if decision == VerificationStatus.CONFIRMED:
        validate_confirmed_ownership_total(claim)
    result = RightsDecision.objects.create(
        claim=claim, decision=decision, decided_by=user, note=note
    )
    claim.status = decision
    claim._allow_status_transition = True
    claim.save(update_fields=("status",))
    from managed_music.lifecycle import refresh_management_status

    refresh_management_status(recording)
    return result


def supersede_rights_claim(
    previous, *, user, territories=(), note="", **values
):
    """Compatibility helper for a single replacement position."""
    return supersede_rights_claims(
        previous,
        user=user,
        replacements=({**values, "territories": territories},),
        note=note,
    )[0]


@transaction.atomic
def supersede_rights_claims(previous, *, user, replacements, note=""):
    """Replace one legal position atomically with one or more new positions."""
    recording = Recording.objects.select_for_update().get(
        pk=RightsClaim.objects.values_list("recording_id", flat=True).get(
            pk=previous.pk
        )
    )
    previous = RightsClaim.objects.select_for_update().get(pk=previous.pk)
    if previous.status == VerificationStatus.SUPERSEDED:
        raise ValidationError("Kravet er allerede erstattet.")
    replacements = tuple(replacements)
    if not replacements:
        raise ValidationError("Angi minst ett erstatningskrav.")
    prepared = []
    for replacement in replacements:
        values = dict(replacement)
        if (
            values.get("right_type", previous.right_type)
            != previous.right_type
        ):
            raise ValidationError(
                "Erstatningskrav må ha samme rettighetstype."
            )
        recording_id = values.get(
            "recording_id",
            getattr(values.get("recording"), "pk", previous.recording_id),
        )
        if str(recording_id) != str(previous.recording_id):
            raise ValidationError(
                "Erstatningskrav må gjelde samme innspilling."
            )
        values.pop("recording_id", None)
        values.pop("supersedes_id", None)
        if "release_scope" not in values and "release_scope_id" not in values:
            values["release_scope_id"] = previous.release_scope_id
        values.update(
            recording=recording,
            right_type=previous.right_type,
            supersedes=previous,
        )
        # Validate every row before creating any replacement/audit.
        territories = _validate_territory_scope(
            values.get("territory_mode", RightsClaim.TerritoryMode.WORLD),
            values.pop("territories", ()),
        )
        values.pop("status", None)
        RightsClaim(**values).full_clean()
        prepared.append((values, territories))
    created = [
        create_rights_claim(territories=territories, **values)
        for values, territories in prepared
    ]
    RightsDecision.objects.create(
        claim=previous,
        decision=VerificationStatus.SUPERSEDED,
        decided_by=user,
        note=f"Erstattet av: {', '.join(str(c.pk) for c in created)}. {note}".strip(),
    )
    for replacement in created:
        RightsDecision.objects.create(
            claim=replacement,
            decided_by=user,
            decision=RightsDecision.Decision.DOCUMENTED,
            note=f"Manuell registrering: erstatter {previous.pk}.",
        )
    previous.status = VerificationStatus.SUPERSEDED
    previous._allow_status_transition = True
    previous.save(update_fields=("status",))
    from managed_music.lifecycle import refresh_management_status

    refresh_management_status(recording)
    return created


_UNSET = object()


def link_claim_agreement(claim, agreement, *, user, note=""):
    return document_rights_claim(
        claim, user=user, agreement=agreement, note=note
    )


@transaction.atomic
def document_rights_claim(
    claim, *, user, agreement=_UNSET, evidence_strength=_UNSET, note=""
):
    """Audit documentation only; never change legal scope or source provenance."""
    Recording.objects.select_for_update().get(
        pk=RightsClaim.objects.values_list("recording_id", flat=True).get(
            pk=claim.pk
        )
    )
    claim = RightsClaim.objects.select_for_update().get(pk=claim.pk)
    changes = []
    fields = []
    for field, value in (
        ("agreement", agreement),
        ("evidence_strength", evidence_strength),
    ):
        if value is _UNSET:
            continue
        old = getattr(claim, field)
        if old != value:
            changes.append(
                f"{field}: {getattr(old, 'pk', old)} → {getattr(value, 'pk', value)}"
            )
            setattr(claim, field, value)
            fields.append(field)
    if not changes and not note.strip():
        raise ValidationError("Angi endret dokumentasjon eller en merknad.")
    if fields:
        claim._allow_claim_update = True
        claim.save(update_fields=fields)
        del claim._allow_claim_update
    history_note = "; ".join(changes)
    if note:
        history_note += f" {note}"
    RightsDecision.objects.create(
        claim=claim,
        decision=RightsDecision.Decision.DOCUMENTED,
        decided_by=user,
        note=history_note,
    )
    return claim


def get_local_organization():
    configuration = RightsConfiguration.objects.select_related(
        "local_organization"
    ).first()
    return configuration.local_organization if configuration else None


@transaction.atomic
def create_release_rights_claims(
    *,
    release,
    recordings,
    allow_managed_registration=False,
    **claim_values,
):
    """Create recording claims from one release-level registration workflow."""
    scope = claim_values.get("release_scope")
    scope_id = claim_values.get("release_scope_id", getattr(scope, "pk", None))
    if scope_id is not None and str(scope_id) != str(release.pk):
        raise ValidationError(
            "Rettighetens utgivelsesscope må være denne utgivelsen."
        )
    recording_ids = {recording.pk for recording in recordings}
    if not recording_ids:
        raise ValidationError("Velg minst én innspilling fra utgivelsen.")
    release_recording_ids = set(
        release.tracks.filter(recording_id__in=recording_ids).values_list(
            "recording_id", flat=True
        )
    )
    if release_recording_ids != recording_ids:
        raise ValidationError(
            "Alle valgte innspillinger må tilhøre utgivelsen."
        )

    configuration = RightsConfiguration.objects.select_related(
        "local_organization"
    ).first()
    from managed_music.models import ManagedRecording
    from music_library.models import MusicLibraryEntry

    selected_recordings = list(
        Recording.objects.select_for_update()
        .filter(pk__in=recording_ids)
        .order_by("pk")
    )
    managed_recording_ids = set(
        ManagedRecording.objects.filter(
            library_entry__recording_id__in=recording_ids
        ).values_list("library_entry__recording_id", flat=True)
    )
    claims = []
    for recording in selected_recordings:
        is_managed = recording.pk in managed_recording_ids
        if not is_managed:
            if not allow_managed_registration:
                raise ValidationError(
                    f"«{recording.title}» er ikke i Forvaltet musikk. Bare en "
                    "administrator kan registrere forvaltning fra utgivelsen."
                )
            if (
                not configuration
                or claim_values.get("rights_holder")
                != configuration.local_organization
            ):
                raise ValidationError(
                    "En ny forvaltningsregistrering må bygge på et krav for "
                    "konfigurert lokal organisasjon."
                )
            entry, _created = MusicLibraryEntry.objects.get_or_create(
                recording=recording
            )
            source_record = claim_values.get("source_record")
            ManagedRecording.objects.create(
                library_entry=entry,
                source_system=(
                    source_record.source_system if source_record else None
                ),
                notes=f"Registrert uttrykkelig fra utgivelsen «{release.title}».",
            )
        claims.append(create_rights_claim(recording=recording, **claim_values))
    return claims
