from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Recording, Release, ReleaseTrack
from flac_ingest.services import database_is_catalogue_authority
from music_library.models import MusicLibraryEntry
from parties.models import Party
from provenance.models import MetadataAssertion, SourceRecord, SourceSystem
from rights.models import RightsClaim, RightsConfiguration, RightsDecision
from rights.services import (
    create_rights_claim,
    decide_rights_claim,
    supersede_rights_claim,
)
from rights_core.models import VerificationStatus

from managed_music.lifecycle import management_state, refresh_management_status
from managed_music.models import ManagedRecording, ManagedRelease
from managed_music.services import (
    create_managed_recording,
    return_to_music_library,
    save_managed_release,
)


class ManagementLifecycleTests(TestCase):
    def setUp(self):
        clock = patch(
            "django.utils.timezone.now",
            return_value=datetime(2026, 9, 17, 10, tzinfo=dt_timezone.utc),
        )
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.user = get_user_model().objects.create_user(username="reviewer")
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="managed_music",
                codename="delete_managedrecording",
            )
        )
        self.local = Party.objects.create(
            name="P7", kind=Party.Kind.ORGANIZATION
        )
        RightsConfiguration.objects.create(local_organization=self.local)
        self.recording = Recording.objects.create(title="Bevart katalog")
        self.managed = create_managed_recording(
            recording=self.recording,
            relationship_type=RightsClaim.RightType.ADMINISTRATION,
            notes="Menneskeskapt katalogkunnskap",
        )
        self.claim = RightsClaim.objects.get(recording=self.recording)

    def decide(self, decision, claim=None):
        self.clock.return_value += timedelta(seconds=1)
        return decide_rights_claim(
            claim or self.claim, decision, user=self.user
        )

    def replace_claim(self, **values):
        self.clock.return_value += timedelta(seconds=1)
        self.claim = supersede_rights_claim(
            self.claim, user=self.user, rights_holder=self.local, **values
        )

    def test_pending_requires_concrete_local_onboarding_basis(self):
        state = management_state(self.managed)
        self.assertEqual(state.status, ManagedRecording.Status.PENDING)
        self.assertTrue(state.has_pending_basis)
        self.assertFalse(state.has_current_basis)
        self.assertFalse(state.has_confirmed_history)
        self.assertFalse(state.requires_follow_up)
        self.assertEqual(self.claim.status, VerificationStatus.UNVERIFIED)

    def test_rejected_pending_remains_managed_with_explicit_follow_up(self):
        self.decide(VerificationStatus.REJECTED)
        self.managed.refresh_from_db()
        state = management_state(self.managed)
        self.assertEqual(self.managed.status, ManagedRecording.Status.PENDING)
        self.assertEqual(state.status, ManagedRecording.Status.PENDING)
        self.assertFalse(state.has_pending_basis)
        self.assertTrue(state.requires_follow_up)
        self.assertTrue(database_is_catalogue_authority(self.recording))
        corrected = create_rights_claim(
            recording=self.recording,
            rights_holder=self.local,
            right_type=RightsClaim.RightType.DISTRIBUTION,
        )
        self.assertFalse(management_state(self.managed).requires_follow_up)
        self.assertEqual(corrected.status, VerificationStatus.UNVERIFIED)

    def test_each_existing_local_master_right_can_activate_management(self):
        for right_type in RightsClaim.RightType.values:
            with self.subTest(right_type=right_type):
                managed = create_managed_recording(
                    new_recording_title=right_type,
                    relationship_type=right_type,
                    ownership_share=(
                        50
                        if right_type == RightsClaim.RightType.OWNERSHIP
                        else None
                    ),
                )
                self.decide(
                    VerificationStatus.CONFIRMED,
                    managed.recording.rights_claims.get(),
                )
                managed.refresh_from_db()
                self.assertEqual(
                    managed.status, ManagedRecording.Status.ACTIVE
                )
                self.assertTrue(management_state(managed).has_current_basis)

    def test_formerly_active_is_inactive_after_rejection_and_remains_protected(
        self,
    ):
        self.decide(VerificationStatus.CONFIRMED)
        self.decide(VerificationStatus.REJECTED)
        self.managed.refresh_from_db()
        state = management_state(self.managed)
        self.assertEqual(self.managed.status, ManagedRecording.Status.INACTIVE)
        self.assertTrue(state.has_confirmed_history)
        self.assertFalse(state.has_current_basis)
        self.assertTrue(database_is_catalogue_authority(self.recording))
        with self.assertRaises(ValidationError):
            return_to_music_library(
                self.managed, user=self.user, reason="Kan ikke slettes"
            )

    def test_history_protects_even_if_old_status_was_reset_to_pending(self):
        self.decide(VerificationStatus.CONFIRMED)
        self.decide(VerificationStatus.REJECTED)
        self.managed.refresh_from_db()
        self.managed.status = ManagedRecording.Status.PENDING
        self.managed.save(update_fields=("status",))
        with self.assertRaises(ValidationError):
            return_to_music_library(
                self.managed, user=self.user, reason="Gammel status"
            )
        refresh_management_status(self.recording)
        self.managed.refresh_from_db()
        self.assertEqual(self.managed.status, ManagedRecording.Status.INACTIVE)

    def test_superseding_keeps_actual_history_and_does_not_confirm_replacement(
        self,
    ):
        self.decide(VerificationStatus.CONFIRMED)
        self.replace_claim()
        self.managed.refresh_from_db()
        self.assertEqual(self.managed.status, ManagedRecording.Status.INACTIVE)
        self.assertEqual(self.claim.status, VerificationStatus.UNVERIFIED)
        self.assertTrue(management_state(self.managed).has_pending_basis)
        self.decide(VerificationStatus.CONFIRMED)
        self.managed.refresh_from_db()
        self.assertEqual(self.managed.status, ManagedRecording.Status.ACTIVE)

    def test_other_current_local_basis_keeps_management_active(self):
        second = create_rights_claim(
            recording=self.recording,
            rights_holder=self.local,
            right_type=RightsClaim.RightType.DISTRIBUTION,
        )
        self.decide(VerificationStatus.CONFIRMED)
        self.decide(VerificationStatus.CONFIRMED, second)
        self.decide(VerificationStatus.REJECTED)
        self.managed.refresh_from_db()
        self.assertEqual(self.managed.status, ManagedRecording.Status.ACTIVE)

    def test_expiry_can_be_read_and_reconciled_without_another_rights_decision(
        self,
    ):
        self.replace_claim(valid_until=timezone.localdate())
        self.decide(VerificationStatus.CONFIRMED)
        self.clock.return_value += timedelta(days=1)
        state = management_state(self.managed)
        self.assertEqual(state.status, ManagedRecording.Status.INACTIVE)
        self.assertTrue(state.has_confirmed_history)
        refresh_management_status(self.recording)
        self.managed.refresh_from_db()
        self.assertEqual(self.managed.status, ManagedRecording.Status.INACTIVE)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, VerificationStatus.CONFIRMED)

    def test_future_confirmation_rejected_before_start_is_not_past_management(
        self,
    ):
        self.replace_claim(
            valid_from=timezone.localdate() + timedelta(days=10)
        )
        self.decide(VerificationStatus.CONFIRMED)
        self.managed.refresh_from_db()
        self.assertEqual(self.managed.status, ManagedRecording.Status.PENDING)
        with self.assertRaises(ValidationError):
            return_to_music_library(
                self.managed, user=self.user, reason="Fremtidig grunnlag"
            )
        self.decide(VerificationStatus.REJECTED)
        self.clock.return_value += timedelta(days=20)
        state = management_state(self.managed)
        self.assertFalse(state.has_confirmed_history)
        self.assertTrue(state.requires_follow_up)
        return_to_music_library(
            self.managed, user=self.user, reason="Avvist før oppstart"
        )

    def test_retrospective_confirmation_establishes_historical_management(
        self,
    ):
        self.replace_claim(
            valid_until=timezone.localdate() - timedelta(days=1)
        )
        self.decide(VerificationStatus.CONFIRMED)
        self.managed.refresh_from_db()
        self.assertEqual(self.managed.status, ManagedRecording.Status.INACTIVE)

    def test_future_confirmation_that_reaches_start_is_historical_after_rejection(
        self,
    ):
        self.replace_claim(valid_from=timezone.localdate() + timedelta(days=1))
        self.decide(VerificationStatus.CONFIRMED)
        self.clock.return_value += timedelta(days=2)
        self.decide(VerificationStatus.REJECTED)
        self.managed.refresh_from_db()
        self.assertEqual(self.managed.status, ManagedRecording.Status.INACTIVE)

    def test_missing_configuration_blocks_return_without_deleting_membership(
        self,
    ):
        self.decide(VerificationStatus.REJECTED)
        RightsConfiguration.objects.get().delete()
        with self.assertRaises(ValidationError):
            return_to_music_library(
                self.managed, user=self.user, reason="Ukjent organisasjon"
            )
        self.assertTrue(
            ManagedRecording.objects.filter(pk=self.managed.pk).exists()
        )

    def test_undocumented_legacy_state_is_not_guessed_or_returned(self):
        self.managed.status = ManagedRecording.Status.ACTIVE
        self.managed.save(update_fields=("status",))
        self.decide(VerificationStatus.REJECTED)
        state = management_state(self.managed)
        self.assertIsNone(state.status)
        self.assertTrue(state.history_uncertain)
        self.assertTrue(state.requires_follow_up)
        with self.assertRaises(ValidationError):
            return_to_music_library(
                self.managed, user=self.user, reason="Ukjent historikk"
            )

    def test_explicit_return_preserves_catalogue_rights_and_source_history(
        self,
    ):
        source_system = SourceSystem.objects.create(
            name="Onboarding", kind=SourceSystem.Kind.IMPORT
        )
        source = SourceRecord.objects.create(
            source_system=source_system, raw_payload={"row": 1}
        )
        self.replace_claim(source_record=source)
        self.decide(VerificationStatus.REJECTED)
        assertion = MetadataAssertion.objects.create(
            source_record=source,
            entity_type=MetadataAssertion.EntityType.MANAGED_RECORDING,
            entity_uuid=self.managed.pk,
            field_name="status",
            raw_value="pending",
        )
        before_claims = list(
            RightsClaim.objects.values("id", "status", "source_record_id")
        )
        before_decisions = list(
            RightsDecision.objects.values("id", "claim_id", "decision")
        )
        managed_id = self.managed.pk
        entry_id = self.managed.library_entry_id
        audit = return_to_music_library(
            self.managed, user=self.user, reason="Feil onboarding"
        )
        self.assertFalse(
            ManagedRecording.objects.filter(pk=managed_id).exists()
        )
        self.assertTrue(
            MusicLibraryEntry.objects.filter(
                pk=entry_id, recording=self.recording
            ).exists()
        )
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.title, "Bevart katalog")
        self.assertEqual(
            list(
                RightsClaim.objects.values("id", "status", "source_record_id")
            ),
            before_claims,
        )
        self.assertEqual(
            list(RightsDecision.objects.values("id", "claim_id", "decision")),
            before_decisions,
        )
        self.assertTrue(
            MetadataAssertion.objects.filter(pk=assertion.pk).exists()
        )
        source.refresh_from_db()
        self.assertEqual(source.raw_payload, {"row": 1})
        self.assertEqual(
            audit.raw_payload["performed_by_id"], str(self.user.pk)
        )
        self.assertEqual(
            audit.raw_payload["managed_recording"]["id"], str(managed_id)
        )
        self.assertEqual(
            audit.raw_payload["managed_recording"]["notes"],
            "Menneskeskapt katalogkunnskap",
        )
        self.assertEqual(audit.raw_payload["reason"], "Feil onboarding")
        self.assertFalse(database_is_catalogue_authority(self.recording))

    def test_return_requires_permission_reason_and_no_plausible_basis(self):
        outsider = get_user_model().objects.create_user(username="reader")
        with self.assertRaises(PermissionDenied):
            return_to_music_library(
                self.managed, user=outsider, reason="Avbryt"
            )
        with self.assertRaises(ValidationError):
            return_to_music_library(
                self.managed, user=self.user, reason="Avbryt"
            )
        self.decide(VerificationStatus.REJECTED)
        with self.assertRaises(ValidationError):
            return_to_music_library(self.managed, user=self.user, reason=" ")
        with patch(
            "managed_music.services.SourceRecord.objects.create",
            side_effect=RuntimeError("audit failed"),
        ):
            with self.assertRaises(RuntimeError):
                return_to_music_library(
                    self.managed, user=self.user, reason="Avbryt"
                )
        self.assertTrue(
            ManagedRecording.objects.filter(pk=self.managed.pk).exists()
        )

    def test_third_party_claim_alone_does_not_create_or_activate_management(
        self,
    ):
        other = Party.objects.create(
            name="Tredjepart", kind=Party.Kind.ORGANIZATION
        )
        ordinary = Recording.objects.create(title="Ordinær")
        MusicLibraryEntry.objects.create(recording=ordinary)
        third_party = create_rights_claim(
            recording=ordinary,
            right_type=RightsClaim.RightType.OWNERSHIP,
            rights_holder=other,
            share=100,
        )
        self.decide(VerificationStatus.CONFIRMED, third_party)
        self.assertFalse(
            ManagedRecording.objects.filter(
                library_entry__recording=ordinary
            ).exists()
        )
        self.decide(VerificationStatus.REJECTED)
        related_claim = create_rights_claim(
            recording=self.recording,
            right_type=RightsClaim.RightType.OWNERSHIP,
            rights_holder=other,
            share=100,
        )
        self.decide(VerificationStatus.CONFIRMED, related_claim)
        self.assertTrue(management_state(self.managed).requires_follow_up)

    def test_release_and_recording_management_do_not_cascade(self):
        release = Release.objects.create(title="Samleplate")
        ReleaseTrack.objects.create(
            release=release, recording=self.recording, sequence_number=1
        )
        self.decide(VerificationStatus.CONFIRMED)
        self.assertFalse(
            ManagedRelease.objects.filter(release=release).exists()
        )
        ordinary = Recording.objects.create(title="Eksternt spor")
        ReleaseTrack.objects.create(
            release=release, recording=ordinary, sequence_number=2
        )
        save_managed_release(
            release=release,
            status=ManagedRelease.Status.ACTIVE,
            relationship=ManagedRelease.Relationship.OWNED_CATALOGUE,
        )
        self.assertFalse(
            ManagedRecording.objects.filter(
                library_entry__recording=ordinary
            ).exists()
        )
        self.assertFalse(
            RightsClaim.objects.filter(recording=ordinary).exists()
        )

    def test_admin_cannot_bypass_explicit_return_service(self):
        admin = get_user_model().objects.create_superuser(
            "admin", password="test"
        )
        self.client.force_login(admin)
        response = self.client.post(
            reverse(
                "admin:managed_music_managedrecording_delete",
                args=(self.managed.pk,),
            ),
            {"post": "yes"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            ManagedRecording.objects.filter(pk=self.managed.pk).exists()
        )
