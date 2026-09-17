from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from catalogue.models import Recording, Release, ReleaseTrack
from managed_music.lifecycle import management_state
from managed_music.models import ManagedRecording, ManagedRelease
from managed_music.services import return_to_music_library
from music_library.models import MusicLibraryEntry
from parties.models import Party
from provenance.models import SourceRecord, SourceSystem
from rights_core.models import VerificationStatus as Status

from rights import services, workflows as wf
from rights.models import (
    Agreement,
    RightsClaim,
    RightsConfiguration,
    RightsDecision,
    Territory,
)

OWN = RightsClaim.RightType.OWNERSHIP
ADMIN = RightsClaim.RightType.ADMINISTRATION
DIST = RightsClaim.RightType.DISTRIBUTION


class RightsWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("workflow-admin")
        self.reader = get_user_model().objects.create_user("reader")
        self.local = Party.objects.create(
            name="P7", kind=Party.Kind.ORGANIZATION
        )
        self.other = Party.objects.create(
            name="Other", kind=Party.Kind.ORGANIZATION
        )
        RightsConfiguration.objects.create(local_organization=self.local)
        self.recording = Recording.objects.create(title="Workflow recording")
        self.release = Release.objects.create(title="Workflow release")
        self.track = ReleaseTrack.objects.create(
            release=self.release, recording=self.recording, sequence_number=1
        )
        self.no = Territory.objects.get(code="NO")

    def onboard(self, **values):
        return wf.onboard_managed_recording(
            user=self.user,
            **{
                "recording": self.recording,
                "relationship_type": ADMIN,
                **values,
            }
        )

    def claim(self, **values):
        return services.create_rights_claim(
            **{
                "recording": self.recording,
                "right_type": ADMIN,
                "rights_holder": self.local,
                **values,
            }
        )

    def bulk(self, **values):
        return {
            "user": self.user,
            "release": self.release,
            "recordings": [self.recording, self.recording],
            "right_type": DIST,
            "rights_holder": self.local,
            "allow_managed_registration": True,
            **values,
        }

    def test_onboarding_all_scope_dimensions_and_creation_actor(self):
        source = SourceRecord.objects.create(
            source_system=SourceSystem.objects.create(
                name="Agreement source", kind="manual"
            )
        )
        agreement = Agreement.objects.create(
            title="Document", agreement_type="license"
        )
        managed_release = ManagedRelease.objects.create(
            release=self.release, relationship="managed_catalogue"
        )
        from catalogue.authority import recording_authority

        before = recording_authority(self.recording)
        managed = self.onboard(
            territory_mode="include",
            territories=[self.no],
            valid_from=date(2020, 1, 1),
            valid_until=date(2099, 1, 1),
            release_scope=self.release,
            grantor=self.other,
            source_record=source,
            agreement=agreement,
            evidence_strength="strong",
            notes="Test scope",
        )
        claim = self.recording.rights_claims.get()
        self.assertEqual(managed.status, ManagedRecording.Status.PENDING)
        self.assertEqual(claim.status, Status.UNVERIFIED)
        self.assertEqual(claim.release_scope, self.release)
        self.assertEqual(list(claim.territories.all()), [self.no])
        self.assertEqual(claim.grantor, self.other)
        self.assertEqual(claim.source_record, source)
        self.assertEqual(claim.agreement, agreement)
        self.assertEqual(claim.evidence_strength, "strong")
        self.assertEqual(claim.decisions.get().decided_by, self.user)
        wf.decide_rights_claim(claim, Status.CONFIRMED, user=self.user)
        managed.refresh_from_db()
        self.assertEqual(managed.status, ManagedRecording.Status.ACTIVE)
        self.assertNotEqual(before, recording_authority(self.recording))
        managed_release.refresh_from_db()
        self.assertEqual(managed_release.status, ManagedRelease.Status.PENDING)

    def test_onboarding_exclude_and_failure_rolls_back_every_object(self):
        self.onboard(territory_mode="exclude", territories=[self.no])
        self.assertEqual(
            self.recording.rights_claims.get().territory_mode, "exclude"
        )
        counts = (
            Recording.objects.count(),
            MusicLibraryEntry.objects.count(),
            ManagedRecording.objects.count(),
            RightsClaim.objects.count(),
        )
        for values in (
            {"relationship_type": OWN},
            {"relationship_type": ADMIN, "ownership_share": 20},
            {
                "relationship_type": OWN,
                "ownership_share": 100,
                "release_scope": self.release,
            },
            {"territory_mode": "include", "territories": []},
            {"valid_from": date(2030, 1, 1), "valid_until": date(2020, 1, 1)},
        ):
            with self.subTest(values=values), self.assertRaises(
                (ValueError, ValidationError)
            ):
                self.onboard(
                    recording=None,
                    new_recording_title="Invalid new recording",
                    **values
                )
            self.assertEqual(
                counts,
                (
                    Recording.objects.count(),
                    MusicLibraryEntry.objects.count(),
                    ManagedRecording.objects.count(),
                    RightsClaim.objects.count(),
                ),
            )

    def test_new_recording_duplicate_protection_and_unmanaged_third_party(
        self,
    ):
        with self.assertRaises(ValueError):
            self.onboard(
                recording=None, new_recording_title=self.recording.title
            )
        with self.assertRaises(ValidationError):
            wf.register_rights_claim(
                user=self.user,
                recording=self.recording,
                right_type=OWN,
                rights_holder=self.other,
            )
        self.onboard()
        claim = wf.register_rights_claim(
            user=self.user,
            recording=self.recording,
            right_type=OWN,
            rights_holder=self.other,
        )
        self.assertEqual(claim.rights_holder, self.other)
        self.assertEqual(claim.decisions.get().decided_by, self.user)

    def test_return_blocks_live_local_decisions_and_replacements(self):
        today = timezone.localdate()
        for right_type in (OWN, ADMIN, DIST):
            for start in (today, today + timedelta(days=30)):
                with self.subTest(right_type=right_type, start=start):
                    recording = Recording.objects.create(title="Returned")
                    managed = wf.onboard_managed_recording(
                        user=self.user,
                        recording=recording,
                        relationship_type=right_type,
                        ownership_share=(
                            Decimal(100) if right_type == OWN else None
                        ),
                        valid_from=start,
                    )
                    claim = recording.rights_claims.get()
                    wf.decide_rights_claim(
                        claim, Status.REJECTED, user=self.user
                    )
                    return_to_music_library(
                        managed, user=self.user, reason="Onboarding avvist"
                    )
                    count = claim.decisions.count()
                    for decision in (Status.CONFIRMED, Status.DISPUTED):
                        with self.assertRaisesMessage(
                            ValidationError, "eksplisitt onboarding"
                        ):
                            wf.decide_rights_claim(
                                claim, decision, user=self.user
                            )
                        claim.refresh_from_db()
                        self.assertEqual(claim.status, Status.REJECTED)
                        self.assertEqual(claim.decisions.count(), count)
                    values = dict(
                        rights_holder=self.local,
                        share=claim.share,
                        valid_from=start,
                    )
                    with self.assertRaisesMessage(
                        ValidationError, "eksplisitt onboarding"
                    ):
                        wf.supersede_rights_claim(
                            claim, user=self.user, **values
                        )
                    claim.refresh_from_db()
                    self.assertEqual(claim.status, Status.REJECTED)
                    self.assertFalse(claim.superseded_by.exists())
                    self.assertEqual(claim.decisions.count(), count)
                    self.assertFalse(
                        ManagedRecording.objects.filter(
                            library_entry__recording=recording
                        ).exists()
                    )
                    # Explicit onboarding restores the manual workflow.
                    wf.onboard_managed_recording(
                        user=self.user,
                        recording=recording,
                        relationship_type=ADMIN,
                    )
                    wf.decide_rights_claim(
                        claim, Status.CONFIRMED, user=self.user
                    )

    def test_return_preserves_historical_correction_and_documentation(self):
        managed = self.onboard()
        claim = self.recording.rights_claims.get()
        wf.decide_rights_claim(claim, Status.REJECTED, user=self.user)
        return_to_music_library(
            managed, user=self.user, reason="Ingen forvaltning"
        )
        wf.document_rights_claim(
            claim, user=self.user, note="Senere historisk dokumentasjon"
        )
        replacement = wf.supersede_rights_claim(
            claim,
            user=self.user,
            rights_holder=self.local,
            valid_until=timezone.localdate() - timedelta(days=1),
        )
        wf.decide_rights_claim(replacement, Status.CONFIRMED, user=self.user)
        self.assertFalse(ManagedRecording.objects.exists())
        claim.refresh_from_db()
        self.assertEqual(claim.status, Status.SUPERSEDED)
        self.assertTrue(
            claim.decisions.filter(
                note=" Senere historisk dokumentasjon"
            ).exists()
        )

    def test_return_release_scope_does_not_bypass_onboarding(self):
        managed = self.onboard()
        claim = self.recording.rights_claims.get()
        wf.decide_rights_claim(claim, Status.REJECTED, user=self.user)
        return_to_music_library(managed, user=self.user, reason="Avvist")
        with self.assertRaises(ValidationError):
            wf.supersede_rights_claim(
                claim,
                user=self.user,
                rights_holder=self.local,
                release_scope=self.release,
                territory_mode=RightsClaim.TerritoryMode.INCLUDE,
                territories=[self.no],
            )
        # Trusted imports remain separate, but manual confirmation is guarded.
        imported = self.claim(release_scope=self.release)
        with self.assertRaises(ValidationError):
            wf.decide_rights_claim(imported, Status.CONFIRMED, user=self.user)
        self.assertFalse(ManagedRecording.objects.exists())

    def test_decision_matrix_terminal_noop_and_atomic_conflict(self):
        self.onboard()
        expected = {
            Status.UNVERIFIED: {
                Status.CONFIRMED,
                Status.DISPUTED,
                Status.REJECTED,
            },
            Status.CONFIRMED: {Status.DISPUTED, Status.REJECTED},
            Status.DISPUTED: {Status.CONFIRMED, Status.REJECTED},
            Status.REJECTED: {Status.CONFIRMED, Status.DISPUTED},
            Status.SUPERSEDED: set(),
        }
        for initial, allowed in expected.items():
            for target in (Status.CONFIRMED, Status.DISPUTED, Status.REJECTED):
                with self.subTest(initial=initial, target=target):
                    claim = self.claim()
                    claim.status = initial
                    claim._allow_status_transition = True
                    claim.save(update_fields=("status",))
                    if target in allowed:
                        result = wf.decide_rights_claim(
                            claim, target, user=self.user
                        )
                        self.assertEqual(result.decided_by, self.user)
                        with self.assertRaises(ValidationError):
                            result.save()
                    else:
                        with self.assertRaises(ValidationError):
                            wf.decide_rights_claim(
                                claim, target, user=self.user
                            )
                        self.assertFalse(claim.decisions.exists())
        full = self.claim(right_type=OWN, share=Decimal(100))
        wf.decide_rights_claim(full, Status.CONFIRMED, user=self.user)
        conflict = self.claim(
            right_type=OWN, share=Decimal(1), rights_holder=self.other
        )
        with self.assertRaises(ValidationError):
            wf.decide_rights_claim(conflict, Status.CONFIRMED, user=self.user)
        conflict.refresh_from_db()
        self.assertEqual(conflict.status, Status.UNVERIFIED)
        self.assertFalse(conflict.decisions.exists())

    def test_split_replacement_audit_scope_history_and_single_reconciliation(
        self,
    ):
        managed = self.onboard()
        previous = self.recording.rights_claims.get()
        wf.decide_rights_claim(previous, Status.CONFIRMED, user=self.user)
        from managed_music.lifecycle import refresh_management_status

        with patch(
            "managed_music.lifecycle.refresh_management_status",
            wraps=refresh_management_status,
        ) as refresh:
            replacements = wf.supersede_rights_claims(
                previous,
                user=self.user,
                replacements=[
                    {
                        "rights_holder": self.local,
                        "territory_mode": "include",
                        "territories": [self.no],
                        "release_scope": self.release,
                    },
                    {
                        "rights_holder": self.other,
                        "territory_mode": "exclude",
                        "territories": [self.no],
                        "valid_from": date(2020, 1, 1),
                    },
                ],
                note="Territoriell oppdeling",
            )
            self.assertEqual(refresh.call_count, 1)
        previous.refresh_from_db()
        managed.refresh_from_db()
        self.assertEqual(previous.status, Status.SUPERSEDED)
        self.assertEqual(managed.status, ManagedRecording.Status.INACTIVE)
        event = previous.decisions.get(decision=Status.SUPERSEDED)
        for claim in replacements:
            self.assertEqual(claim.status, Status.UNVERIFIED)
            self.assertEqual(claim.supersedes, previous)
            self.assertIn(str(claim.pk), event.note)
            self.assertEqual(claim.decisions.get().decided_by, self.user)
        with self.assertRaises(ValidationError):
            wf.supersede_rights_claim(
                previous, user=self.user, rights_holder=self.local
            )

    def test_invalid_replacement_rolls_back_all_and_preserves_invariants(self):
        previous = self.claim()
        for invalid in (
            {"right_type": OWN},
            {"valid_from": date(2030, 1, 1), "valid_until": date(2000, 1, 1)},
        ):
            with self.assertRaises(ValidationError):
                services.supersede_rights_claims(
                    previous,
                    user=self.user,
                    replacements=[
                        {"rights_holder": self.local},
                        {"rights_holder": self.local, **invalid},
                    ],
                )
            self.assertEqual(RightsClaim.objects.count(), 1)
            self.assertFalse(RightsDecision.objects.exists())
        with self.assertRaises(ValidationError):
            services.supersede_rights_claim(
                previous,
                user=self.user,
                recording=self.release,
                rights_holder=self.local,
            )

    def test_documentation_preserves_status_scope_source_and_logs_old_new(
        self,
    ):
        source = SourceRecord.objects.create(
            source_system=SourceSystem.objects.create(
                name="Original", kind="manual"
            )
        )
        claim = self.claim(
            source_record=source,
            release_scope=self.release,
            valid_from=date(2020, 1, 1),
        )
        wf.decide_rights_claim(claim, Status.REJECTED, user=self.user)
        agreement = Agreement.objects.create(
            title="Later evidence",
            agreement_type="license",
            effective_date=date(2030, 1, 1),
        )
        wf.document_rights_claim(
            claim,
            user=self.user,
            agreement=agreement,
            evidence_strength="documented",
            note="Nytt dokument",
        )
        claim.refresh_from_db()
        self.assertEqual(claim.status, Status.REJECTED)
        self.assertEqual(claim.source_record, source)
        self.assertEqual(claim.release_scope, self.release)
        self.assertEqual(claim.valid_from, date(2020, 1, 1))
        event = claim.decisions.get(decision="documented")
        self.assertIn(str(agreement.pk), event.note)
        self.assertIn("not_assessed → documented", event.note)
        with self.assertRaises(TypeError):
            wf.document_rights_claim(claim, user=self.user, source_record=None)

    def test_bulk_preview_readonly_unique_explicit_onboarding_and_general_scope(
        self,
    ):
        values = self.bulk()
        counts = (
            RightsClaim.objects.count(),
            ManagedRecording.objects.count(),
            RightsDecision.objects.count(),
        )
        plan = wf.preview_release_rights_registration(**values)
        self.assertEqual(
            counts,
            (
                RightsClaim.objects.count(),
                ManagedRecording.objects.count(),
                RightsDecision.objects.count(),
            ),
        )
        self.assertEqual(plan.onboarding_count, 1)
        self.assertEqual(len(plan.rows), 1)
        self.assertFalse(plan.blockers)
        (claim,) = wf.apply_release_rights_registration(
            preview_token=plan.token, **values
        )
        self.assertIsNone(claim.release_scope)
        self.assertEqual(claim.status, Status.UNVERIFIED)
        self.assertEqual(ManagedRecording.objects.get().status, "pending")
        self.assertEqual(claim.decisions.get().decided_by, self.user)
        with self.assertRaises(ValidationError):
            wf.apply_release_rights_registration(
                preview_token=plan.token, **values
            )

    def test_bulk_scope_no_hidden_onboarding_and_third_party_guard(self):
        for change in (
            {"allow_managed_registration": False},
            {"rights_holder": self.other},
            {"right_type": OWN, "share": 100, "release_scope": self.release},
        ):
            values = self.bulk(**change)
            plan = wf.preview_release_rights_registration(**values)
            self.assertTrue(plan.blockers)
            with self.assertRaises(ValidationError):
                wf.apply_release_rights_registration(
                    preview_token=plan.token, **values
                )
        self.assertFalse(ManagedRecording.objects.exists())
        self.onboard()
        values = self.bulk(
            rights_holder=self.other,
            release_scope=self.release,
            allow_managed_registration=False,
        )
        plan = wf.preview_release_rights_registration(**values)
        (claim,) = wf.apply_release_rights_registration(
            preview_token=plan.token, **values
        )
        self.assertEqual(claim.release_scope, self.release)
        self.assertEqual(claim.rights_holder, self.other)

    def test_bulk_revalidates_tracks_membership_claims_and_inputs(self):
        values = self.bulk()
        plan = wf.preview_release_rights_registration(**values)
        self.track.title_override = "Changed placement metadata"
        self.track.save()
        with self.assertRaises(ValidationError):
            wf.apply_release_rights_registration(
                preview_token=plan.token, **values
            )
        plan = wf.preview_release_rights_registration(**values)
        self.onboard()
        with self.assertRaises(ValidationError):
            wf.apply_release_rights_registration(
                preview_token=plan.token, **values
            )
        plan = wf.preview_release_rights_registration(**values)
        with self.assertRaises(ValidationError):
            wf.apply_release_rights_registration(
                preview_token=plan.token,
                **{**values, "notes": "Changed input"}
            )
        self.claim()
        with self.assertRaises(ValidationError):
            wf.apply_release_rights_registration(
                preview_token=plan.token, **values
            )
        plan = wf.preview_release_rights_registration(**values)
        self.track.delete()
        with self.assertRaises(ValidationError):
            wf.apply_release_rights_registration(
                preview_token=plan.token, **values
            )

    def test_bulk_ownership_conflict_and_late_failure_are_atomic(self):
        second = Recording.objects.create(title="Second")
        ReleaseTrack.objects.create(
            release=self.release, recording=second, sequence_number=2
        )
        values = self.bulk(
            recordings=[self.recording, second],
            right_type=OWN,
            share=Decimal(60),
        )
        confirmed = self.claim(
            right_type=OWN, share=Decimal(50), rights_holder=self.other
        )
        wf.decide_rights_claim(confirmed, Status.CONFIRMED, user=self.user)
        plan = wf.preview_release_rights_registration(**values)
        self.assertTrue(plan.blockers)
        with self.assertRaises(ValidationError):
            wf.apply_release_rights_registration(
                preview_token=plan.token, **values
            )
        self.assertFalse(ManagedRecording.objects.exists())
        values = self.bulk(recordings=[self.recording, second])
        plan = wf.preview_release_rights_registration(**values)
        original = services.create_rights_claim
        calls = []

        def fail_second(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise ValidationError("Concurrent invalid row")
            return original(**kwargs)

        with patch(
            "rights.services.create_rights_claim", side_effect=fail_second
        ), self.assertRaises(ValidationError):
            wf.apply_release_rights_registration(
                preview_token=plan.token, **values
            )
        self.assertFalse(ManagedRecording.objects.exists())
        self.assertFalse(MusicLibraryEntry.objects.exists())
        self.assertEqual(RightsClaim.objects.count(), 1)

    def test_legacy_correction_audit_keeps_membership_and_plausible_claims(
        self,
    ):
        managed = self.onboard()
        managed.status = ManagedRecording.Status.ACTIVE
        managed.save()
        self.assertTrue(management_state(managed).history_uncertain)
        with self.assertRaises(ValidationError):
            wf.correct_legacy_management_history(
                managed, user=self.user, reason=" "
            )
        with patch("flac_ingest.services.mark_recording_for_sync") as mark:
            audit = wf.correct_legacy_management_history(
                managed,
                user=self.user,
                reason="Importstatus feil; aldri faktisk forvaltet",
            )
            mark.assert_not_called()
        managed.refresh_from_db()
        state = management_state(managed)
        self.assertEqual(managed.status, ManagedRecording.Status.PENDING)
        self.assertFalse(state.history_uncertain)
        self.assertFalse(state.can_return_to_music_library)
        self.assertEqual(audit.raw_payload["previous_status"], "active")
        self.assertEqual(
            audit.raw_payload["performed_by_id"], str(self.user.pk)
        )
        claim = self.recording.rights_claims.get()
        wf.decide_rights_claim(claim, Status.REJECTED, user=self.user)
        return_to_music_library(
            managed, user=self.user, reason="Grunnlaget avvist"
        )
        self.assertTrue(SourceRecord.objects.filter(pk=audit.pk).exists())
        self.assertTrue(RightsClaim.objects.filter(pk=claim.pk).exists())
        self.assertTrue(MusicLibraryEntry.objects.exists())

    def test_retrospective_confirmation_resolves_legacy_without_false_correction(
        self,
    ):
        managed = self.onboard(
            valid_until=timezone.localdate() - timedelta(days=1)
        )
        managed.status = ManagedRecording.Status.ACTIVE
        managed.save()
        wf.decide_rights_claim(
            self.recording.rights_claims.get(),
            Status.CONFIRMED,
            user=self.user,
        )
        managed.refresh_from_db()
        self.assertEqual(managed.status, ManagedRecording.Status.INACTIVE)
        with self.assertRaises(ValidationError):
            wf.correct_legacy_management_history(
                managed, user=self.user, reason="Not allowed"
            )

    def test_workflow_permissions_are_enforced_without_views(self):
        claim = self.claim()
        managed = self.onboard()
        actions = (
            lambda: wf.onboard_managed_recording(
                user=self.reader,
                recording=self.recording,
                relationship_type=ADMIN,
            ),
            lambda: wf.register_rights_claim(
                user=self.reader,
                recording=self.recording,
                right_type=ADMIN,
                rights_holder=self.local,
            ),
            lambda: wf.decide_rights_claim(
                claim, Status.CONFIRMED, user=self.reader
            ),
            lambda: wf.supersede_rights_claim(
                claim, user=self.reader, rights_holder=self.local
            ),
            lambda: wf.document_rights_claim(
                claim, user=self.reader, note="No access"
            ),
            lambda: wf.correct_legacy_management_history(
                managed, user=self.reader, reason="No access"
            ),
            lambda: wf.preview_release_rights_registration(
                **self.bulk(user=self.reader)
            ),
        )
        for action in actions:
            with self.assertRaises(PermissionDenied):
                action()
        self.reader.user_permissions.add(
            *Permission.objects.filter(
                codename__in=("add_managedrecording", "add_rightsclaim")
            )
        )
        reader = get_user_model().objects.get(pk=self.reader.pk)
        with self.assertRaises(PermissionDenied):
            wf.onboard_managed_recording(
                user=reader, recording=self.recording, relationship_type=ADMIN
            )

    def test_non_admin_workflow_permissions_and_revoked_apply_access(self):
        self.onboard()
        self.reader.user_permissions.add(
            *Permission.objects.filter(
                codename__in=(
                    "add_rightsclaim",
                    "change_rightsclaim",
                    "decide_rightsclaim",
                    "view_release",
                )
            )
        )
        actor = get_user_model().objects.get(pk=self.reader.pk)
        claim = wf.register_rights_claim(
            user=actor,
            recording=self.recording,
            right_type=DIST,
            rights_holder=self.other,
        )
        wf.document_rights_claim(claim, user=actor, evidence_strength="strong")
        wf.decide_rights_claim(claim, Status.CONFIRMED, user=actor)
        with self.assertRaises(PermissionDenied):
            wf.document_rights_claim(
                claim,
                user=actor,
                agreement=None,
                note="Cannot manage agreements",
            )
        values = self.bulk(user=actor, allow_managed_registration=False)
        plan = wf.preview_release_rights_registration(**values)
        actor.user_permissions.clear()
        values["user"] = get_user_model().objects.get(pk=actor.pk)
        with self.assertRaises(PermissionDenied):
            wf.apply_release_rights_registration(
                preview_token=plan.token, **values
            )

    def test_preview_token_detects_reference_changes_even_with_old_objects(
        self,
    ):
        values = self.bulk(territory_mode="include", territories=[self.no])
        plan = wf.preview_release_rights_registration(**values)
        territory = Territory.objects.get(pk=self.no.pk)
        territory.name_nb = "Endret navn"
        territory.save()
        with self.assertRaises(ValidationError):
            wf.apply_release_rights_registration(
                preview_token=plan.token, **values
            )

    def test_corrected_legacy_does_not_allow_return_with_disputed_or_future_basis(
        self,
    ):
        for status in (Status.DISPUTED, Status.CONFIRMED):
            with self.subTest(status=status):
                recording = Recording.objects.create(title=str(status))
                managed = self.onboard(
                    recording=recording,
                    valid_from=timezone.localdate() + timedelta(days=30),
                )
                wf.decide_rights_claim(
                    recording.rights_claims.get(), status, user=self.user
                )
                managed.refresh_from_db()
                managed.status = ManagedRecording.Status.INACTIVE
                managed.save()
                wf.correct_legacy_management_history(
                    managed, user=self.user, reason="Feil legacy-status"
                )
                managed.refresh_from_db()
                self.assertFalse(
                    management_state(managed).can_return_to_music_library
                )
                with self.assertRaises(ValidationError):
                    return_to_music_library(
                        managed,
                        user=self.user,
                        reason="Fortsatt plausible krav",
                    )
