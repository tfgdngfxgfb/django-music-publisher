"""Real row-lock tests for PostgreSQL CI; SQLite intentionally skips these."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import (
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.test import TransactionTestCase, skipUnlessDBFeature

from catalogue.models import Recording, Release, ReleaseTrack
from managed_music.models import ManagedRecording
from managed_music.services import return_to_music_library
from parties.models import Party
from rights_core.models import VerificationStatus as Status

from . import workflows as wf
from .models import RightsClaim, RightsConfiguration


@skipUnlessDBFeature("has_select_for_update")
class WorkflowLockingTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "workflow-locking"
        )
        self.local = Party.objects.create(name="P7", kind="organization")
        RightsConfiguration.objects.create(local_organization=self.local)
        self.recording = Recording.objects.create(title="Serialized workflows")
        self.managed = wf.onboard_managed_recording(
            user=self.user,
            recording=self.recording,
            relationship_type=RightsClaim.RightType.ADMINISTRATION,
        )
        self.claim = self.recording.rights_claims.get()

    def while_locked(self, operation, mutation):
        attempted = Event()

        def observe(execute, sql, params, many, context):
            if '"catalogue_recording"' in sql and "FOR UPDATE" in sql:
                attempted.set()
            return execute(sql, params, many, context)

        def worker():
            close_old_connections()
            try:
                with connection.execute_wrapper(observe):
                    return operation()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Recording.objects.select_for_update().get(pk=self.recording.pk)
                future = pool.submit(worker)
                self.assertTrue(attempted.wait(timeout=10))
                self.assertFalse(future.done())
                mutation()
            return future.result(timeout=10)

    def test_supersede_serializes_and_terminal_status_is_reread(self):
        replace = lambda: wf.supersede_rights_claim(
            self.claim, user=self.user, rights_holder=self.local
        )
        with self.assertRaises(ValidationError):
            self.while_locked(replace, replace)
        self.assertEqual(self.claim.superseded_by.count(), 1)

    def test_documentation_uses_recording_first_and_preserves_concurrent_decision(
        self,
    ):
        self.while_locked(
            lambda: wf.document_rights_claim(
                self.claim, user=self.user, evidence_strength="strong"
            ),
            lambda: wf.decide_rights_claim(
                self.claim, Status.CONFIRMED, user=self.user
            ),
        )
        self.claim.refresh_from_db()
        self.managed.refresh_from_db()
        self.assertEqual(self.claim.status, Status.CONFIRMED)
        self.assertEqual(self.claim.evidence_strength, "strong")
        self.assertEqual(self.managed.status, ManagedRecording.Status.ACTIVE)

    def test_registration_does_not_recreate_concurrently_returned_membership(
        self,
    ):
        wf.decide_rights_claim(self.claim, Status.REJECTED, user=self.user)
        with self.assertRaises(ValidationError):
            self.while_locked(
                lambda: wf.register_rights_claim(
                    user=self.user,
                    recording=self.recording,
                    right_type=RightsClaim.RightType.DISTRIBUTION,
                    rights_holder=self.local,
                ),
                lambda: return_to_music_library(
                    self.managed, user=self.user, reason="Avvist onboarding"
                ),
            )
        self.assertFalse(ManagedRecording.objects.exists())
        self.assertEqual(RightsClaim.objects.count(), 1)

    def test_bulk_rechecks_track_revision_after_waiting(self):
        release = Release.objects.create(title="Concurrency release")
        track = ReleaseTrack.objects.create(
            release=release, recording=self.recording, sequence_number=1
        )
        values = dict(
            user=self.user,
            release=release,
            recordings=[self.recording],
            right_type=RightsClaim.RightType.DISTRIBUTION,
            rights_holder=self.local,
        )
        plan = wf.preview_release_rights_registration(**values)

        def edit():
            track.title_override = "Edited concurrently"
            track.save()

        with self.assertRaises(ValidationError):
            self.while_locked(
                lambda: wf.apply_release_rights_registration(
                    preview_token=plan.token, **values
                ),
                edit,
            )
        self.assertEqual(RightsClaim.objects.count(), 1)

    def test_concurrent_onboarding_creates_only_one_membership(self):
        self.recording = Recording.objects.create(
            title="Concurrent onboarding"
        )

        def onboard():
            return wf.onboard_managed_recording(
                user=self.user,
                recording=self.recording,
                relationship_type=RightsClaim.RightType.ADMINISTRATION,
            )

        with self.assertRaises(ValidationError):
            self.while_locked(onboard, onboard)
        self.assertEqual(
            ManagedRecording.objects.filter(
                library_entry__recording=self.recording
            ).count(),
            1,
        )
        self.assertEqual(self.recording.rights_claims.count(), 1)

    def test_legacy_correction_rereads_concurrent_confirmation(self):
        self.managed.status = ManagedRecording.Status.ACTIVE
        self.managed.save()
        with self.assertRaises(ValidationError):
            self.while_locked(
                lambda: wf.correct_legacy_management_history(
                    self.managed, user=self.user, reason="Feil status"
                ),
                lambda: wf.decide_rights_claim(
                    self.claim, Status.CONFIRMED, user=self.user
                ),
            )
        self.managed.refresh_from_db()
        self.assertEqual(self.managed.status, ManagedRecording.Status.ACTIVE)
