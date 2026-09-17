"""Actual row-lock checks run in PostgreSQL CI, skipped by local SQLite."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from django.contrib.auth import get_user_model
from django.db import (
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.test import TransactionTestCase, skipUnlessDBFeature

from catalogue.models import Recording
from managed_music.lifecycle import reconcile_management_statuses
from managed_music.models import ManagedRecording
from managed_music.services import (
    create_managed_recording,
    return_to_music_library,
)
from rights.models import RightsClaim, RightsConfiguration
from rights.services import decide_rights_claim
from parties.models import Party
from rights_core.models import VerificationStatus as Status


@skipUnlessDBFeature("has_select_for_update")
class LifecycleLockingTests(TransactionTestCase):
    def setUp(self):
        local = Party.objects.create(name="P7", kind=Party.Kind.ORGANIZATION)
        RightsConfiguration.objects.create(local_organization=local)
        self.user = get_user_model().objects.create_superuser(
            "reconcile-admin"
        )
        self.managed = create_managed_recording(
            new_recording_title="Serialized lifecycle",
            relationship_type=RightsClaim.RightType.ADMINISTRATION,
        )
        self.claim = self.managed.recording.rights_claims.get()

    def reconcile_during(self, mutation):
        attempted_lock = Event()

        def observe(execute, sql, params, many, context):
            if '"catalogue_recording"' in sql and "FOR UPDATE" in sql:
                attempted_lock.set()
            return execute(sql, params, many, context)

        def worker():
            close_old_connections()
            try:
                with connection.execute_wrapper(observe):
                    return reconcile_management_statuses()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Recording.objects.select_for_update().get(
                    pk=self.managed.recording.pk
                )
                future = pool.submit(worker)
                self.assertTrue(attempted_lock.wait(timeout=10))
                self.assertFalse(future.done())
                mutation()
            return future.result(timeout=10)

    def test_batch_rereads_claim_after_concurrent_decision(self):
        decide_rights_claim(self.claim, Status.CONFIRMED, user=self.user)
        result = self.reconcile_during(
            lambda: decide_rights_claim(
                self.claim, Status.REJECTED, user=self.user
            )
        )
        self.managed.refresh_from_db()
        self.assertEqual(self.managed.status, ManagedRecording.Status.INACTIVE)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.examined, 1)

    def test_batch_does_not_recreate_concurrently_returned_membership(self):
        decide_rights_claim(self.claim, Status.REJECTED, user=self.user)
        result = self.reconcile_during(
            lambda: return_to_music_library(
                self.managed,
                user=self.user,
                reason="Ingen gjenværende grunnlag",
            )
        )
        self.assertFalse(ManagedRecording.objects.exists())
        self.assertEqual(result.examined, 0)
        self.assertEqual(result.updated, 0)
