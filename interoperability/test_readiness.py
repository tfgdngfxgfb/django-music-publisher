"""Synthetic canonical facts only, not real RDR-N/CWR messages."""

from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from interoperability.contracts import (
    Adapter,
    Dependency,
    Direction,
    Disposition,
    EntityFacts,
    Fact,
    MappingEntry,
)
from interoperability.readiness import (
    assess_interoperability,
    assess_rdrn_readiness,
    milliseconds_to_duration,
)
from interoperability.registry import REGISTRY, Registry
from interoperability.standards.cwr_2_2_rev2 import ADAPTER as CWR
from interoperability.standards.rdrn_1_5 import ADAPTER as RDRN


def facts(**values):
    return EntityFacts(
        tuple(Fact(key, value) for key, value in values.items())
    )


class ContractsTests(SimpleTestCase):
    def test_contracts_reject_mutable_or_lazy_facts(self):
        for value in ({"raw": []}, [], object(), ([],), Decimal("NaN")):
            with self.subTest(value=value), self.assertRaises(TypeError):
                Fact("recording.title", value)
        item = Fact("recording.title", "Synthetic title")
        with self.assertRaises(FrozenInstanceError):
            item.value = "Changed"
        supplied = [item]
        snapshot = EntityFacts(supplied)
        supplied.clear()
        self.assertEqual(snapshot.facts, (item,))

    def test_immutable_nested_facts_are_supported(self):
        nested = ("world", (Decimal("50.00"), date(2020, 1, 1), None))
        self.assertEqual(Fact("claim.scope", nested).value, nested)

    def test_duplicate_facts_are_rejected(self):
        with self.assertRaises(ValueError):
            EntityFacts((Fact("title", "A"), Fact("title", "B")))

    def test_mapping_validation_and_phase_dependencies(self):
        entry = RDRN.entries[0]
        for changes in (
            {"direction": "SIDEWAYS"},
            {"disposition": "MAYBE"},
            {"standard_path": ""},
            {"disposition": Disposition.DERIVED, "transform": None},
            {"disposition": Disposition.PHASE_7, "dependencies": ()},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(entry, **changes)
        with self.assertRaises(ValueError):
            MappingEntry(
                RDRN.standard_id,
                "empty",
                (),
                Direction.BOTH,
                Disposition.DIRECT,
                "invalid",
            )

    def test_duplicate_registry_ids_and_paths_fail(self):
        with self.assertRaises(ValueError):
            Registry((RDRN, RDRN))
        with self.assertRaises(ValueError):
            replace(RDRN, entries=RDRN.entries + (RDRN.entries[0],))

    def test_core_coverage_cannot_silently_disappear(self):
        with self.assertRaises(ValueError):
            replace(RDRN, core_paths=("Unmapped mandatory concept",))
        for adapter in REGISTRY.adapters:
            declared = {e.standard_path: e for e in adapter.entries}
            for path in adapter.core_paths:
                self.assertIsInstance(declared[path].disposition, Disposition)

    def test_pinned_versions_no_current_fallback(self):
        self.assertEqual(
            REGISTRY.standard_ids, ("cisac:cwr:2.2-rev2", "ddex:rdr-n:1.5")
        )
        for version in ("CURRENT_RDRN", "ddex:rdr-n:1.4", "cisac:cwr:2.2"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                REGISTRY.get(version)
        with self.assertRaises(ValueError):
            replace(RDRN, standard_id="ddex:rdr-n:1.4")

    def test_registry_extensible_without_changing_old_adapters(self):
        future_id = "synthetic:future:1.0"
        adapter = Adapter(
            future_id,
            (
                MappingEntry(
                    future_id,
                    "title",
                    ("recording.title",),
                    Direction.BOTH,
                    Disposition.DIRECT,
                    "Synthetic extension, not a real standard.",
                ),
            ),
            ("title",),
            (),
        )
        registry = Registry((adapter, RDRN, CWR))
        report = assess_interoperability(
            facts(**{"recording.title": "A"}), future_id, registry=registry
        )
        self.assertEqual(report.paths(Disposition.DIRECT), ("title",))
        self.assertEqual(
            REGISTRY.standard_ids, ("cisac:cwr:2.2-rev2", "ddex:rdr-n:1.5")
        )


class ReadinessTests(SimpleTestCase):
    # SimpleTestCase prohibits database access: every assessment here is pure.
    def test_empty_input_reports_gaps_and_dependencies_not_completeness(self):
        for adapter in REGISTRY.adapters:
            report = assess_interoperability(
                EntityFacts(), adapter.standard_id
            )
            self.assertTrue(report.blockers)
            self.assertFalse(report.paths(Disposition.DIRECT))
        cwr = assess_interoperability(EntityFacts(), CWR.standard_id)
        self.assertEqual(
            cwr.dependencies, (Dependency.PHASE_7, Dependency.PHASE_8)
        )

    def test_loaded_catalogue_values_make_only_supported_concepts_ready(self):
        report = assess_rdrn_readiness(
            facts(
                **{
                    "recording.title": "Synthetic recording",
                    "recording.isrc": "NOABC2600001",
                    "recording.duration_ms": 183501,
                    "release.title": "Synthetic release",
                    "release.catalogue_number": "P7-TEST",
                    "release.barcode": "012345678905",
                    "release.barcode_scheme": "UPC",
                }
            )
        )
        self.assertIn(
            "SoundRecording/SoundRecordingId/ISRC",
            report.paths(Disposition.DIRECT),
        )
        self.assertIn(
            "HostSoundCarrier/ReleaseId/ICPN", report.paths(Disposition.DIRECT)
        )
        duration = next(
            a
            for a in report.assessments
            if a.entry.standard_path == "SoundRecording/Duration"
        )
        self.assertEqual(duration.derived_value, "PT183.501S")
        self.assertTrue(
            report.blockers
        )  # InitialProducer/rights/P-line unresolved

    def test_invalid_identifiers_are_never_ready(self):
        for value in ("NO-ABC-26-00001", "invalid", 123, ""):
            report = assess_rdrn_readiness(facts(**{"recording.isrc": value}))
            self.assertNotIn(
                "SoundRecording/SoundRecordingId/ISRC",
                report.paths(Disposition.DIRECT),
            )
        for scheme, value in (
            ("UPC", "012345678906"),
            ("ISRC", "NOABC2600001"),
        ):
            report = assess_rdrn_readiness(
                facts(
                    **{
                        "release.barcode": value,
                        "release.barcode_scheme": scheme,
                    }
                )
            )
            self.assertNotIn(
                "HostSoundCarrier/ReleaseId/ICPN",
                report.paths(Disposition.DIRECT),
            )

    def test_duration_is_exact_including_zero_and_rejects_guesses(self):
        for value, expected in (
            (0, "PT0S"),
            (1, "PT0.001S"),
            (30500, "PT30.5S"),
            (3600000, "PT3600S"),
        ):
            self.assertEqual(milliseconds_to_duration(value), expected)
        for value in (-1, True, "180000", None):
            report = assess_rdrn_readiness(
                facts(**{"recording.duration_ms": value})
            )
            self.assertIn(
                "SoundRecording/Duration", report.paths(Disposition.MISSING)
            )

    def test_missing_share_producer_and_original_date_are_not_inferred(self):
        report = assess_rdrn_readiness(
            facts(
                **{
                    "recording.contributions": (
                        ("producer", "Synthetic Party"),
                    ),
                    "claim.share": None,
                    "claim.right_type": "master_ownership",
                    "release.release_year": 1994,
                    "release.label_name": "P7",
                    "management.recording": "active",
                }
            )
        )
        self.assertIn(
            "SoundRecording/InitialProducer", report.paths(Disposition.MISSING)
        )
        self.assertIn(
            "SoundRecording/OriginalResourceReleaseDate",
            report.paths(Disposition.MISSING),
        )
        self.assertIn(
            "SoundRecording/RightsController/RightSharePercentage",
            report.paths(Disposition.LOSSY),
        )
        self.assertIn(
            "P7/management membership (no standard equivalent)",
            report.paths(Disposition.NOT_APPLICABLE),
        )

    def test_cwr_does_not_promote_recording_composer_or_even_supplied_work_facts(
        self,
    ):
        report = assess_interoperability(
            facts(
                **{
                    "recording.contributions": (
                        ("composer", "Synthetic Party"),
                    ),
                    "work.title": "Synthetic Work",
                    "work.writer_shares": Decimal("100"),
                    "claim.share": Decimal("100"),
                }
            ),
            CWR.standard_id,
        )
        self.assertFalse(report.paths(Disposition.DIRECT))
        self.assertEqual(
            len(report.paths(Disposition.PHASE_8)), len(CWR.entries)
        )
        writer = next(
            a
            for a in report.assessments
            if a.entry.standard_path == "SWR+OWR/Writer"
        )
        self.assertEqual(
            writer.entry.dependencies, (Dependency.PHASE_7, Dependency.PHASE_8)
        )

    def test_release_scoped_distribution_is_not_a_ready_controller(self):
        report = assess_rdrn_readiness(
            facts(
                **{
                    "claim.right_type": "distribution",
                    "claim.release_scope": "release-r",
                    "claim.rights_holder": "p7",
                    "claim.grantor": "someone-else",
                    "claim.territory_mode": "include",
                    "claim.territories": ("NO",),
                }
            )
        )
        self.assertIn(
            "SoundRecording/RightsController/RightsStatement/UseType",
            report.paths(Disposition.LOSSY),
        )
        self.assertIn(
            "SoundRecording/RightsController/PartyId",
            report.paths(Disposition.PHASE_7),
        )

    def test_input_order_does_not_change_report_unknown_values_stay_unknown(
        self,
    ):
        entries = (
            Fact("recording.title", "A"),
            Fact("invented.field", "value"),
        )
        first = assess_rdrn_readiness(EntityFacts(entries))
        self.assertEqual(
            first, assess_rdrn_readiness(EntityFacts(entries[::-1]))
        )
        self.assertEqual(first.unknown_facts, ("invented.field",))
        with self.assertRaises(TypeError):
            assess_rdrn_readiness(object())

    def test_date_precision_language_and_kind_stay_conservative(self):
        report = assess_rdrn_readiness(
            facts(
                **{
                    "release.release_year": 1992,
                    "recording.language": "nb-NO",
                    "recording.recording_kind": "sound",
                }
            )
        )
        self.assertIn(
            "HostSoundCarrier/release date (concept)",
            report.paths(Disposition.LOSSY),
        )
        self.assertIn(
            "SoundRecording/LanguageOfPerformance",
            report.paths(Disposition.LOSSY),
        )
        self.assertIn(
            "SoundRecording/SoundRecordingType",
            report.paths(Disposition.LOSSY),
        )
        self.assertTrue(
            all(a.derived_value is None for a in report.assessments)
        )
