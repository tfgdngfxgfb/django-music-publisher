"""Native Rights workspace: domain semantics, authorization and query scaling."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Recording, Release, ReleaseTrack
from managed_music.models import ManagedRecording, ManagedRelease
from managed_music.services import return_to_music_library
from music_library.models import MusicLibraryEntry
from parties.models import Party
from provenance.models import SourceRecord, SourceSystem
from rights import services, workflows
from rights.models import (
    Agreement,
    AgreementDocument,
    RightsClaim,
    RightsDecision,
    RightsConfiguration,
    Territory,
)
from rights_core.models import VerificationStatus as S
from media_assets.models import FileAsset
from gui_v2.recording_rights import build_recording_rights


@override_settings(GUI_V2_WRITES_ENABLED=True)
class RecordingRightsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("rights-gui")
        self.client.force_login(self.user)
        self.recording = Recording.objects.create(
            title="Rettighetsinnspilling"
        )
        self.local = Party.objects.create(
            name="P7", kind=Party.Kind.ORGANIZATION
        )
        self.other = Party.objects.create(
            name="Annen eier", kind=Party.Kind.ORGANIZATION
        )
        RightsConfiguration.objects.create(local_organization=self.local)
        self.today = timezone.localdate()

    def url(self, name="rights", claim=None):
        args = [self.recording.pk]
        if claim:
            args.append(claim.pk)
        return reverse("gui_v2:recording_" + name, args=args)

    def onboard(self):
        return workflows.onboard_managed_recording(
            user=self.user,
            recording=self.recording,
            relationship_type=RightsClaim.RightType.ADMINISTRATION,
        )

    def claim(self, status=S.UNVERIFIED, **values):
        claim = services.create_rights_claim(
            recording=self.recording,
            **{
                "rights_holder": self.local,
                "right_type": RightsClaim.RightType.ADMINISTRATION,
                **values,
            },
        )
        if status != S.UNVERIFIED:
            services.decide_rights_claim(claim, status, user=self.user)
            claim.refresh_from_db()
        return claim

    def payload(self, **values):
        return {
            "right_type": "master_administration",
            "rights_holder": str(self.local.pk),
            "territory_mode": "world",
            "evidence_strength": "not_assessed",
            **values,
        }

    def split_payload(self, count=1, **values):
        result = {
            "positions-TOTAL_FORMS": str(count),
            "positions-INITIAL_FORMS": "0",
            "positions-MIN_NUM_FORMS": "1",
            "positions-MAX_NUM_FORMS": "20",
            "confirm_replace": "yes",
        }
        for i in range(count):
            result.update(
                {
                    f"positions-{i}-{k}": v
                    for k, v in self.payload(**values).items()
                }
            )
        return result

    def test_navigation_return_shell_and_read_only_get(self):
        managed = self.onboard()
        claim = self.claim(
            S.CONFIRMED, valid_until=self.today - timedelta(days=1)
        )
        # Simulate a materialized status waiting for scheduled reconciliation.
        managed.refresh_from_db()
        managed.status = ManagedRecording.Status.ACTIVE
        managed.save(update_fields=["status"])
        return_url = reverse("gui_v2:music_library") + "?genre=Pop&per_page=50"
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url(), {"return": return_url})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            any(
                q["sql"]
                .lstrip()
                .upper()
                .startswith(("INSERT", "UPDATE", "DELETE"))
                for q in queries
            )
        )
        self.assertContains(response, "Historisk/inaktiv forvaltning")
        self.assertContains(response, "Status venter på systemoppdatering")
        self.assertEqual(response.context["return_url"], return_url)
        self.assertIn(self.url(), response.context["tab_urls"]["rights"])
        for url in (
            self.url("rights_claim", claim),
            self.url("rights_document", claim),
            self.url("rights_replace", claim),
            self.url("rights_register"),
        ):
            page = self.client.get(url, {"return": return_url})
            self.assertContains(page, 'class="player global-player"')
            self.assertContains(page, 'class="recording-object-header"')
            self.assertEqual(page.context["return_url"], return_url)
        managed.refresh_from_db()
        self.assertEqual(managed.status, ManagedRecording.Status.ACTIVE)
        overview = self.client.get(self.url("detail"))
        self.assertIn(self.url(), overview.context["tab_urls"]["rights"])

    def test_empty_without_membership_and_release_only(self):
        response = self.client.get(self.url())
        self.assertContains(response, "Ikke i Forvaltet musikk")
        self.assertNotContains(response, "Registrer rettighetsposisjon</a>")
        release = Release.objects.create(title="Forvaltet katalog")
        ManagedRelease.objects.create(
            release=release,
            relationship=ManagedRelease.Relationship.MANAGED_CATALOGUE,
        )
        ReleaseTrack.objects.create(
            release=release, recording=self.recording, sequence_number=1
        )
        self.assertContains(
            self.client.get(self.url()), "Kun via forvaltet utgivelse"
        )
        self.assertFalse(ManagedRecording.objects.exists())

    def test_management_states(self):
        managed = self.onboard()
        self.assertContains(self.client.get(self.url()), "Under vurdering")
        claim = self.recording.rights_claims.get()
        workflows.decide_rights_claim(claim, S.CONFIRMED, user=self.user)
        self.assertEqual(
            build_recording_rights(self.recording)["management_label"], "Aktiv"
        )
        workflows.decide_rights_claim(claim, S.DISPUTED, user=self.user)
        self.assertEqual(
            build_recording_rights(self.recording)["management_label"],
            "Historisk/inaktiv forvaltning",
        )
        # Legacy membership with no evidence has a separate uncertainty signal.
        other = Recording.objects.create(title="Legacy")
        entry = MusicLibraryEntry.objects.create(recording=other)
        ManagedRecording.objects.create(library_entry=entry, status="active")
        self.assertEqual(
            build_recording_rights(other)["management_label"],
            "Historikk må avklares",
        )

    def test_ownership_categories_unknown_and_territorial_shares(self):
        cases = [
            (self.local, 100, S.CONFIRMED, "full"),
            (self.local, 40, S.CONFIRMED, "partial"),
            (self.other, 100, S.CONFIRMED, "not_owned"),
            (self.local, None, S.CONFIRMED, "unresolved"),
            (self.local, 100, S.DISPUTED, "disputed"),
        ]
        for holder, share, status, expected in cases:
            self.recording = Recording.objects.create(title=expected)
            self.claim(
                status,
                right_type="master_ownership",
                rights_holder=holder,
                share=share,
            )
            result = build_recording_rights(self.recording)
            self.assertEqual(result["ownership"].category, expected)
            if share is None:
                self.assertIsNone(result["ownership"].local_share)
                self.assertNotContains(self.client.get(self.url()), "P7: 0")
        self.recording = Recording.objects.create(
            title="Territorielle andeler"
        )
        self.claim(
            S.CONFIRMED,
            right_type="master_ownership",
            share=40,
            territory_mode="include",
            territories=[Territory.objects.get(code="NO")],
        )
        self.claim(
            S.CONFIRMED,
            right_type="master_ownership",
            share=60,
            territory_mode="exclude",
            territories=[Territory.objects.get(code="NO")],
        )
        self.assertIsNone(
            build_recording_rights(self.recording)["ownership"].local_share
        )
        self.assertContains(
            self.client.get(self.url()), "Andelen varierer etter territorium"
        )

    def test_grouping_and_scoped_basis(self):
        release = Release.objects.create(title="Kun denne utgivelsen")
        ReleaseTrack.objects.create(
            release=release, recording=self.recording, sequence_number=1
        )
        for status in (S.UNVERIFIED, S.DISPUTED):
            self.claim(status, valid_until=self.today - timedelta(days=1))
        self.claim(S.CONFIRMED)
        self.claim(S.CONFIRMED, release_scope=release)
        self.claim(S.CONFIRMED, right_type="distribution")
        future = self.claim(
            S.CONFIRMED, valid_from=self.today + timedelta(days=1)
        )
        expired = self.claim(
            S.CONFIRMED, valid_until=self.today - timedelta(days=1)
        )
        rejected = self.claim(S.REJECTED)
        previous = self.claim()
        services.supersede_rights_claim(
            previous, user=self.user, rights_holder=self.local
        )
        result = build_recording_rights(self.recording)
        groups = {g["key"]: g["rows"] for g in result["claim_groups"]}
        self.assertEqual(len(groups["attention"]), 3)
        self.assertEqual(len(groups["confirmed"]), 4)
        self.assertEqual(len(groups["history"]), 3)
        self.assertIn(
            "kommende",
            next(
                r["status"]
                for r in groups["confirmed"]
                if r["claim"] == future
            ),
        )
        self.assertIn(
            "utløpt",
            next(
                r["status"] for r in groups["history"] if r["claim"] == expired
            ),
        )
        self.assertEqual(
            [s["confirmed"] for s in result["basis_summaries"]], [2, 1]
        )
        self.assertContains(
            self.client.get(self.url()), "Kun denne utgivelsen"
        )

    def test_detail_documentation_history_split_and_return(self):
        managed = self.onboard()
        original = self.recording.rights_claims.get()
        workflows.decide_rights_claim(original, S.REJECTED, user=self.user)
        return_to_music_library(
            managed, user=self.user, reason="Avvist onboarding"
        )
        agreement = Agreement.objects.create(
            title="Dokumentert avtale", agreement_type="other"
        )
        source = SourceRecord.objects.create(
            source_system=SourceSystem.objects.create(
                name="Original import", kind="manual"
            ),
            raw_payload={},
        )
        asset = FileAsset.objects.create(
            filename="avtale.pdf", role=FileAsset.Role.DOCUMENT
        )
        AgreementDocument.objects.create(
            agreement=agreement, file_asset=asset, description="Underlag"
        )
        claim = services.supersede_rights_claim(
            original,
            user=self.user,
            rights_holder=self.local,
            grantor=self.other,
            agreement=agreement,
            source_record=source,
            valid_until=self.today - timedelta(days=1),
        )
        children = workflows.supersede_rights_claims(
            claim,
            user=self.user,
            replacements=[
                {
                    "rights_holder": self.local,
                    "valid_until": self.today - timedelta(days=2),
                },
                {"rights_holder": self.other},
            ],
        )
        response = self.client.get(self.url("rights_claim", claim))
        for text in (
            "Dokumentert avtale",
            "Original import",
            "avtale.pdf",
            "Underlag",
            self.user.username,
            str(original.pk),
            *(str(c.pk) for c in children),
        ):
            self.assertContains(response, text)
        self.assertNotContains(response, "Ingen konflikt")
        self.assertFalse(ManagedRecording.objects.exists())
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_registration_uses_workflow_and_starts_unverified(self):
        response = self.client.post(
            self.url("rights_register"), self.payload()
        )
        self.assertContains(response, "eksplisitt onboarding")
        self.assertFalse(RightsClaim.objects.exists())
        self.onboard()
        with patch(
            "rights.workflows.register_rights_claim",
            wraps=workflows.register_rights_claim,
        ) as call:
            response = self.client.post(
                self.url("rights_register"), self.payload(status="confirmed")
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(call.call_count, 1)
        self.assertTrue(
            all(c.status == S.UNVERIFIED for c in RightsClaim.objects.all())
        )

    def test_decisions_follow_matrix_and_document_preserves_legal_fields(self):
        self.onboard()
        claim = self.recording.rights_claims.get()
        response = self.client.post(
            self.url("rights_decide", claim),
            {"action": "confirm", "note": "Vurdert"},
        )
        self.assertEqual(response.status_code, 302)
        page = self.client.get(self.url("rights_claim", claim))
        self.assertNotContains(page, ">Bekreft</a>")
        self.assertContains(page, ">Bestrid</a>")
        agreement = Agreement.objects.create(
            title="Senere dokumentasjon", agreement_type="other"
        )
        self.client.post(
            self.url("rights_document", claim),
            {
                "agreement": str(agreement.pk),
                "evidence_strength": "strong",
                "note": "Nytt grunnlag",
                "rights_holder": str(self.other.pk),
                "status": "rejected",
            },
        )
        claim.refresh_from_db()
        self.assertEqual(claim.status, S.CONFIRMED)
        self.assertEqual(claim.rights_holder_id, self.local.pk)
        self.assertEqual(claim.agreement, agreement)
        self.assertIsNone(claim.source_record_id)
        self.assertEqual(claim.decisions.first().decision, "documented")

    def test_split_confirmation_atomic_errors_and_locked_type(self):
        self.onboard()
        claim = self.recording.rights_claims.get()
        data = self.split_payload(2, right_type="distribution")
        data.pop("confirm_replace")
        response = self.client.post(self.url("rights_replace", claim), data)
        self.assertContains(response, "Bekreft at det opprinnelige")
        self.assertFalse(claim.superseded_by.exists())
        data["confirm_replace"] = "yes"
        data["positions-1-valid_until"] = "bad-date"
        response = self.client.post(self.url("rights_replace", claim), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(claim.superseded_by.exists())
        data.pop("positions-1-valid_until")
        response = self.client.post(self.url("rights_replace", claim), data)
        self.assertEqual(response.status_code, 302)
        claim.refresh_from_db()
        self.assertEqual(claim.status, S.SUPERSEDED)
        self.assertEqual(claim.superseded_by.count(), 2)
        self.assertTrue(
            all(
                c.status == S.UNVERIFIED and c.right_type == claim.right_type
                for c in claim.superseded_by.all()
            )
        )
        self.assertNotContains(
            self.client.get(self.url("rights_claim", claim)), ">Bekreft</a>"
        )

    def test_historical_correction_allowed_but_new_basis_blocked(self):
        managed = self.onboard()
        claim = self.recording.rights_claims.get()
        workflows.decide_rights_claim(claim, S.REJECTED, user=self.user)
        return_to_music_library(managed, user=self.user, reason="Avvist")
        for values in (
            {},
            {"valid_from": (self.today + timedelta(days=5)).isoformat()},
        ):
            response = self.client.post(
                self.url("rights_replace", claim), self.split_payload(**values)
            )
            self.assertContains(response, "eksplisitt onboarding")
            self.assertFalse(claim.superseded_by.exists())
        response = self.client.post(
            self.url("rights_decide", claim), {"action": "confirm"}
        )
        self.assertContains(response, "eksplisitt onboarding")
        response = self.client.post(
            self.url("rights_replace", claim),
            self.split_payload(
                valid_until=(self.today - timedelta(days=1)).isoformat()
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ManagedRecording.objects.exists())

    def test_permissions_write_flag_and_cross_recording_claim(self):
        self.onboard()
        claim = self.recording.rights_claims.get()
        reader = get_user_model().objects.create_user("reader")
        reader.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="catalogue", codename="view_recording"
            )
        )
        self.client.force_login(reader)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        reader.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="rights", codename="view_rightsclaim"
            )
        )
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        for name in ("document", "decide", "replace"):
            self.assertEqual(
                self.client.post(
                    self.url("rights_" + name, claim), {}
                ).status_code,
                403,
            )
        self.assertEqual(
            self.client.post(self.url("rights_register"), {}).status_code, 403
        )
        self.client.force_login(self.user)
        with override_settings(GUI_V2_WRITES_ENABLED=False):
            for name in ("document", "decide", "replace"):
                self.assertEqual(
                    self.client.post(
                        self.url("rights_" + name, claim), {}
                    ).status_code,
                    403,
                )
        self.recording = Recording.objects.create(title="Other recording")
        self.assertEqual(
            self.client.get(self.url("rights_claim", claim)).status_code, 404
        )

    def test_overview_query_scaling(self):
        self.onboard()
        with CaptureQueriesContext(connection) as first:
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        for i in range(35):
            self.claim(
                S.CONFIRMED,
                territory_mode="include",
                territories=[Territory.objects.get(code="NO")],
            )
        with CaptureQueriesContext(connection) as many:
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(many), len(first) + 1)

    def test_full_scope_registration_and_hidden_documentation_permissions(
        self,
    ):
        self.onboard()
        release = Release.objects.create(title="Scoped release")
        ReleaseTrack.objects.create(
            release=release,
            recording=self.recording,
            sequence_number=1,
            track_number=1,
        )
        source = SourceRecord.objects.create(
            source_system=SourceSystem.objects.create(
                name="Private source", kind="manual"
            ),
            raw_payload={},
            source_locator="original-reference",
        )
        agreement = Agreement.objects.create(
            title="Private agreement", agreement_type="other"
        )
        response = self.client.post(
            self.url("rights_register"),
            self.payload(
                release_scope=str(release.pk),
                grantor=str(self.other.pk),
                territory_mode="include",
                territories=[Territory.objects.get(code="NO").pk],
                valid_from=self.today.isoformat(),
                valid_until=(self.today + timedelta(days=30)).isoformat(),
                agreement=str(agreement.pk),
                source_record=str(source.pk),
                notes="Scope detail",
            ),
        )
        self.assertEqual(response.status_code, 302)
        claim = RightsClaim.objects.get(notes="Scope detail")
        self.assertEqual(claim.status, S.UNVERIFIED)
        self.assertEqual(claim.release_scope, release)
        self.assertEqual(claim.grantor, self.other)
        self.assertEqual(
            list(claim.territories.values_list("code", flat=True)), ["NO"]
        )
        self.assertEqual(claim.source_record, source)
        editor = get_user_model().objects.create_user("limited-editor")
        for app, code in (
            ("catalogue", "view_recording"),
            ("rights", "view_rightsclaim"),
            ("rights", "change_rightsclaim"),
        ):
            editor.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label=app, codename=code
                )
            )
        self.client.force_login(editor)
        response = self.client.get(self.url("rights_claim", claim))
        self.assertNotContains(response, "Private agreement")
        self.assertNotContains(response, "original-reference")
        response = self.client.post(
            self.url("rights_document", claim),
            {
                "agreement": "",
                "evidence_strength": "documented",
                "note": "Extra evidence",
            },
        )
        self.assertEqual(response.status_code, 302)
        claim.refresh_from_db()
        self.assertEqual(claim.agreement, agreement)
        self.assertEqual(claim.source_record, source)

    def test_direct_unavailable_action_gets_redirect_without_writes(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url("rights_register"))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            any(
                q["sql"]
                .lstrip()
                .upper()
                .startswith(("INSERT", "UPDATE", "DELETE"))
                for q in queries
            )
        )
        self.onboard()
        claim = self.recording.rights_claims.get()
        workflows.supersede_rights_claims(
            claim,
            user=self.user,
            replacements=[
                {"rights_holder": self.local, "right_type": claim.right_type}
            ],
        )
        for action in ("decide", "replace"):
            self.assertEqual(
                self.client.get(
                    self.url("rights_" + action, claim)
                ).status_code,
                302,
            )
