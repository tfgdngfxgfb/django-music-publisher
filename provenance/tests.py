from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from catalogue.models import Release
from rights_core.models import VerificationStatus

from provenance.models import MetadataAssertion, SourceRecord, SourceSystem
from provenance.services import decide_assertion, supersede_assertion


class ProvenanceTests(TestCase):
    def setUp(self):
        self.release = Release.objects.create(title="Historisk utgivelse")
        self.cover = SourceSystem.objects.create(
            name="LP-cover", kind=SourceSystem.Kind.PHYSICAL
        )
        self.orchard = SourceSystem.objects.create(
            name="Orchard", kind=SourceSystem.Kind.IMPORT
        )

    def assertion(self, source, raw_value, *, save=True):
        record = SourceRecord.objects.create(
            source_system=source, source_locator=source.name
        )
        assertion = MetadataAssertion(
            source_record=record,
            entity_type=MetadataAssertion.EntityType.RELEASE,
            entity_uuid=self.release.pk,
            field_name="release_year",
            raw_value=raw_value,
            normalized_value=int(raw_value),
        )
        if save:
            assertion.save()
        return assertion

    def test_assertion_can_be_confirmed_disputed_and_rejected_with_history(self):
        user = get_user_model().objects.create_user("reviewer")
        assertion = self.assertion(self.cover, "1978")
        decide_assertion(assertion, VerificationStatus.CONFIRMED, user=user)
        decide_assertion(assertion, VerificationStatus.DISPUTED, user=user)
        decide_assertion(assertion, VerificationStatus.REJECTED, user=user)
        assertion.refresh_from_db()
        self.assertEqual(assertion.status, VerificationStatus.REJECTED)
        self.assertEqual(
            list(assertion.decisions.values_list("decision", flat=True)),
            [
                VerificationStatus.REJECTED,
                VerificationStatus.DISPUTED,
                VerificationStatus.CONFIRMED,
            ],
        )

    def test_superseding_preserves_both_original_source_values(self):
        old = self.assertion(self.orchard, "1979")
        replacement = self.assertion(self.cover, "1978", save=False)
        supersede_assertion(old, replacement)
        old.refresh_from_db()
        replacement.refresh_from_db()
        self.assertEqual(old.raw_value, "1979")
        self.assertEqual(old.status, VerificationStatus.SUPERSEDED)
        self.assertEqual(replacement.raw_value, "1978")
        self.assertEqual(replacement.supersedes, old)

    def test_original_source_record_and_assertion_cannot_be_overwritten(self):
        assertion = self.assertion(self.cover, "1978")
        assertion.raw_value = "1979"
        with self.assertRaises(ValidationError):
            assertion.save()
        source_record = assertion.source_record
        source_record.raw_payload = {"year": "changed"}
        with self.assertRaises(ValidationError):
            source_record.save()
        assertion.refresh_from_db()
        source_record.refresh_from_db()
        self.assertEqual(assertion.raw_value, "1978")
        self.assertEqual(source_record.raw_payload, {})

    def test_admin_decision_action_records_reviewer(self):
        user = get_user_model().objects.create_superuser("admin", password="test")
        self.client.force_login(user)
        assertion = self.assertion(self.cover, "1978")
        response = self.client.post(
            reverse("admin:provenance_metadataassertion_changelist"),
            {
                "action": "confirm_assertions",
                "_selected_action": str(assertion.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        assertion.refresh_from_db()
        self.assertEqual(assertion.status, VerificationStatus.CONFIRMED)
        self.assertEqual(assertion.decisions.get().decided_by, user)
