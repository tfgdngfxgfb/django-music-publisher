"""6G native management workflows and read-only derived list contract."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Recording, Release, ReleaseTrack
from managed_music.models import ManagedRecording, ManagedRelease
from music_library.models import MusicLibraryEntry
from parties.models import Party
from provenance.models import MetadataAssertion, SourceRecord, SourceSystem
from rights import workflows, services
from rights.models import (
    RightsClaim,
    RightsDecision,
    RightsConfiguration,
    Territory,
)
from rights_core.models import VerificationStatus as S
from gui_v2.managed_music import filtered_rows, memberships, present_batch


@override_settings(GUI_V2_WRITES_ENABLED=True)
class ManagedMusicTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("management-gui")
        self.client.force_login(self.user)
        self.local = Party.objects.create(
            name="P7", kind=Party.Kind.ORGANIZATION
        )
        self.other = Party.objects.create(
            name="Ekstern", kind=Party.Kind.ORGANIZATION
        )
        RightsConfiguration.objects.create(local_organization=self.local)
        self.today = timezone.localdate()
        self.recording = Recording.objects.create(title="En innspilling")
        MusicLibraryEntry.objects.create(recording=self.recording)

    def url(self, action="music", managed=None):
        return reverse(
            "gui_v2:managed_" + action, args=[managed.pk] if managed else []
        )

    def member(self, recording=None, status="pending"):
        entry, _ = MusicLibraryEntry.objects.get_or_create(
            recording=recording or self.recording
        )
        return ManagedRecording.objects.create(
            library_entry=entry, status=status
        )

    def claim(self, status=S.UNVERIFIED, recording=None, **values):
        claim = services.create_rights_claim(
            recording=recording or self.recording,
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
            "recording": str(self.recording.pk),
            "relationship_type": "master_administration",
            "territory_mode": "world",
            "evidence_strength": "not_assessed",
            **values,
        }

    def rows(self, **filters):
        return list(filtered_rows(filters, can_view_rights=True))

    def test_empty_native_shell_and_home_navigation(self):
        response = self.client.get(self.url())
        self.assertContains(response, "Ingen forvaltede innspillinger")
        self.assertContains(
            response, 'aria-current="page" href="' + self.url()
        )
        self.assertContains(response, "data-p7-player-footer")
        self.assertContains(
            self.client.get(reverse("gui_v2:home")), self.url()
        )

    def test_only_actual_memberships_and_no_get_writes(self):
        managed = self.member()
        self.claim()
        release = Release.objects.create(title="Forvaltet album")
        ManagedRelease.objects.create(
            release=release, relationship="managed_catalogue"
        )
        ordinary = Recording.objects.create(title="Kun på album")
        ReleaseTrack.objects.create(
            release=release,
            recording=ordinary,
            track_number=1,
            sequence_number=1,
        )
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, ordinary.title)
        self.assertNotContains(response, 'name="selected" type="checkbox"')
        self.assertFalse(
            any(
                q["sql"]
                .lstrip()
                .upper()
                .startswith(("INSERT", "UPDATE", "DELETE"))
                for q in captured
            )
        )
        managed.refresh_from_db()
        self.assertEqual(managed.status, "pending")

    def test_effective_status_and_default_visibility_before_pagination(self):
        managed = self.member()
        self.claim(S.CONFIRMED, valid_until=self.today - timedelta(days=1))
        managed.refresh_from_db()
        managed.status = "active"
        managed.save(update_fields=["status"])
        rows = self.rows(status="inactive")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["state"].needs_reconciliation)
        self.assertEqual(self.rows(status="active"), [])
        # Mismatched history needs follow-up and is included by default.
        self.assertEqual(len(self.rows()), 1)
        managed.status = "inactive"
        managed.save(update_fields=["status"])
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(self.rows(status="all")), 1)

    def test_uncertain_inactive_is_findable_by_default(self):
        self.member(status="inactive")
        row = self.rows()[0]
        self.assertEqual(row["status"], "uncertain")
        self.assertEqual(
            len(self.rows(status="uncertain", follow_up="yes")), 1
        )

    def test_pending_without_basis_and_disputed_follow_up(self):
        self.member()
        self.assertIn(
            "Mangler plausibelt P7-grunnlag", self.rows()[0]["follow_up"]
        )
        claim = self.claim()
        self.assertEqual(self.rows()[0]["follow_up"], [])
        services.decide_rights_claim(claim, S.DISPUTED, user=self.user)
        self.assertIn("Bestridt P7-grunnlag", self.rows()[0]["follow_up"])

    def test_source_and_search_filters(self):
        managed = self.member()
        source = SourceSystem.objects.create(
            name="Manuell kilde", kind=SourceSystem.Kind.MANUAL
        )
        managed.source_system = source
        managed.save()
        self.assertEqual(len(self.rows(q="innspilling", source=source)), 1)
        self.assertEqual(self.rows(q="ukjent"), [])

    def test_release_scoped_basis_is_counted_and_labelled(self):
        self.member()
        release = Release.objects.create(title="Avgrenset album")
        ReleaseTrack.objects.create(
            release=release,
            recording=self.recording,
            track_number=1,
            sequence_number=1,
        )
        self.claim(S.CONFIRMED, release_scope=release)
        response = self.client.get(self.url(), {"administration": "confirmed"})
        self.assertContains(response, "1 release-avgrenset")
        self.assertContains(response, "ingen bruksautorisasjon")
        self.assertEqual(response.context["selected"]["status"], "active")

    def test_ownership_categories_and_unknown_share(self):
        self.member()
        self.claim(S.CONFIRMED, right_type="master_ownership", share=None)
        response = self.client.get(self.url(), {"ownership": "unresolved"})
        self.assertEqual(
            response.context["selected"]["ownership"].category, "unresolved"
        )
        self.assertIsNone(
            response.context["selected"]["ownership"].local_share
        )
        for name, holder, share, expected in (
            ("Full", self.local, 100, "full"),
            ("Partial", self.local, 50, "partial"),
            ("Other", self.other, 100, "not_owned"),
        ):
            recording = Recording.objects.create(title=name)
            self.member(recording)
            self.claim(
                S.CONFIRMED,
                recording=recording,
                rights_holder=holder,
                right_type="master_ownership",
                share=share,
            )
            self.assertEqual(len(self.rows(ownership=expected)), 1)
        disputed = Recording.objects.create(title="Disputed")
        self.member(disputed)
        self.claim(
            S.DISPUTED,
            recording=disputed,
            right_type="master_ownership",
            share=100,
        )
        self.assertEqual(len(self.rows(ownership="disputed")), 1)

    def test_territorial_shares_are_not_summed(self):
        self.member()
        no, _ = Territory.objects.get_or_create(
            code="NO", defaults={"name_nb": "Norge"}
        )
        se, _ = Territory.objects.get_or_create(
            code="SE", defaults={"name_nb": "Sverige"}
        )
        for territory, share in ((no, 60), (se, 80)):
            self.claim(
                S.CONFIRMED,
                right_type="master_ownership",
                share=share,
                territory_mode="include",
                territories=[territory],
            )
        self.assertIsNone(self.rows()[0]["ownership"].local_share)

    def test_rights_columns_and_filters_require_permission(self):
        self.member()
        reader = get_user_model().objects.create_user("reader")
        reader.user_permissions.add(
            *Permission.objects.filter(
                codename__in=("view_managedrecording", "view_recording")
            )
        )
        self.client.force_login(reader)
        response = self.client.get(self.url(), {"ownership": "full"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="ownership"')
        self.assertNotContains(response, "Åpne Rettigheter")
        self.assertNotContains(response, "Mastereierskap</th>")
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertEqual(self.client.get(self.url("onboard")).status_code, 403)
        reader.user_permissions.clear()
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_inspector_links_preserve_return_context(self):
        self.member()
        response = self.client.get(self.url(), {"q": "En"})
        row = response.context["selected"]
        self.assertIn("return=", row["rights_url"])
        self.assertIn("q%3DEn", row["recording_url"])
        self.assertIn("/rettigheter/", row["rights_url"])

    def test_onboarding_existing_scope_and_provenance(self):
        release = Release.objects.create(title="Album")
        ReleaseTrack.objects.create(
            release=release,
            recording=self.recording,
            track_number=1,
            sequence_number=1,
        )
        source = SourceSystem.objects.create(
            name="Kilde", kind=SourceSystem.Kind.MANUAL
        )
        source_record = SourceRecord.objects.create(source_system=source)
        no, _ = Territory.objects.get_or_create(
            code="NO", defaults={"name_nb": "Norge"}
        )
        response = self.client.post(
            self.url("onboard"),
            self.payload(
                territory_mode="include",
                territories=[str(no.pk)],
                grantor=str(self.other.pk),
                valid_from=str(self.today + timedelta(days=1)),
                release_scope=str(release.pk),
                source_system=str(source.pk),
                source_record=str(source_record.pk),
                notes="Grunnlag",
            ),
        )
        self.assertEqual(response.status_code, 302)
        managed = ManagedRecording.objects.get()
        claim = RightsClaim.objects.get()
        self.assertEqual(managed.status, "pending")
        self.assertEqual(claim.status, S.UNVERIFIED)
        self.assertEqual(claim.rights_holder, self.local)
        self.assertEqual(claim.grantor, self.other)
        self.assertEqual(claim.release_scope, release)
        self.assertEqual(claim.source_record, source_record)
        self.assertEqual(list(claim.territories.all()), [no])

    def test_gui_onboarding_requires_existing_library_entry(self):
        response = self.client.get(
            self.url("onboard"), {"recording": self.recording.pk}
        )
        for field in (
            "new_recording_title",
            "new_isrc",
            "artist_identity",
            "force_create",
        ):
            self.assertNotIn(field, response.context["form"].fields)
        count = Recording.objects.count()
        response = self.client.post(
            self.url("onboard"),
            self.payload(recording="", new_recording_title="Ny unik tittel"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ManagedRecording.objects.exists())
        self.assertEqual(Recording.objects.count(), count)
        outside = Recording.objects.create(title="Utenfor Musikkarkivet")
        response = self.client.post(
            self.url("onboard"),
            self.payload(recording=str(outside.pk)),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ManagedRecording.objects.exists())

    def test_catalogue_metadata_is_read_only_during_onboarding(self):
        response = self.client.post(
            self.url("onboard"),
            self.payload(
                title="Endret",
                new_recording_title="Oppdiktet",
                new_isrc="NOXXX2600001",
                force_create="on",
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.title, "En innspilling")
        self.assertEqual(Recording.objects.count(), 1)
        self.assertFalse(self.recording.identifiers.exists())

    def test_onboarding_share_release_validation_and_rollback(self):
        for values in (
            {"relationship_type": "master_ownership"},
            {"ownership_share": "10"},
            {"valid_from": "2026-10-10", "valid_until": "2020-01-01"},
        ):
            response = self.client.post(
                self.url("onboard"), self.payload(**values)
            )
            self.assertEqual(response.status_code, 200)
            self.assertFalse(ManagedRecording.objects.exists())
        with patch(
            "managed_music.services.create_rights_claim",
            side_effect=ValidationError("Test rollback"),
        ):
            response = self.client.post(
                self.url("onboard"),
                self.payload(),
            )
        self.assertContains(response, "Test rollback")
        self.assertFalse(Recording.objects.filter(title="Rollback").exists())
        self.assertFalse(ManagedRecording.objects.exists())
        self.assertEqual(MusicLibraryEntry.objects.count(), 1)

    def test_release_scope_choices_only_linked_and_ownership_scope_rejected(
        self,
    ):
        linked = Release.objects.create(title="Tilknyttet")
        other = Release.objects.create(title="Annen utgivelse")
        ReleaseTrack.objects.create(
            release=linked,
            recording=self.recording,
            track_number=1,
            sequence_number=1,
        )
        response = self.client.get(
            self.url("onboard"), {"recording": self.recording.pk}
        )
        self.assertEqual(
            list(response.context["form"].fields["release_scope"].queryset),
            [linked],
        )
        for scope, kind in (
            (other, "master_administration"),
            (linked, "master_ownership"),
        ):
            response = self.client.post(
                self.url("onboard"),
                self.payload(
                    release_scope=str(scope.pk),
                    relationship_type=kind,
                    ownership_share=(
                        "100" if kind == "master_ownership" else ""
                    ),
                ),
            )
            self.assertEqual(response.status_code, 200)
            self.assertFalse(ManagedRecording.objects.exists())

    def test_legacy_correction_and_return_are_separate_audited_actions(self):
        managed = self.member(status="inactive")
        self.claim()
        response = self.client.post(
            self.url("correct", managed),
            {"reason": "Feil import", "confirm": "on"},
        )
        self.assertEqual(response.status_code, 302)
        managed.refresh_from_db()
        self.assertEqual(managed.status, "pending")
        self.assertTrue(
            SourceRecord.objects.filter(
                raw_payload__action="correct_legacy_management_history"
            ).exists()
        )
        self.assertTrue(RightsClaim.objects.exists())
        # Unverified basis still blocks return after correction.
        response = self.client.post(
            self.url("return", managed), {"reason": "Tilbake", "confirm": "on"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ManagedRecording.objects.filter(pk=managed.pk).exists()
        )

    def test_correction_rejects_current_and_historical_management(self):
        managed = self.member()
        self.claim(S.CONFIRMED)
        response = self.client.post(
            self.url("correct", managed), {"reason": "Feil", "confirm": "on"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SourceRecord.objects.exists())
        self.assertFalse(response.context["eligible"])

    def test_explicit_return_preserves_history_and_requires_reonboarding(self):
        managed = self.member()
        claim = self.claim(S.REJECTED)
        decision_ids = set(RightsDecision.objects.values_list("pk", flat=True))
        response = self.client.post(
            self.url("return", managed),
            {"reason": "Avvist grunnlag", "confirm": "on"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ManagedRecording.objects.exists())
        self.assertTrue(
            MusicLibraryEntry.objects.filter(recording=self.recording).exists()
        )
        self.assertTrue(RightsClaim.objects.filter(pk=claim.pk).exists())
        self.assertEqual(
            set(RightsDecision.objects.values_list("pk", flat=True)),
            decision_ids,
        )
        self.assertTrue(SourceRecord.objects.exists())
        self.assertEqual(
            self.client.post(self.url("onboard"), self.payload()).status_code,
            302,
        )
        self.assertEqual(ManagedRecording.objects.get().status, "pending")

    def test_return_guards_current_future_disputed_unverified_history_uncertain(
        self,
    ):
        for status, dates in (
            (S.CONFIRMED, {}),
            (S.CONFIRMED, {"valid_from": self.today + timedelta(days=10)}),
            (S.CONFIRMED, {"valid_until": self.today - timedelta(days=10)}),
            (S.DISPUTED, {}),
            (S.UNVERIFIED, {}),
        ):
            recording = Recording.objects.create(title=str((status, dates)))
            managed = self.member(recording)
            self.claim(status, recording=recording, **dates)
            response = self.client.post(
                self.url("return", managed),
                {"reason": "Test", "confirm": "on"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(
                ManagedRecording.objects.filter(pk=managed.pk).exists()
            )
        uncertain = self.member(status="inactive")
        self.assertEqual(
            self.client.post(
                self.url("return", uncertain),
                {"reason": "Test", "confirm": "on"},
            ).status_code,
            200,
        )
        self.assertTrue(
            ManagedRecording.objects.filter(pk=uncertain.pk).exists()
        )

    def test_action_gets_are_read_only_and_include_player(self):
        managed = self.member(status="active")
        for url in (
            self.url("correct", managed),
            self.url("return", managed),
            self.url("onboard"),
        ):
            with CaptureQueriesContext(connection) as captured:
                response = self.client.get(url)
            self.assertContains(response, "data-p7-player-footer")
            self.assertFalse(
                any(
                    q["sql"]
                    .lstrip()
                    .upper()
                    .startswith(("INSERT", "UPDATE", "DELETE"))
                    for q in captured
                )
            )

    def test_reasons_confirmation_permissions_and_write_flag(self):
        managed = self.member()
        for values in (
            {"reason": "   ", "confirm": "on"},
            {"reason": "Årsak"},
        ):
            self.assertEqual(
                self.client.post(
                    self.url("return", managed), values
                ).status_code,
                200,
            )
            self.assertTrue(ManagedRecording.objects.exists())
        with override_settings(GUI_V2_WRITES_ENABLED=False):
            for url in (
                self.url("return", managed),
                self.url("correct", managed),
                self.url("onboard"),
            ):
                self.assertEqual(
                    self.client.post(
                        url, {"reason": "Test", "confirm": "on"}
                    ).status_code,
                    403,
                )
        reader = get_user_model().objects.create_user("no-action")
        reader.user_permissions.add(
            *Permission.objects.filter(
                codename__in=("view_managedrecording", "view_recording")
            )
        )
        self.client.force_login(reader)
        for url in (
            self.url("return", managed),
            self.url("correct", managed),
            self.url("onboard"),
        ):
            self.assertEqual(
                self.client.post(
                    url, {"reason": "Test", "confirm": "on"}
                ).status_code,
                403,
            )

    def test_stale_return_revalidated_by_service(self):
        managed = self.member()
        self.assertTrue(
            self.client.get(self.url("return", managed)).context["eligible"]
        )
        self.claim()
        self.assertEqual(
            self.client.post(
                self.url("return", managed),
                {"reason": "Test", "confirm": "on"},
            ).status_code,
            200,
        )
        self.assertTrue(ManagedRecording.objects.exists())

    def test_query_scaling_one_vs_fifty_with_claims_history_and_territories(
        self,
    ):
        self.member()
        self.claim(S.REJECTED)

        def query_count():
            with CaptureQueriesContext(connection) as captured:
                rows = present_batch(
                    list(memberships({})), can_view_rights=True
                )
                for row in rows:
                    str(row["ownership"].local_share)
            return len(captured)

        initial = query_count()
        no, _ = Territory.objects.get_or_create(
            code="NO", defaults={"name_nb": "Norge"}
        )
        for i in range(49):
            recording = Recording.objects.create(title=f"Skalering {i}")
            self.member(recording)
            self.claim(
                S.REJECTED,
                recording=recording,
                territory_mode="include",
                territories=[no],
            )
        self.assertEqual(query_count(), initial)
        response = self.client.get(self.url(), {"page": "2"})
        self.assertEqual(response.context["page"].paginator.count, 50)
        self.assertEqual(len(response.context["page"].object_list), 25)

    def test_missing_configuration_and_invalid_inputs_are_controlled(self):
        self.member()
        RightsConfiguration.objects.all().delete()
        self.assertContains(self.client.get(self.url()), "Kan ikke vurderes")
        self.assertEqual(
            self.client.get(
                self.url("onboard"), {"recording": "invalid"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                self.url(), {"page": "invalid", "status": "invalid"}
            ).status_code,
            200,
        )

    def catalogue_source(self, locator, *, recording_target=False):
        system, _ = SourceSystem.objects.get_or_create(
            name="Arkivimport", defaults={"kind": SourceSystem.Kind.IMPORT}
        )
        source = SourceRecord.objects.create(
            source_system=system,
            source_locator=locator,
            raw_payload={"original": locator},
        )
        MetadataAssertion.objects.create(
            source_record=source,
            entity_type=(
                "recording" if recording_target else "music_library_entry"
            ),
            entity_uuid=(
                self.recording.pk
                if recording_target
                else self.recording.music_library_entry.pk
            ),
            field_name="title" if recording_target else "genre",
            raw_value="Original verdi",
        )
        return source

    def test_onboarding_defaults_reuse_original_source_after_rescan(self):
        original = self.catalogue_source("Første import")
        self.catalogue_source("Senere rescan")
        before = list(
            SourceRecord.objects.values(
                "pk", "raw_payload", "revision", "updated_at"
            )
        )
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(
                self.url("onboard"), {"recording": self.recording.pk}
            )
        self.assertEqual(
            response.context["form"]["source_record"].value(), original.pk
        )
        self.assertEqual(
            response.context["form"]["source_system"].value(),
            original.source_system_id,
        )
        self.assertFalse(
            any(
                q["sql"]
                .lstrip()
                .upper()
                .startswith(("INSERT", "UPDATE", "DELETE"))
                for q in captured
            )
        )
        response = self.client.post(
            self.url("onboard"), self.payload(source_record="")
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RightsClaim.objects.get().source_record, original)
        self.assertEqual(
            ManagedRecording.objects.get().source_system,
            original.source_system,
        )
        self.assertEqual(
            list(
                SourceRecord.objects.values(
                    "pk", "raw_payload", "revision", "updated_at"
                )
            ),
            before,
        )

    def test_explicit_alternative_source_preserves_original_provenance(self):
        original = self.catalogue_source("Opprinnelig katalogkilde")
        alternative = SourceRecord.objects.create(
            source_system=original.source_system,
            source_locator="Eksisterende rettighetsdokumentasjon",
        )
        response = self.client.post(
            self.url("onboard"),
            self.payload(source_record=str(alternative.pk)),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RightsClaim.objects.get().source_record, alternative)
        self.assertEqual(
            MetadataAssertion.objects.get().source_record, original
        )
        self.assertEqual(SourceRecord.objects.count(), 2)

    def test_recording_provenance_fallback_and_no_fabricated_source(self):
        response = self.client.get(
            self.url("onboard"), {"recording": self.recording.pk}
        )
        self.assertIsNone(response.context["form"].default_source)
        source = self.catalogue_source(
            "Recording-kilde", recording_target=True
        )
        response = self.client.get(
            self.url("onboard"), {"recording": self.recording.pk}
        )
        self.assertEqual(response.context["form"].default_source, source)
        self.assertEqual(SourceRecord.objects.count(), 1)

    def test_ownership_default_100_and_explicit_lower_share(self):
        response = self.client.get(
            self.url("onboard"), {"recording": self.recording.pk}
        )
        self.assertEqual(
            response.context["form"]["ownership_share"].value(), 100
        )
        response = self.client.post(
            self.url("onboard"),
            self.payload(
                relationship_type="master_ownership", ownership_share="37.5"
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(str(RightsClaim.objects.get().share), "37.50")
        self.assertEqual(RightsClaim.objects.get().status, S.UNVERIFIED)
        self.assertFalse(SourceRecord.objects.exists())
