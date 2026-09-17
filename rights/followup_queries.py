"""Permission-aware candidate queries and bounded fact loading for 6I.

Only this boundary uses ORM. Evaluators receive immutable, fully loaded facts.
No global catalogue traversal and no presentation-helper calls per Release.
"""

from collections import defaultdict
from dataclasses import dataclass
from itertools import islice

from django.db.models import Exists, OuterRef, Prefetch, Q
from django.utils import timezone

from catalogue.models import Recording, RecordingContribution, ReleaseTrack
from managed_music.lifecycle import evaluate_management_state
from managed_music.models import ManagedRecording, ManagedRelease
from rights.models import RightsClaim, RightsConfiguration
from rights.followup import (
    ClaimFact,
    RecordingFacts,
    ReleaseFacts,
    TerritoryFact,
    evaluate_recording,
    evaluate_release,
)
from rights.scope import RELEVANT_STATUSES

BATCH_SIZE = 200


@dataclass(frozen=True)
class Visibility:
    rights: bool
    recording: bool
    management: bool
    release_review: bool
    release: bool
    party: bool
    source: bool
    agreement: bool

    @classmethod
    def for_user(cls, user):
        return cls(
            user.has_perm("rights.view_rightsclaim"),
            user.has_perm("catalogue.view_recording"),
            user.has_perm("managed_music.view_managedrecording"),
            user.has_perms(
                ("catalogue.view_release", "managed_music.view_managedrelease")
            ),
            user.has_perm("catalogue.view_release"),
            user.has_perm("parties.view_party"),
            user.has_perms(
                (
                    "provenance.view_sourcerecord",
                    "provenance.view_sourcesystem",
                )
            ),
            user.has_perm("rights.view_agreement"),
        )


def chunks(values, size=BATCH_SIZE):
    iterator = iter(values)
    while batch := tuple(islice(iterator, size)):
        yield batch


def claim_fact(claim, visibility):
    territories = tuple(claim.territories.all())
    details = (
        ("Rettighetstype", claim.get_right_type_display()),
        (
            "Status",
            (
                "Uverifisert"
                if claim.status == "unverified"
                else claim.get_status_display()
            ),
        ),
        ("Andel", "Ukjent" if claim.share is None else f"{claim.share} %"),
        (
            "Territorium",
            claim.get_territory_mode_display()
            + (
                ": " + ", ".join(sorted(t.code for t in territories))
                if territories
                else ""
            ),
        ),
        (
            "Periode",
            f"{claim.valid_from or 'Åpen start'} – {claim.valid_until or 'Åpen slutt'}",
        ),
        ("Dokumentasjonsstyrke", claim.get_evidence_strength_display()),
        (
            "Juridisk scope",
            (
                (
                    f"Kun utgivelsen {claim.release_scope.title}"
                    if visibility.release
                    else "Utgivelsesavgrenset"
                )
                if claim.release_scope_id
                else "Generell innspillingsposisjon"
            ),
        ),
    )
    if claim.right_type != "master_ownership":
        details = tuple(d for d in details if d[0] != "Andel")
    if visibility.party:
        details += (
            ("Rettighetshaver", claim.rights_holder.name),
            (
                "Rettighetsgiver",
                claim.grantor.name if claim.grantor_id else "Ikke registrert",
            ),
        )
    if visibility.source and claim.source_record_id:
        details += (
            ("Kildesystem", claim.source_record.source_system.name),
            ("Opprinnelig kildepost", str(claim.source_record)),
        )
    if visibility.agreement and claim.agreement_id:
        details += (("Avtale", claim.agreement.title),)
    return ClaimFact(
        pk=claim.pk,
        recording_id=claim.recording_id,
        right_type=claim.right_type,
        rights_holder_id=claim.rights_holder_id,
        grantor_id=claim.grantor_id,
        share=claim.share,
        status=claim.status,
        territory_mode=claim.territory_mode,
        territories=tuple(TerritoryFact(t.pk, t.code) for t in territories),
        valid_from=claim.valid_from,
        valid_until=claim.valid_until,
        release_scope_id=claim.release_scope_id,
        evidence_strength=claim.evidence_strength,
        details=details,
    )


def loaded_claims(ids, *, history=False):
    claims = (
        RightsClaim.objects.filter(recording_id__in=ids)
        .select_related(
            "rights_holder",
            "grantor",
            "release_scope",
            "agreement",
            "source_record__source_system",
        )
        .prefetch_related("territories")
        .order_by("recording_id", "id")
    )
    return claims.prefetch_related("decisions") if history else claims


def recording_candidates(local_id, day):
    live = RightsClaim.objects.filter(
        recording_id=OuterRef("pk"),
        rights_holder_id=local_id,
        status__in=RELEVANT_STATUSES,
    ).filter(Q(valid_until__isnull=True) | Q(valid_until__gte=day))
    members = ManagedRecording.objects.filter(
        library_entry__recording_id=OuterRef("pk")
    )
    protected = ReleaseTrack.objects.filter(
        recording_id=OuterRef("pk"), release__managed_release__isnull=False
    )
    any_live = RightsClaim.objects.filter(
        recording_id=OuterRef("pk"), status__in=RELEVANT_STATUSES
    ).filter(Q(valid_until__isnull=True) | Q(valid_until__gte=day))
    return (
        Recording.objects.filter(
            Exists(live)
            | Exists(members)
            | (Exists(protected) & Exists(any_live))
        )
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def load_recordings(ids, visibility, local_id, day):
    recordings = (
        Recording.objects.filter(pk__in=ids)
        .prefetch_related(
            "identifiers",
            Prefetch(
                "contributions",
                queryset=RecordingContribution.objects.select_related(
                    "party", "artist_identity"
                ).order_by("display_order", "id"),
            ),
        )
        .order_by("pk")
    )
    members = {
        m.library_entry.recording_id: m
        for m in ManagedRecording.objects.filter(
            library_entry__recording_id__in=ids
        ).select_related("library_entry")
    }
    claims = defaultdict(list)
    for claim in loaded_claims(ids, history=visibility.management):
        claims[claim.recording_id].append(claim)
    releases = defaultdict(set)
    for rid, release_id in ReleaseTrack.objects.filter(
        recording_id__in=ids
    ).values_list("recording_id", "release_id"):
        releases[rid].add(release_id)
    for recording in recordings:
        member = members.get(recording.pk)
        state = (
            evaluate_management_state(
                member,
                claims[recording.pk],
                local_organization=local_id,
                on_date=day,
            )
            if member and visibility.management
            else None
        )
        # Credits belong to the Recording's canonical display identity.
        artists = ", ".join(
            dict.fromkeys(
                c.display_credit
                for c in recording.contributions.all()
                if c.role
                in (
                    RecordingContribution.Role.PRIMARY,
                    RecordingContribution.Role.FEATURED,
                )
            )
        )
        identifiers = ", ".join(i.value for i in recording.identifiers.all())
        yield RecordingFacts(
            recording.pk,
            recording.title,
            (
                ("Artist / kreditering", artists or "Ikke registrert"),
                ("Identifikatorer", identifiers or "Ikke registrert"),
            ),
            tuple(claim_fact(c, visibility) for c in claims[recording.pk]),
            frozenset(releases[recording.pk]),
            member.pk if member else None,
            state,
            visibility.management,
        )


def load_releases(ids, visibility):
    releases = (
        ManagedRelease.objects.filter(pk__in=ids)
        .select_related("release")
        .order_by("pk")
    )
    by_release = defaultdict(set)
    for release_id, rid in ReleaseTrack.objects.filter(
        release__managed_release__pk__in=ids
    ).values_list("release_id", "recording_id"):
        by_release[release_id].add(rid)
    recording_ids = set().union(*by_release.values()) if by_release else set()
    managed = set()
    claims = defaultdict(list)
    # Chunk large track collections too; no per-Release or per-track queries.
    for batch in chunks(sorted(recording_ids)):
        managed.update(
            ManagedRecording.objects.filter(
                library_entry__recording_id__in=batch
            ).values_list("library_entry__recording_id", flat=True)
        )
        for claim in loaded_claims(batch):
            claims[claim.recording_id].append(claim_fact(claim, visibility))
    for m in releases:
        rids = frozenset(by_release[m.release_id])
        yield ReleaseFacts(
            m.release_id,
            m.release.title,
            m.status,
            rids,
            frozenset(managed & rids),
            tuple(c for rid in sorted(rids) for c in claims[rid]),
            (
                (
                    "Katalognummer",
                    m.release.catalogue_number or "Ikke registrert",
                ),
                ("Katalogforhold", m.get_relationship_display()),
                ("Forvaltningsstatus", m.get_status_display()),
            ),
        )


def collect_items(user, *, on_date=None, horizon=90):
    """Permission filtering precedes aggregation, counts, choices and pagination."""
    visibility = Visibility.for_user(user)
    if not visibility.rights:
        return (), False
    day = on_date or timezone.localdate()
    local_id = RightsConfiguration.objects.values_list(
        "local_organization_id", flat=True
    ).first()
    if local_id is None:
        return (), False
    items = []
    if visibility.recording:
        for ids in chunks(
            recording_candidates(local_id, day).iterator(chunk_size=BATCH_SIZE)
        ):
            for facts in load_recordings(ids, visibility, local_id, day):
                items.extend(
                    evaluate_recording(
                        facts, local_id=local_id, on_date=day, horizon=horizon
                    )
                )
    if visibility.release_review:
        candidates = (
            ManagedRelease.objects.filter(status__in=("active", "pending"))
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        for ids in chunks(candidates.iterator(chunk_size=50), 50):
            for facts in load_releases(ids, visibility):
                items.extend(
                    evaluate_release(facts, local_id=local_id, on_date=day)
                )
    return tuple(items), True
