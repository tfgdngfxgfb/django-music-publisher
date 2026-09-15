"""Real separate-connection tests for atomic human bulk decisions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import SkipTest

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase

from catalogue.models import Recording, Release
from media_assets.digitization import apply_plan, preview_operation
from media_assets.models import (
    DigitizationBatch,
    DigitizationDerivation,
    DigitizationFile,
    DigitizationPlan,
    FileAsset,
    RecordingMediaSelection,
)


class PostgreSQLDigitizationTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            raise SkipTest(
                "Krever PostgreSQL for separate transaksjoner og radlåsing."
            )

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "concurrent-digitizer", "", "test"
        )
        self.release = Release.objects.create(title="Concurrent digitization")
        self.batch = DigitizationBatch.objects.create(
            release=self.release, title="Capture", created_by=self.user
        )
        self.raws = [
            FileAsset.objects.create(
                release=self.release,
                filename=f"raw{n}.wav",
                role=FileAsset.Role.RAW_DIGITIZATION,
            )
            for n in range(2)
        ]
        self.recording = Recording.objects.create(title="Sang")
        self.masters = [
            FileAsset.objects.create(
                recording=self.recording,
                filename=f"master{n}.wav",
                role=FileAsset.Role.EDITED_WAV_MASTER,
            )
            for n in range(2)
        ]
        for asset in self.raws + self.masters:
            DigitizationFile.objects.create(batch=self.batch, asset=asset)

    def race(self, plans):
        barrier = Barrier(2)

        def worker(pk):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.user.pk)
                plan = DigitizationPlan.objects.get(pk=pk)
                barrier.wait(timeout=10)
                try:
                    apply_plan(plan=plan, user=user)
                    return "applied"
                except ValidationError:
                    return "stale"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, plan.pk) for plan in plans]
            return [future.result(timeout=30) for future in futures]

    def test_concurrent_raw_assignments_apply_once_without_partial_links(self):
        plans = [
            preview_operation(
                batch=self.batch,
                user=self.user,
                operation="raw_link",
                payload={
                    "source": str(raw.pk),
                    "assets": [str(a.pk) for a in self.masters],
                },
            )
            for raw in self.raws
        ]
        self.assertCountEqual(self.race(plans), ["applied", "stale"])
        links = list(DigitizationDerivation.objects.filter(is_active=True))
        self.assertEqual(len(links), 2)
        self.assertEqual(len({link.source_asset_id for link in links}), 1)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'digitization_one_active_source'"
            )
            definition = cursor.fetchone()[0]
        self.assertIn("UNIQUE", definition)
        self.assertIn("WHERE is_active", definition)

    def test_concurrent_bulk_master_choices_cannot_replace_each_other(self):
        plans = [
            preview_operation(
                batch=self.batch,
                user=self.user,
                operation="select_master",
                payload={"assets": [str(asset.pk)]},
            )
            for asset in self.masters
        ]
        self.assertCountEqual(self.race(plans), ["applied", "stale"])
        self.assertEqual(RecordingMediaSelection.objects.count(), 1)
        self.assertIn(
            RecordingMediaSelection.objects.get().selected_master_id,
            [a.pk for a in self.masters],
        )
