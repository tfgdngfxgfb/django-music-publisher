from pathlib import Path
from decimal import Decimal
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TestCase

from music_publisher.royalty_calculation import (
    RoyaltyCalculation,
    RoyaltyCalculationView,
)


class DownloadCompatibilityTests(SimpleTestCase):
    def test_royalty_download_deleted_after_stream_closed(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "royalties.csv"
            path.write_bytes(b"amount\n1.00\n")
            result = SimpleNamespace(out_file_path=str(path), filename="royalties.csv")
            with patch(
                "music_publisher.royalty_calculation.RoyaltyCalculation",
                return_value=result,
            ):
                response = RoyaltyCalculationView().form_valid(None)
            self.assertTrue(path.exists())
            self.assertEqual(b"".join(response.streaming_content), b"amount\n1.00\n")
            response.close()
            self.assertFalse(path.exists())
            response.close()


class MigrationCompatibilityTests(TestCase):
    def test_dmp_and_canonical_tables_and_renamed_index_exist(self):
        tables = connection.introspection.table_names()
        for name in (
            "music_publisher_work",
            "music_publisher_recording",
            "catalogue_recording",
            "parties_party",
        ):
            self.assertIn(name, tables)
        with connection.cursor() as cursor:
            indexes = connection.introspection.get_constraints(
                cursor, "music_publisher_workacknowledgement"
            )
        self.assertEqual(
            indexes["music_publi_society_ebcad0_idx"]["columns"],
            ["society_code", "remote_work_id"],
        )


class RoyaltyCalculationSafetyTests(SimpleTestCase):
    def calculation(self, share):
        calculation = RoyaltyCalculation.__new__(RoyaltyCalculation)
        calculation.wc = 0
        calculation.ac = 1
        calculation.right = "p"
        calculation.rc = None
        calculation.algo = "share"
        calculation.work_id_source = "ISWC"
        calculation.default_fee = Decimal("0")
        calculation.works = {
            "T1234567894": [
                {
                    "writer_id": 1,
                    "role": "Composer",
                    "relative_share": Decimal(share),
                    "fee": None,
                }
            ]
        }
        calculation.writers = {1: {"name": "Writer", "account_number": "", "fee": None}}
        return calculation

    def test_invalid_amount_is_reported_instead_of_crashing(self):
        rows = list(
            self.calculation("100").process_row(["T-123.456.789-4", "not-a-number"])
        )
        self.assertEqual(rows[0][-1], "ERROR: Invalid amount")

    def test_zero_controlled_share_is_reported_instead_of_dividing_by_zero(self):
        rows = list(self.calculation("0").process_row(["T-123.456.789-4", "100"]))
        self.assertEqual(rows[0][-1], "ERROR: Controlled share is zero")
