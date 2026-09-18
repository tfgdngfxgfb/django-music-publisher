from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from django.test import SimpleTestCase

from .digitization_matching import suggest_master_links


class MasterMatchingTests(SimpleTestCase):
    def tracks(self, positions):
        return [
            SimpleNamespace(
                pk=n,
                sequence_number=n,
                disc_number=1,
                side=side,
                track_number=number,
                recording_id=n,
            )
            for n, (side, number) in enumerate(positions, 1)
        ]

    def masters(self, names):
        return [
            SimpleNamespace(
                pk=n,
                filename=name,
                recording_id=None,
                release_track_id=None,
                source_modified_at=None,
            )
            for n, name in enumerate(names, 1)
        ]

    def test_explicit_side_positions_and_raw_side(self):
        tracks = self.tracks([("A", 1), ("A", 2), ("B", 1), ("B", 4)])
        for first in (
            "A1.wav",
            "A01.wav",
            "A1-test.wav",
            "A1_test.wav",
            "A1.test.wav",
        ):
            masters = self.masters(
                [first, "A2 test.wav", "B1_sang.wav", "B04.wav"]
            )
            raws = self.masters(["FMC102-spole-A.wav", "FMC102_spole_B.wav"])
            results = suggest_master_links(masters, tracks, raws, {})
            self.assertEqual(
                [r.track.pk for r in results.values()], [1, 2, 3, 4]
            )
            self.assertEqual(
                [r.source.pk for r in results.values()], [1, 1, 2, 2]
            )

    def test_numeric_sequence_requires_matching_counts_and_complete_numbers(
        self,
    ):
        tracks = self.tracks([("A", 1), ("B", 1)])
        masters = self.masters(["2.wav", "1.wav"])
        results = suggest_master_links(masters, tracks, [], {})
        self.assertEqual([r.track.pk for r in results.values()], [2, 1])
        for names in (["1.wav"], ["1.wav", "3.wav"]):
            self.assertTrue(
                all(
                    r.track is None
                    for r in suggest_master_links(
                        self.masters(names), tracks, [], {}
                    ).values()
                )
            )

    def test_timestamp_order_is_explained_and_ambiguity_is_manual(self):
        tracks = self.tracks([("A", 1), ("B", 1)])
        masters = self.masters(["Audio0001.wav", "Audio0002.wav"])
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        masters[0].source_modified_at = now + timedelta(seconds=1)
        masters[1].source_modified_at = now
        results = suggest_master_links(masters, tracks, [], {})
        self.assertEqual([r.track.pk for r in results.values()], [2, 1])
        self.assertIn("ikke sikker opprettelsesdato", results[1].reason)
        masters[1].source_modified_at = masters[0].source_modified_at
        self.assertTrue(
            all(
                r.track is None
                for r in suggest_master_links(masters, tracks, [], {}).values()
            )
        )

    def test_mismatch_never_shifts_order_and_duplicate_proposals_are_manual(
        self,
    ):
        tracks = self.tracks([("A", n) for n in range(1, 7)])
        masters = self.masters([f"Audio{n:04}.wav" for n in range(1, 6)])
        for n, master in enumerate(masters):
            master.source_modified_at = datetime(
                2026, 1, 1, tzinfo=timezone.utc
            ) + timedelta(seconds=n)
        self.assertTrue(
            all(
                r.track is None
                for r in suggest_master_links(masters, tracks, [], {}).values()
            )
        )
        duplicate = self.masters(["A1.wav", "A01 kopi.wav"])
        self.assertTrue(
            all(
                r.track is None
                for r in suggest_master_links(
                    duplicate, tracks, [], {}
                ).values()
            )
        )

    def test_saved_links_win_and_single_raw_can_supply_both_sides(self):
        tracks = self.tracks([("A", 1), ("B", 1)])
        masters = self.masters(["B1.wav", "A1.wav"])
        masters[0].release_track_id = 1
        masters[1].release_track_id = 2
        raw = self.masters(["Side A+B.wav"])[0]
        results = suggest_master_links(masters, tracks, [raw], {1: raw})
        self.assertEqual([r.track.pk for r in results.values()], [1, 2])
        self.assertTrue(all(r.source == raw for r in results.values()))
