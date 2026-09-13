from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from catalogue.models import Recording
from managed_music.services import create_managed_recording
from media_assets.models import FileAsset
from parties.models import Party
from provenance.models import SourceRecord, SourceSystem
from rights_core.models import VerificationStatus

from rights.models import (
    Agreement,
    AgreementDocument,
    AgreementParty,
    RightsClaim,
    RightsConfiguration,
    RightsDecision,
    Territory,
)
from rights.services import (
    create_rights_claim,
    decide_rights_claim,
    link_claim_agreement,
    supersede_rights_claim,
)
from rights.summaries import (
    OwnershipCategory,
    classify_ownership,
    has_local_confirmed_right,
)


class RightsDomainTests(TestCase):
    def setUp(self):
        self.recording = Recording.objects.create(title="Testmaster")
        self.owner_a = Party.objects.create(name="Eier A", kind=Party.Kind.ORGANIZATION)
        self.owner_b = Party.objects.create(name="Eier B", kind=Party.Kind.PERSON)
        self.user = get_user_model().objects.create_user(username="rights-reviewer")

    def claim(self, **overrides):
        values = {
            "recording": self.recording,
            "right_type": RightsClaim.RightType.OWNERSHIP,
            "rights_holder": self.owner_a,
            "territory_mode": RightsClaim.TerritoryMode.WORLD,
        }
        values.update(overrides)
        return create_rights_claim(**values)

    def test_one_or_many_owners_and_unknown_share_are_valid(self):
        first = self.claim(share=Decimal("50"))
        second = self.claim(rights_holder=self.owner_b, share=Decimal("50"))
        unknown = self.claim(rights_holder=self.owner_b, share=None)
        self.assertEqual(first.share, Decimal("50"))
        self.assertEqual(second.share, Decimal("50"))
        self.assertIsNone(unknown.share)

    def test_share_range_and_incomplete_dates_are_validated(self):
        self.claim(share=Decimal("0"), valid_from=None, valid_until=None)
        self.claim(share=Decimal("100"), valid_from=date(2020, 1, 1), valid_until=None)
        with self.assertRaises(ValidationError):
            self.claim(share=Decimal("100.01"))
        with self.assertRaises(ValidationError):
            self.claim(share=Decimal("-0.01"))
        with self.assertRaises(ValidationError):
            self.claim(valid_from=date(2025, 1, 1), valid_until=date(2024, 1, 1))

    def test_right_types_remain_independent(self):
        for right_type in RightsClaim.RightType.values:
            self.claim(right_type=right_type)
        self.assertEqual(
            set(RightsClaim.objects.values_list("right_type", flat=True)),
            set(RightsClaim.RightType.values),
        )
        self.assertEqual(
            RightsClaim.objects.filter(
                right_type=RightsClaim.RightType.OWNERSHIP
            ).count(),
            1,
        )

    def test_territory_modes_require_consistent_lists(self):
        norway = Territory.objects.get(code="NO")
        sweden = Territory.objects.get(code="SE")
        world = self.claim()
        included = self.claim(
            rights_holder=self.owner_b,
            territory_mode=RightsClaim.TerritoryMode.INCLUDE,
            territories=(norway, sweden),
        )
        excluded = self.claim(
            territory_mode=RightsClaim.TerritoryMode.EXCLUDE,
            territories=(sweden,),
        )
        self.assertEqual(world.territory_display, "Hele verden")
        self.assertIn("Norge", included.territory_display)
        self.assertIn("unntatt", excluded.territory_display)
        with self.assertRaises(ValidationError):
            self.claim(territories=(norway,))
        with self.assertRaises(ValidationError):
            self.claim(territory_mode=RightsClaim.TerritoryMode.INCLUDE)

    def test_imported_claim_starts_unverified_and_decisions_are_history(self):
        source_system = SourceSystem.objects.create(
            name="Historisk base", kind=SourceSystem.Kind.IMPORT
        )
        source = SourceRecord.objects.create(
            source_system=source_system, external_record_id="ROW-1"
        )
        claim = self.claim(source_record=source)
        self.assertEqual(claim.status, VerificationStatus.UNVERIFIED)
        decide_rights_claim(
            claim, VerificationStatus.CONFIRMED, user=self.user, note="Dokumentert"
        )
        claim.refresh_from_db()
        self.assertEqual(claim.status, VerificationStatus.CONFIRMED)
        self.assertEqual(claim.decisions.get().decided_by, self.user)
        with self.assertRaises(ValidationError):
            decision = claim.decisions.get()
            decision.note = "Overskriv"
            decision.save()

        claim.status = VerificationStatus.REJECTED
        with self.assertRaises(ValidationError):
            claim.save()

    def test_local_organization_uses_explicit_party_configuration(self):
        configuration = RightsConfiguration.objects.create(
            local_organization=self.owner_a
        )
        self.assertEqual(configuration.local_organization, self.owner_a)
        with self.assertRaises(ValidationError):
            RightsConfiguration.objects.create(local_organization=self.owner_b)

    def test_rejection_and_superseding_preserve_claims(self):
        rejected = self.claim()
        decide_rights_claim(rejected, VerificationStatus.REJECTED, user=self.user)
        previous = self.claim(share=Decimal("40"))
        replacement = supersede_rights_claim(
            previous,
            user=self.user,
            rights_holder=self.owner_a,
            share=Decimal("60"),
            territory_mode=RightsClaim.TerritoryMode.WORLD,
            note="Ny dokumentasjon",
        )
        rejected.refresh_from_db()
        previous.refresh_from_db()
        self.assertEqual(rejected.status, VerificationStatus.REJECTED)
        self.assertEqual(previous.status, VerificationStatus.SUPERSEDED)
        self.assertEqual(replacement.supersedes, previous)
        self.assertEqual(RightsClaim.objects.count(), 3)

    def test_confirmed_overlapping_exact_scope_cannot_exceed_100(self):
        first = self.claim(share=Decimal("60"))
        second = self.claim(rights_holder=self.owner_b, share=Decimal("50"))
        decide_rights_claim(first, VerificationStatus.CONFIRMED, user=self.user)
        with self.assertRaises(ValidationError):
            decide_rights_claim(second, VerificationStatus.CONFIRMED, user=self.user)
        second.refresh_from_db()
        self.assertEqual(second.status, VerificationStatus.UNVERIFIED)
        self.assertFalse(second.decisions.exists())

        norway = Territory.objects.get(code="NO")
        sweden = Territory.objects.get(code="SE")
        norwegian_claim = self.claim(
            rights_holder=self.owner_b,
            share=Decimal("80"),
            territory_mode=RightsClaim.TerritoryMode.INCLUDE,
            territories=(norway,),
        )
        swedish_claim = self.claim(
            share=Decimal("80"),
            territory_mode=RightsClaim.TerritoryMode.INCLUDE,
            territories=(sweden,),
        )
        decide_rights_claim(
            norwegian_claim, VerificationStatus.CONFIRMED, user=self.user
        )
        decide_rights_claim(
            swedish_claim, VerificationStatus.CONFIRMED, user=self.user
        )

    def test_managed_recording_does_not_create_ownership(self):
        managed = create_managed_recording(recording=self.recording)
        FileAsset.objects.create(
            filename="master.wav", role=FileAsset.Role.EDITED_WAV_MASTER
        )
        self.assertEqual(managed.recording, self.recording)
        self.assertFalse(RightsClaim.objects.exists())
        claim = self.claim()
        decide_rights_claim(claim, VerificationStatus.CONFIRMED, user=self.user)
        self.assertTrue(
            managed.recording.rights_claims.filter(
                status=VerificationStatus.CONFIRMED
            ).exists()
        )

    def test_registered_claim_cannot_be_silently_rewritten(self):
        claim = self.claim(share=Decimal("50"))
        claim.share = Decimal("75")
        with self.assertRaises(ValidationError):
            claim.save()

    def test_evidence_strength_is_separate_and_immutable(self):
        claim = self.claim(evidence_strength=RightsClaim.EvidenceStrength.STRONG)
        self.assertEqual(claim.status, VerificationStatus.UNVERIFIED)
        self.assertEqual(
            claim.evidence_strength, RightsClaim.EvidenceStrength.STRONG
        )
        claim.evidence_strength = RightsClaim.EvidenceStrength.DOCUMENTED
        with self.assertRaises(ValidationError):
            claim.save()

    def test_ownership_classification_is_conservative(self):
        self.assertEqual(
            classify_ownership([], self.owner_a).category,
            OwnershipCategory.UNRESOLVED,
        )
        unverified = self.claim(rights_holder=self.owner_b, share=Decimal("100"))
        self.assertEqual(
            classify_ownership([unverified], self.owner_a).category,
            OwnershipCategory.UNRESOLVED,
        )
        decide_rights_claim(
            unverified, VerificationStatus.CONFIRMED, user=self.user
        )
        unverified.refresh_from_db()
        self.assertEqual(
            classify_ownership([unverified], self.owner_a).category,
            OwnershipCategory.NOT_OWNED,
        )

    def test_local_full_partial_and_unknown_share_are_classified(self):
        full = self.claim(share=Decimal("100"))
        decide_rights_claim(full, VerificationStatus.CONFIRMED, user=self.user)
        full.refresh_from_db()
        self.assertEqual(
            classify_ownership([full], self.owner_a).category,
            OwnershipCategory.FULL,
        )

        partial_recording = Recording.objects.create(title="Deleid")
        partial = self.claim(recording=partial_recording, share=Decimal("40"))
        decide_rights_claim(partial, VerificationStatus.CONFIRMED, user=self.user)
        partial.refresh_from_db()
        self.assertEqual(
            classify_ownership([partial], self.owner_a).category,
            OwnershipCategory.PARTIAL,
        )

        unknown_recording = Recording.objects.create(title="Ukjent andel")
        unknown = self.claim(recording=unknown_recording, share=None)
        decide_rights_claim(unknown, VerificationStatus.CONFIRMED, user=self.user)
        unknown.refresh_from_db()
        summary = classify_ownership([unknown], self.owner_a)
        self.assertEqual(summary.category, OwnershipCategory.PARTIAL)
        self.assertTrue(summary.has_unknown_local_share)

    def test_disputed_claim_wins_and_other_right_types_do_not_imply_ownership(self):
        disputed = self.claim(rights_holder=self.owner_b)
        decide_rights_claim(disputed, VerificationStatus.DISPUTED, user=self.user)
        disputed.refresh_from_db()
        self.assertEqual(
            classify_ownership([disputed], self.owner_a).category,
            OwnershipCategory.DISPUTED,
        )

        administration = self.claim(
            right_type=RightsClaim.RightType.ADMINISTRATION,
            rights_holder=self.owner_a,
        )
        decide_rights_claim(
            administration, VerificationStatus.CONFIRMED, user=self.user
        )
        administration.refresh_from_db()
        self.assertEqual(
            classify_ownership([administration], self.owner_a).category,
            OwnershipCategory.UNRESOLVED,
        )
        self.assertTrue(
            has_local_confirmed_right(
                [administration],
                self.owner_a,
                RightsClaim.RightType.ADMINISTRATION,
            )
        )

    def test_agreement_dates_are_validated(self):
        with self.assertRaises(ValidationError):
            Agreement.objects.create(
                title="Ugyldig avtale",
                agreement_type=Agreement.Type.LICENSE,
                effective_date=date(2025, 1, 1),
                expiry_date=date(2024, 1, 1),
            )

    def test_agreement_parties_document_claim_and_protect_history(self):
        agreement = Agreement.objects.create(
            title="Masterlisens", agreement_type=Agreement.Type.LICENSE
        )
        AgreementParty.objects.create(
            agreement=agreement, party=self.owner_a, role=AgreementParty.Role.LICENSOR
        )
        AgreementParty.objects.create(
            agreement=agreement, party=self.owner_b, role=AgreementParty.Role.LICENSEE
        )
        document = FileAsset.objects.create(
            filename="avtale.pdf",
            mime_type="application/pdf",
            role=FileAsset.Role.DOCUMENT,
        )
        AgreementDocument.objects.create(agreement=agreement, file_asset=document)
        claim = self.claim()
        link_claim_agreement(claim, agreement, user=self.user)
        claim.refresh_from_db()
        self.assertEqual(claim.agreement, agreement)
        self.assertEqual(
            claim.decisions.get().decision, RightsDecision.Decision.DOCUMENTED
        )
        with self.assertRaises(ProtectedError):
            agreement.delete()
