"""All payloads/mapping plans below are synthetic, not vendor schemas."""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

from django.test import SimpleTestCase

from import_readiness.contracts import (
    CanonicalValue,
    Concept,
    Disposition,
    FieldMapping,
    FrozenJSON,
    MappingPlan,
    MappingResult,
    Readiness,
    ReimportAction,
    SourceEnvelope,
)
from import_readiness.mapping import (
    IdentifierFact,
    MatchFacts,
    MatchStrength,
    assess_recording_match,
    group_legal_positions,
)
from import_readiness.profiles import all_profiles, get_profile
from import_readiness.readiness import assess_import_readiness, assess_reimport
from rights.followup import ClaimFact, TerritoryFact


def envelope(payload, *, source="mudi", external_id="synthetic-1"):
    return SourceEnvelope(
        source, "synthetic-v1", external_id, "synthetic.json", payload
    )


def plan(*fields, source="mudi"):
    return MappingPlan(
        source,
        "synthetic-v1",
        "synthetic test fixture; NOT a vendor schema",
        fields,
    )


class ImportReadinessTests(SimpleTestCase):
    # SimpleTestCase rejects every database query: all exercised paths are pure.
    def test_payload_is_deeply_immutable_and_round_trips(self):
        payload = {"nested": ["æ", {"none": None, "flag": False}], "zero": 0}
        original = envelope(payload)
        payload["nested"][1]["none"] = "changed"
        decoded = original.raw_payload.decode()
        self.assertIsNone(decoded["nested"][1]["none"])
        decoded["nested"].append("changed")
        self.assertEqual(len(original.raw_payload.decode()["nested"]), 2)
        with self.assertRaises(FrozenInstanceError):
            original.source_name = "changed"
        with self.assertRaises(FrozenInstanceError):
            original.raw_payload.text = "{}"

    def test_non_json_payloads_are_rejected_without_coercion(self):
        for mutable_or_bytes in (bytearray(b"{}"), b"{}"):
            with self.assertRaises(TypeError):
                FrozenJSON(mutable_or_bytes)
        for value in (
            {1: "would lose key type"},
            {"amount": Decimal("1.2")},
            {"nan": float("nan")},
            {"set": {1, 2}},
            ("tuple",),
        ):
            with self.subTest(value=value), self.assertRaises(
                (ValueError, TypeError)
            ):
                envelope(value)

    def test_dispositions_and_concepts_are_validated(self):
        with self.assertRaises(ValueError):
            MappingResult(("x",), None, "AUTO_CONFIRM", {})
        with self.assertRaises(ValueError):
            FieldMapping(("x",), "made.up.field")
        result = MappingResult(["x"], None, "UNKNOWN_SOURCE", {"mutable": []})
        self.assertIs(result.disposition, Disposition.UNKNOWN_SOURCE)
        self.assertEqual(result.source_path, ("x",))

    def test_reviewed_mapping_plan_rejects_ambiguity(self):
        title = FieldMapping(("x",), Concept.RECORDING_TITLE)
        with self.assertRaises(ValueError):
            MappingPlan("mudi", None, "", (title,))
        for fields in (
            (title, title),
            (title, FieldMapping(("x", "child"), Concept.RELEASE_TITLE)),
            (title, FieldMapping(("y",), Concept.RECORDING_TITLE)),
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                plan(*fields)
        with self.assertRaises(ValueError):
            FieldMapping(("id",), Concept.EXTERNAL_RECORDING_ID)
        with self.assertRaises(ValueError):
            FieldMapping(("id",), Concept.ISRC, "mudi")

    def test_all_profiles_declare_unknown_source_schema_and_complete_matrix(
        self,
    ):
        profiles = all_profiles()
        self.assertEqual(
            [p.source_name for p in profiles],
            ["mudi", "orchard", "klango", "lumi"],
        )
        for profile in profiles:
            self.assertEqual(profile.schema_status, "SAMPLE_REQUIRED")
            self.assertEqual(len(profile.matrix), 17)
            self.assertTrue(
                all(
                    m.source_status == Readiness.UNKNOWN
                    for m in profile.matrix
                )
            )
        self.assertIsNone(get_profile("not-known"))

    def test_unknown_fields_remain_unknown_even_with_plausible_names(self):
        source = envelope(
            {
                "title": "Synthetic",
                "isrc": "NOABC2600001",
                "owner": "P7",
                "nested": {"a": 1},
            }
        )
        report = assess_import_readiness(source)
        self.assertEqual(len(report.mappings), 4)
        self.assertTrue(
            all(
                m.disposition == Disposition.UNKNOWN_SOURCE
                and m.concept is None
                for m in report.mappings
            )
        )
        self.assertIn("SAMPLE_REQUIRED", report.blockers[0])
        self.assertEqual(report.source, source)

    def test_partial_plan_does_not_silently_drop_unknown_fields(self):
        report = assess_import_readiness(
            envelope({"known": "Name", "unknown": [1, 2]}),
            plan=plan(FieldMapping(("known",), Concept.RECORDING_TITLE)),
        )
        self.assertEqual(
            [r.concept for r in report.mappings],
            [Concept.RECORDING_TITLE, None, None],
        )
        self.assertEqual(len(report.unknowns), 2)

    def test_exact_source_and_version_are_required(self):
        source = envelope({"title": "Name"})
        for wrong in (
            replace(plan(), source_name="orchard"),
            replace(plan(), source_version="other"),
        ):
            with self.assertRaises(ValueError):
                assess_import_readiness(source, plan=wrong)

    def test_missing_sparse_values_do_not_generate_defaults(self):
        mapping = plan(
            FieldMapping(("isrc",), Concept.ISRC),
            FieldMapping(("date",), Concept.RELEASE_DATE),
            FieldMapping(("rights",), Concept.OWNERSHIP),
            source="lumi",
        )
        report = assess_import_readiness(
            envelope({"date": None}, source="lumi"), plan=mapping
        )
        self.assertEqual(len(report.mappings), 1)
        self.assertIsNone(report.mappings[0].normalized_value)
        self.assertEqual(len(report.unknowns), 2)
        self.assertFalse(any(r.proposed_status for r in report.mappings))

    def test_identifiers_use_existing_normalizers_without_becoming_uuids(self):
        source = envelope(
            {
                "i": "no-abc-26-00001",
                "ean": "4006381333931",
                "upc": "036000291452",
                "gtin": "04006381333931",
                "ext": "  source-1  ",
            }
        )
        mapping = plan(
            FieldMapping(("i",), Concept.ISRC),
            FieldMapping(("ean",), Concept.EAN),
            FieldMapping(("upc",), Concept.UPC),
            FieldMapping(("gtin",), Concept.GTIN),
            FieldMapping(("ext",), Concept.EXTERNAL_RECORDING_ID, "mudi"),
        )
        report = assess_import_readiness(source, plan=mapping)
        self.assertTrue(
            all(
                r.disposition == Disposition.MATCH_REQUIRED
                for r in report.mappings
            )
        )
        values = {
            r.concept: r.normalized_value.decode() for r in report.mappings
        }
        self.assertEqual(values[Concept.ISRC], "NOABC2600001")
        self.assertEqual(values[Concept.EXTERNAL_RECORDING_ID], "source-1")
        self.assertEqual(source.raw_payload.decode()["ext"], "  source-1  ")

    def test_bad_identifiers_remain_raw_review_values(self):
        report = assess_import_readiness(
            envelope({"i": "invalid", "ean": "4006381333932"}),
            plan=plan(
                FieldMapping(("i",), Concept.ISRC),
                FieldMapping(("ean",), Concept.EAN),
            ),
        )
        self.assertTrue(
            all(
                r.disposition == Disposition.MANUAL_REVIEW
                and r.normalized_value is None
                for r in report.mappings
            )
        )

    def test_year_is_not_promoted_to_full_date(self):
        report = assess_import_readiness(
            envelope({"year": 1981, "date": "1981"}),
            plan=plan(
                FieldMapping(("year",), Concept.RELEASE_YEAR),
                FieldMapping(("date",), Concept.RELEASE_DATE),
            ),
        )
        values = {r.concept: r for r in report.mappings}
        self.assertEqual(
            values[Concept.RELEASE_YEAR].normalized_value.decode(), 1981
        )
        self.assertIsNone(values[Concept.RELEASE_DATE].normalized_value)

    def test_titles_can_normalize_without_granting_write_authority(self):
        report = assess_import_readiness(
            envelope({"t": "  Name  "}),
            plan=plan(FieldMapping(("t",), Concept.RECORDING_TITLE)),
        )
        self.assertEqual(
            report.mappings[0].disposition, Disposition.NORMALIZED
        )
        self.assertEqual(report.mappings[0].normalized_value.decode(), "Name")
        self.assertEqual(report.mappings[0].raw_value.decode(), "  Name  ")

    def test_objectwise_authority_and_mismatches_preserve_assertions(self):
        mapping = plan(
            FieldMapping(("r",), Concept.RECORDING_TITLE),
            FieldMapping(("a",), Concept.RELEASE_TITLE),
            source="klango",
        )
        source = envelope({"r": "Source title", "a": "Album"}, source="klango")
        recording_protected = assess_import_readiness(
            source, plan=mapping, protected_concepts=(Concept.RECORDING_TITLE,)
        )
        states = {
            r.concept: r.disposition for r in recording_protected.mappings
        }
        self.assertEqual(
            states[Concept.RECORDING_TITLE], Disposition.ASSERTION_ONLY
        )
        self.assertEqual(states[Concept.RELEASE_TITLE], Disposition.DIRECT)
        mismatch = assess_import_readiness(
            source,
            plan=mapping,
            canonical_values=(
                CanonicalValue(Concept.RELEASE_TITLE, "Existing album"),
            ),
        )
        self.assertEqual(
            mismatch.mappings[0].disposition, Disposition.ASSERTION_ONLY
        )
        self.assertTrue(mismatch.manual_review)

    def test_rights_and_membership_are_only_workflow_proposals(self):
        concepts = (
            Concept.OWNERSHIP,
            Concept.ADMINISTRATION,
            Concept.DISTRIBUTION,
            Concept.MANAGED_RECORDING,
            Concept.MANAGED_RELEASE,
            Concept.AGREEMENT,
        )
        payload = {
            str(i): {"synthetic": "CONFIRMED"} for i in range(len(concepts))
        }
        mapping = plan(
            *(FieldMapping((str(i),), c) for i, c in enumerate(concepts))
        )
        report = assess_import_readiness(envelope(payload), plan=mapping)
        self.assertTrue(
            all(
                r.disposition == Disposition.WORKFLOW_REQUIRED
                for r in report.mappings
            )
        )
        self.assertEqual(
            [r.proposed_status for r in report.mappings[:3]],
            ["unverified"] * 3,
        )
        self.assertTrue(
            all(r.proposed_status is None for r in report.mappings[3:])
        )
        self.assertTrue(
            all(r.normalized_value is None for r in report.mappings[3:5])
        )

    def test_orchard_listing_never_infers_ownership(self):
        report = assess_import_readiness(
            envelope({"listing": True}, source="orchard")
        )
        self.assertFalse(
            any(
                r.concept in (Concept.OWNERSHIP, Concept.MANAGED_RECORDING)
                for r in report.mappings
            )
        )
        self.assertTrue(
            any("not ownership" in note for note in report.warnings)
        )

    def test_party_and_work_dependencies_are_explicit(self):
        report = assess_import_readiness(
            envelope({"artist": "Someone", "work": {"composer": "Someone"}}),
            plan=plan(
                FieldMapping(("artist",), Concept.CONTRIBUTOR),
                FieldMapping(("work",), Concept.WORK),
            ),
        )
        self.assertEqual(
            [r.dependency for r in report.mappings], ["PHASE_7", "PHASE_8"]
        )
        self.assertEqual(
            report.mappings[1].disposition, Disposition.UNSUPPORTED
        )
        self.assertTrue(report.unsupported)

    def test_same_source_id_payload_reuses_original_provenance(self):
        existing = envelope({"a": 1, "b": 2})
        incoming = envelope({"b": 2, "a": 1})
        self.assertEqual(
            assess_reimport(incoming, (existing,)).action,
            ReimportAction.REUSE_EXISTING,
        )
        self.assertEqual(existing.raw_payload.decode(), {"a": 1, "b": 2})

    def test_changed_payload_same_id_is_revision_gap_not_overwrite(self):
        original, changed = envelope({"a": 1}), envelope({"a": 2})
        report = assess_import_readiness(changed, existing_sources=(original,))
        self.assertEqual(
            report.reimport.action, ReimportAction.UPSTREAM_REVISION_GAP
        )
        self.assertTrue(any("revision" in b for b in report.blockers))
        self.assertEqual(original.raw_payload.decode(), {"a": 1})

    def test_same_payload_different_or_missing_id_requires_review(self):
        original = envelope({"a": 1})
        for external_id in ("different", None, ""):
            incoming = envelope({"a": 1}, external_id=external_id)
            self.assertEqual(
                assess_reimport(incoming, (original,)).action,
                ReimportAction.DUPLICATE_PAYLOAD_REVIEW,
            )

    def test_different_sources_do_not_share_idempotency_identity(self):
        first, second = envelope({"a": 1}), envelope(
            {"a": 1}, source="orchard"
        )
        self.assertEqual(
            assess_reimport(second, (first,)).action,
            ReimportAction.NEW_OBSERVATION,
        )
        self.assertEqual(
            assess_reimport(first).action, ReimportAction.NEW_OBSERVATION
        )

    def test_title_alone_is_insufficient_but_multiple_signals_are_candidates(
        self,
    ):
        facts = MatchFacts(
            title="Name", artist_credit="Artist", duration_ms=10000
        )
        self.assertEqual(
            assess_recording_match(MatchFacts(title="Name"), facts).strength,
            MatchStrength.INSUFFICIENT,
        )
        match = assess_recording_match(
            MatchFacts(title="Name", duration_ms=12000), facts
        )
        self.assertEqual(match.strength, MatchStrength.CANDIDATE)
        self.assertEqual(match.disposition, Disposition.MATCH_REQUIRED)

    def test_match_facts_reject_mutable_labels_and_invalid_durations(self):
        for invalid in (
            {"title": []},
            {"artist_credit": {}},
            {"release_context": []},
        ):
            with self.assertRaises(TypeError):
                MatchFacts(**invalid)
        for invalid in (True, -1, 1.5, "1000", []):
            with self.assertRaises(ValueError):
                MatchFacts(duration_ms=invalid)

    def test_isrc_and_namespaced_ids_are_strong_signals_not_merge(self):
        incoming = MatchFacts((IdentifierFact("ISRC", "no-abc-26-00001"),))
        match = assess_recording_match(incoming, incoming)
        self.assertEqual(match.strength, MatchStrength.STRONG)
        self.assertEqual(match.disposition, Disposition.MATCH_REQUIRED)
        a = MatchFacts((IdentifierFact("EXTERNAL", "1", "mudi"),))
        b = MatchFacts((IdentifierFact("EXTERNAL", "1", "orchard"),))
        self.assertEqual(
            assess_recording_match(a, b).strength, MatchStrength.NONE
        )
        self.assertEqual(
            assess_recording_match(a, a).strength, MatchStrength.STRONG
        )

    def test_conflicting_strong_ids_override_name_agreement(self):
        a = MatchFacts((IdentifierFact("ISRC", "NOABC2600001"),), title="Same")
        b = MatchFacts((IdentifierFact("ISRC", "NOABC2600002"),), title="Same")
        self.assertEqual(
            assess_recording_match(a, b).strength, MatchStrength.CONFLICT
        )

    def test_legal_identity_reuses_6i_semantics_without_additive_duplicates(
        self,
    ):
        claim = ClaimFact(
            "claim-1",
            "recording",
            "master_ownership",
            "p7",
            share=Decimal("50"),
            territory_mode="include",
            territories=(TerritoryFact("no", "NO"),),
        )
        documented_elsewhere = replace(
            claim,
            pk="claim-2",
            evidence_strength="documented",
            details=(("source", "another source"),),
        )
        other_position = replace(
            claim, pk="claim-3", grantor_id="another-grantor"
        )
        self.assertEqual(
            group_legal_positions(
                (claim, documented_elsewhere, other_position)
            ),
            ((0, 1), (2,)),
        )
        self.assertEqual(claim.share, Decimal("50"))

    def test_reports_are_deterministic_and_have_no_production_apply(self):
        source = envelope({"b": 2, "a": {"c": 3}})
        self.assertEqual(
            assess_import_readiness(source), assess_import_readiness(source)
        )
        report = assess_import_readiness(source)
        self.assertFalse(hasattr(report, "save"))
        self.assertFalse(hasattr(report, "apply"))
        with self.assertRaises(FrozenInstanceError):
            report.blockers = ()

    def test_json_fingerprint_is_stable_without_mutating_raw_order(self):
        first, second = FrozenJSON.capture(
            {"a": 1, "b": 2}
        ), FrozenJSON.capture({"b": 2, "a": 1})
        self.assertNotEqual(first.text, second.text)
        self.assertEqual(first.fingerprint, second.fingerprint)
