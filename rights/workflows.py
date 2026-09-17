"""Actor-authorized manual workflows; low-level import services remain separate."""

from dataclasses import dataclass
import hashlib
import json

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from catalogue.models import Recording, ReleaseTrack
from managed_music.models import ManagedRecording
from rights_core.models import VerificationStatus

from . import services
from .models import RightsClaim, RightsConfiguration, RightsDecision, Territory
from .scope import (
    find_ownership_conflict,
    prepare_claims,
    resolve_management_basis,
    territory_scope_countries,
)

CLAIM_FIELDS = frozenset(
    (
        "right_type",
        "rights_holder",
        "grantor",
        "share",
        "territory_mode",
        "territories",
        "valid_from",
        "valid_until",
        "release_scope",
        "evidence_strength",
        "source_record",
        "agreement",
        "notes",
    )
)
PLAN_SALT = "rights.release-registration.6e"


def require_permissions(user, *permissions):
    if (
        not user.is_authenticated
        or not user.is_active
        or not user.has_perms(permissions)
    ):
        raise PermissionDenied(
            "Du har ikke tillatelse til denne rettighetshandlingen."
        )


def require_onboarding(user):
    require_permissions(
        user, "managed_music.add_managedrecording", "rights.add_rightsclaim"
    )
    if not user.is_superuser:
        raise PermissionDenied(
            "Bare en administrator kan registrere forvaltning."
        )


def _claim_values(values):
    if set(values) - CLAIM_FIELDS:
        raise ValidationError("Uventede felt i rettighetsregistreringen.")
    values = dict(values)
    # Callers may retain objects from preview. Re-read referenced metadata, too.
    for name in (
        "rights_holder",
        "grantor",
        "release_scope",
        "source_record",
        "agreement",
    ):
        value = values.get(name)
        if value is not None:
            model = RightsClaim._meta.get_field(name).remote_field.model
            try:
                values[name] = model.objects.get(pk=value.pk)
            except model.DoesNotExist as error:
                raise ValidationError(
                    "En referanse er fjernet. Last grunnlaget på nytt."
                ) from error
    territory_ids = {t.pk for t in values.get("territories", ())}
    territories = tuple(
        Territory.objects.filter(pk__in=territory_ids).order_by("pk")
    )
    if len(territories) != len(territory_ids):
        raise ValidationError("Et valgt territorium er fjernet.")
    values["territories"] = services._validate_territory_scope(
        values.get("territory_mode", RightsClaim.TerritoryMode.WORLD),
        territories,
    )
    return values


def _audit_registration(claim, user, note):
    RightsDecision.objects.create(
        claim=claim,
        decided_by=user,
        decision=RightsDecision.Decision.DOCUMENTED,
        note=f"Manuell registrering. {note}".strip(),
    )


@transaction.atomic
def register_rights_claim(*, user, recording, **values):
    require_permissions(user, "rights.add_rightsclaim")
    recording = Recording.objects.select_for_update().get(pk=recording.pk)
    if not ManagedRecording.objects.filter(
        library_entry__recording=recording
    ).exists():
        raise ValidationError(
            "Registrer eksplisitt onboarding før et nytt rettighetskrav."
        )
    claim = services.create_rights_claim(
        recording=recording, **_claim_values(values)
    )
    _audit_registration(
        claim, user, "Registrert på eksisterende forvaltet innspilling."
    )
    return claim


def onboard_managed_recording(*, user, **values):
    require_onboarding(user)
    from managed_music.services import create_managed_recording

    return create_managed_recording(user=user, **values)


def _require_membership_for_live_basis(claim):
    """Historical corrections remain possible; live onboarding stays explicit.

    Caller holds the Recording lock and an atomic transaction. Check both
    current and future applicability using the shared 6C scope engine.
    """
    day = max(timezone.localdate(), claim.valid_from or timezone.localdate())
    basis = resolve_management_basis(
        claim.recording_id, claims=(claim,), on_date=day
    )
    if (
        basis.claims
        and not ManagedRecording.objects.filter(
            library_entry__recording_id=claim.recording_id
        ).exists()
    ):
        raise ValidationError(
            "Aktuelt eller framtidig lokalt P7-grunnlag krever eksplisitt "
            "onboarding før kravet kan registreres eller revurderes."
        )


@transaction.atomic
def decide_rights_claim(claim, decision, *, user, note=""):
    require_permissions(user, "rights.decide_rightsclaim")
    result = services.decide_rights_claim(
        claim, decision, user=user, note=note
    )
    current = RightsClaim.objects.get(pk=claim.pk)
    _require_membership_for_live_basis(current)
    return result


@transaction.atomic
def supersede_rights_claims(previous, *, user, replacements, note=""):
    require_permissions(
        user, "rights.add_rightsclaim", "rights.decide_rightsclaim"
    )
    replacements = tuple(_claim_values(row) for row in replacements)
    created = services.supersede_rights_claims(
        previous, user=user, replacements=replacements, note=note
    )
    for claim in created:
        _require_membership_for_live_basis(claim)
    return created


def supersede_rights_claim(previous, *, user, note="", **values):
    return supersede_rights_claims(
        previous, user=user, replacements=(values,), note=note
    )[0]


def document_rights_claim(claim, *, user, **values):
    require_permissions(user, "rights.change_rightsclaim")
    if "agreement" in values:
        require_permissions(user, "rights.manage_agreement")
    return services.document_rights_claim(claim, user=user, **values)


def link_claim_agreement(claim, agreement, *, user, note=""):
    return document_rights_claim(
        claim, user=user, agreement=agreement, note=note
    )


@transaction.atomic
def correct_legacy_management_history(managed, *, user, reason):
    """Explicitly certify erroneous legacy status; never erase real history."""
    require_permissions(
        user,
        "managed_music.change_managedrecording",
        "rights.decide_rightsclaim",
    )
    if not user.is_superuser:
        raise PermissionDenied(
            "Bare en administrator kan avklare legacy-historikk."
        )
    if not reason.strip():
        raise ValidationError("Begrunn hvorfor legacy-statusen var feil.")
    from flac_ingest.signals import suppress_automatic_flac_sync
    from managed_music.lifecycle import management_state
    from provenance.models import SourceRecord, SourceSystem

    recording_id = ManagedRecording.objects.values_list(
        "library_entry__recording_id", flat=True
    ).get(pk=managed.pk)
    Recording.objects.select_for_update().get(pk=recording_id)
    managed = ManagedRecording.objects.select_for_update().get(pk=managed.pk)
    state = management_state(managed)
    if (
        not state.history_uncertain
        or state.has_current_basis
        or state.has_confirmed_history
    ):
        raise ValidationError(
            "Bare usikker legacy-historikk uten bekreftet forvaltning kan korrigeres."
        )
    source, _ = SourceSystem.objects.get_or_create(
        name="P7 forvaltningshistorikk",
        defaults={"kind": SourceSystem.Kind.MANUAL},
    )
    audit = SourceRecord.objects.create(
        source_system=source,
        source_locator=f"Recording {recording_id}",
        raw_payload={
            "action": "correct_legacy_management_history",
            "recording_id": str(recording_id),
            "managed_recording_id": str(managed.pk),
            "library_entry_id": str(managed.library_entry_id),
            "performed_by_id": str(user.pk),
            "reason": reason.strip(),
            "previous_status": managed.status,
            "previous_revision": managed.revision,
            "new_status": ManagedRecording.Status.PENDING,
            "had_plausible_basis": state.has_plausible_basis,
        },
    )
    with suppress_automatic_flac_sync():
        managed.status = ManagedRecording.Status.PENDING
        managed.save(update_fields=("status",))
    return audit


@dataclass(frozen=True)
class RegistrationRow:
    recording: Recording
    managed: bool
    status: str


@dataclass(frozen=True)
class ReleaseRegistrationPlan:
    rows: tuple
    blockers: tuple
    token: str
    release_scoped: bool
    onboarding_count: int


def _snapshot_value(value):
    if hasattr(value, "pk"):
        return {"id": str(value.pk), "revision": value.revision}
    if isinstance(value, (list, tuple)):
        return sorted((_snapshot_value(v) for v in value), key=str)
    return None if value is None else str(value)


def preview_release_rights_registration(
    *, user, release, recordings, allow_managed_registration=False, **values
):
    """Read-only bounded selection, signed to actor, inputs and current revisions."""
    require_permissions(
        user, "catalogue.view_release", "rights.add_rightsclaim"
    )
    if allow_managed_registration:
        require_onboarding(user)
    values = _claim_values(values)
    ids = sorted({r.pk for r in recordings})
    if not ids or len(ids) > 500:
        raise ValidationError("Velg mellom 1 og 500 unike innspillinger.")
    recordings = list(Recording.objects.filter(pk__in=ids).order_by("pk"))
    tracks = list(
        ReleaseTrack.objects.filter(
            release=release, recording_id__in=ids
        ).order_by("pk")
    )
    memberships = {
        m.library_entry.recording_id: m
        for m in ManagedRecording.objects.filter(
            library_entry__recording_id__in=ids
        ).select_related("library_entry")
    }
    config = RightsConfiguration.objects.select_related(
        "local_organization"
    ).first()
    local = config.local_organization if config else None
    blockers = []
    if len(recordings) != len(ids) or {t.recording_id for t in tracks} != set(
        ids
    ):
        blockers.append(
            "Alle valgte innspillinger må fortsatt tilhøre utgivelsen."
        )
    scope = values.get("release_scope")
    if scope and scope.pk != release.pk:
        blockers.append(
            "Rettighetens utgivelsesscope må være denne utgivelsen."
        )
    existing = list(
        RightsClaim.objects.filter(recording_id__in=ids)
        .prefetch_related("territories")
        .order_by("pk")
    )
    rows = []
    for recording in recordings:
        managed = memberships.get(recording.pk)
        rows.append(
            RegistrationRow(
                recording, bool(managed), managed.status if managed else ""
            )
        )
        if not managed:
            if not territory_scope_countries(
                values.get("territory_mode", "world"), values["territories"]
            ):
                blockers.append(
                    "Onboarding krever et ikke-tomt territorielt grunnlag."
                )
            if not allow_managed_registration:
                blockers.append(
                    f"«{recording.title}»: eksplisitt onboarding kreves. Bare en administrator kan registrere forvaltning."
                )
            elif not local or values.get("rights_holder") != local:
                blockers.append(
                    f"«{recording.title}»: onboarding krever et lokalt P7-grunnlag."
                )
            if (
                values.get("right_type") == RightsClaim.RightType.OWNERSHIP
                and values.get("share") is None
            ):
                blockers.append(
                    f"«{recording.title}»: onboarding krever oppgitt eierandel."
                )
        fields = {
            key: value for key, value in values.items() if key != "territories"
        }
        claim = RightsClaim(recording=recording, **fields)
        try:
            claim.full_clean()
            # Preview the effect of confirming this proposal; creation remains UNVERIFIED.
            claim._prefetched_objects_cache = {
                "territories": values["territories"]
            }
            confirmed = [
                c
                for c in existing
                if c.recording_id == recording.pk
                and c.status == VerificationStatus.CONFIRMED
            ]
            conflict = (
                find_ownership_conflict(prepare_claims((claim, *confirmed)))
                if claim.right_type == RightsClaim.RightType.OWNERSHIP
                else None
            )
            if conflict:
                blockers.append(
                    f"«{recording.title}»: eierandeler overstiger 100 % i {conflict.territory} på {conflict.on_date}."
                )
        except ValidationError as error:
            blockers.extend(error.messages)
    snapshot = {
        "user": str(user.pk),
        "release": str(release.pk),
        "ids": list(map(str, ids)),
        "onboarding": allow_managed_registration,
        "values": {k: _snapshot_value(v) for k, v in sorted(values.items())},
        "configuration": _snapshot_value(config),
        "tracks": [(str(t.pk), t.revision) for t in tracks],
        "recordings": [(str(r.pk), r.revision) for r in recordings],
        "memberships": sorted(
            (str(m.pk), m.revision) for m in memberships.values()
        ),
        "claims": [
            (
                str(c.pk),
                c.revision,
                sorted((str(t.pk), t.code) for t in c.territories.all()),
            )
            for c in existing
        ],
    }
    digest = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True).encode()
    ).hexdigest()
    return ReleaseRegistrationPlan(
        tuple(rows),
        tuple(dict.fromkeys(blockers)),
        signing.dumps({"snapshot": digest}, salt=PLAN_SALT),
        bool(scope),
        sum(not row.managed for row in rows),
    )


@transaction.atomic
def apply_release_rights_registration(
    *,
    preview_token,
    user,
    release,
    recordings,
    allow_managed_registration=False,
    **values,
):
    require_permissions(
        user, "catalogue.view_release", "rights.add_rightsclaim"
    )
    if allow_managed_registration:
        require_onboarding(user)
    try:
        previous = signing.loads(preview_token, salt=PLAN_SALT, max_age=1800)
    except signing.BadSignature as error:
        raise ValidationError(
            "Forhåndsvisningen er ugyldig eller utløpt. Vis på nytt."
        ) from error
    ids = sorted({r.pk for r in recordings})
    if not ids or len(ids) > 500:
        raise ValidationError("Velg mellom 1 og 500 unike innspillinger.")
    # Same global order as 6D; track editing also takes Recording locks first.
    recordings = list(
        Recording.objects.select_for_update().filter(pk__in=ids).order_by("pk")
    )
    list(
        ReleaseTrack.objects.select_for_update()
        .filter(release=release, recording_id__in=ids)
        .order_by("pk")
    )
    plan = preview_release_rights_registration(
        user=user,
        release=release,
        recordings=recordings,
        allow_managed_registration=allow_managed_registration,
        **values,
    )
    current = signing.loads(plan.token, salt=PLAN_SALT)
    if previous != current:
        raise ValidationError(
            "Grunnlaget er endret siden forhåndsvisningen. Vis på nytt."
        )
    if plan.blockers:
        raise ValidationError(list(plan.blockers))
    values = _claim_values(values)
    claims = services.create_release_rights_claims(
        release=release,
        recordings=recordings,
        allow_managed_registration=allow_managed_registration,
        **values,
    )
    for claim in claims:
        _audit_registration(
            claim,
            user,
            f"Registrert fra utgivelse {release.pk} etter forhåndsvisning.",
        )
    return claims
