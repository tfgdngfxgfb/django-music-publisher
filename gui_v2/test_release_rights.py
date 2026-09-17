"""6H: context, batch reads, and the real signed modal boundary."""

from datetime import timedelta
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
from music_library.models import MusicLibraryEntry
from parties.models import Party
from provenance.models import SourceRecord, SourceSystem, MetadataAssertion
from rights import services, workflows
from rights.models import (
    Agreement,
    RightsClaim,
    RightsConfiguration,
    RightsDecision,
    Territory,
)
from rights_core.models import VerificationStatus as S
from gui_v2.release_rights import build_matrix, release_tracks
from gui_v2.catalogue_sources import catalogue_sources


@override_settings(GUI_V2_WRITES_ENABLED=True)
class ReleaseRightsMatrixTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("matrix-admin")
        self.client.force_login(self.user)
        self.local = Party.objects.create(name="P7", kind="organization")
        self.config = RightsConfiguration.objects.create(
            local_organization=self.local
        )
        self.release = Release.objects.create(title="Utgivelsen")
        self.other_release = Release.objects.create(title="Annen utgivelse")
        self.recording = self.add_recording()
        self.day = timezone.localdate()
        self.url = (
            reverse("gui_v2:release_detail", args=[self.release.pk])
            + "?tab=rights"
        )
        self.bulk_url = reverse(
            "gui_v2:release_rights_bulk", args=[self.release.pk]
        )

    def add_recording(self, title="Sangen", member=False):
        recording = Recording.objects.create(title=title)
        ReleaseTrack.objects.create(
            release=self.release,
            recording=recording,
            sequence_number=self.release.tracks.count() + 1,
        )
        if member:
            self.member(recording)
        return recording

    def member(self, recording=None, status="pending"):
        entry, _ = MusicLibraryEntry.objects.get_or_create(
            recording=recording or self.recording
        )
        return ManagedRecording.objects.create(
            library_entry=entry, status=status
        )

    def claim(self, recording=None, status=S.UNVERIFIED, **values):
        claim = services.create_rights_claim(
            recording=recording or self.recording,
            **{
                "right_type": "master_administration",
                "rights_holder": self.local,
                **values,
            },
        )
        if status != S.UNVERIFIED:
            services.decide_rights_claim(claim, status, user=self.user)
            claim.refresh_from_db()
        return claim

    def matrix(self):
        return build_matrix(self.release, release_tracks(self.release))

    def payload(self, **values):
        return {
            "recordings": [str(self.recording.pk)],
            "right_type": "distribution",
            "legal_scope": "general",
            "territory_mode": "world",
            "evidence_strength": "not_assessed",
            "allow_managed_registration": "on",
            "stage": "preview",
            **values,
        }

    def preview(self, **values):
        response = self.client.post(self.bulk_url, self.payload(**values))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            response.context["form"].errors, response.context["form"].errors
        )
        return response

    def apply(self, response, **values):
        source = response.context["form"].data
        data = {key: source.getlist(key) for key in source}
        data["stage"] = "apply"
        data.update(values)
        return self.client.post(self.bulk_url, data)

    def source(self, recordings, locator="Opprinnelig"):
        system, _ = SourceSystem.objects.get_or_create(
            name="Arkiv", defaults={"kind": "import"}
        )
        source = SourceRecord.objects.create(
            source_system=system, source_locator=locator
        )
        for recording in recordings:
            MetadataAssertion.objects.create(
                source_record=source,
                entity_type="recording",
                entity_uuid=recording.pk,
                field_name="title",
                raw_value=recording.title,
            )
        return source

    def test_native_tab_readonly_header_player_return_and_no_rights_leak(self):
        self.claim()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(
                self.url + "&return=/v2/utgivelser/?q=album"
            )
        self.assertContains(response, "Rettighetsmatrise")
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, "data-p7-player-footer")
        self.assertContains(
            response,
            "?tab=tracks&amp;return=/v2/utgivelser/%3Fq%3Dalbum",
        )
        self.assertEqual(
            response.context["return_url"], "/v2/utgivelser/?q=album"
        )
        self.assertFalse(
            any(
                q["sql"]
                .lstrip()
                .upper()
                .startswith(("INSERT", "UPDATE", "DELETE"))
                for q in queries
            )
        )
        reader = get_user_model().objects.create_user("release-reader")
        reader.user_permissions.add(
            Permission.objects.get(
                codename="view_release", content_type__app_label="catalogue"
            )
        )
        self.client.force_login(reader)
        response = self.client.get(self.url)
        self.assertContains(
            response, "Du har ikke tilgang til rettighetsopplysningene"
        )
        self.assertNotContains(response, "data-matrix-row")
        self.assertNotIn("rows", response.context)
        self.assertEqual(self.client.get(self.bulk_url).status_code, 403)

    def test_placements_order_unique_summaries_and_override(self):
        self.claim(
            status=S.CONFIRMED, right_type="master_ownership", share=100
        )
        track = ReleaseTrack.objects.create(
            release=self.release,
            recording=self.recording,
            sequence_number=2,
            side="B",
            track_number=4,
            title_override="Alternativ sportittel",
        )
        data = self.matrix()
        self.assertEqual((data["track_count"], data["unique_count"]), (2, 1))
        self.assertEqual(data["rows"][1]["track"], track)
        self.assertEqual(data["rows"][1]["position"], "Side B · Spor 4")
        self.assertEqual(
            data["rows"][0]["ownership"], data["rows"][1]["ownership"]
        )
        self.assertEqual(data["ownership_counts"][0][1], 1)
        self.assertContains(self.client.get(self.url), "Alternativ sportittel")

    def test_management_current_other_and_none_are_not_lifecycle(self):
        self.assertEqual(self.matrix()["rows"][0]["management_key"], "none")
        ReleaseTrack.objects.create(
            release=self.other_release,
            recording=self.recording,
            sequence_number=1,
        )
        ManagedRelease.objects.create(
            release=self.other_release, relationship="owned_catalogue"
        )
        self.assertEqual(
            self.matrix()["rows"][0]["management_key"], "via_other"
        )
        ManagedRelease.objects.create(
            release=self.release,
            relationship="owned_catalogue",
            status="inactive",
        )
        self.assertEqual(
            self.matrix()["rows"][0]["management_key"], "via_current"
        )
        self.member()
        self.assertEqual(self.matrix()["rows"][0]["management_key"], "pending")
        self.assertFalse(RightsClaim.objects.exists())

    def test_effective_lifecycle_history_uncertainty_and_no_get_reconcile(
        self,
    ):
        managed = self.member(status="active")
        self.assertEqual(
            self.matrix()["rows"][0]["management_key"], "uncertain"
        )
        self.claim(
            status=S.CONFIRMED, valid_until=self.day - timedelta(days=1)
        )
        # Simulate a missed date reconciliation, not a rights-decision write.
        managed.refresh_from_db()
        managed.status = "active"
        managed.save(update_fields=("status",))
        row = self.matrix()["rows"][0]
        self.assertEqual(row["management_key"], "inactive")
        self.assertTrue(row["state"].needs_reconciliation)
        managed.refresh_from_db()
        self.assertEqual(managed.status, "active")
        self.claim(
            status=S.CONFIRMED,
            right_type="distribution",
            release_scope=self.release,
        )
        self.assertEqual(self.matrix()["rows"][0]["management_key"], "active")

    def test_all_ownership_categories_and_unknown_share(self):
        other = Party.objects.create(name="Andre", kind="organization")
        cases = [
            (None, S.UNVERIFIED, self.local, "unresolved"),
            (100, S.CONFIRMED, self.local, "full"),
            (30, S.CONFIRMED, self.local, "partial"),
            (100, S.CONFIRMED, other, "not_owned"),
            (None, S.CONFIRMED, self.local, "unresolved"),
            (20, S.DISPUTED, self.local, "disputed"),
        ]
        ids = []
        for share, status, holder, category in cases:
            recording = self.add_recording(category)
            self.claim(
                recording=recording,
                right_type="master_ownership",
                share=share,
                status=status,
                rights_holder=holder,
            )
            ids.append((recording.pk, category, share))
        rows = {r["recording"].pk: r for r in self.matrix()["rows"]}
        for pk, category, share in ids:
            self.assertEqual(rows[pk]["ownership"].category, category)
            if share is None:
                self.assertIsNone(rows[pk]["ownership"].local_share)

    def test_admin_and_distribution_context_and_dates(self):
        ReleaseTrack.objects.create(
            release=self.other_release,
            recording=self.recording,
            sequence_number=1,
        )
        for kind, name in (
            ("master_administration", "administration"),
            ("distribution", "distribution"),
        ):
            for scope in (None, self.release, self.other_release):
                self.claim(
                    status=S.CONFIRMED, right_type=kind, release_scope=scope
                )
            self.claim(right_type=kind)
            self.claim(status=S.DISPUTED, right_type=kind)
            self.claim(
                status=S.CONFIRMED,
                right_type=kind,
                valid_from=self.day + timedelta(days=1),
            )
            self.claim(
                status=S.CONFIRMED,
                right_type=kind,
                valid_until=self.day - timedelta(days=1),
            )
            data = self.matrix()
            summary = data["rows"][0][name]
            self.assertEqual(
                (
                    summary["confirmed"],
                    summary["pending"],
                    summary["disputed"],
                    summary["future"],
                ),
                (2, 1, 1, 1),
            )
            self.assertFalse(
                any(
                    p["claim"].release_scope_id == self.other_release.pk
                    for p in summary["positions"]
                )
            )
            self.assertEqual(
                next(
                    s
                    for s in data["basis_counts"]
                    if s["label"] == RightsClaim.RightType(kind).label
                )["confirmed"],
                1,
            )

    def test_query_scaling_one_vs_fifty_with_claims_history_territory(self):
        self.member()
        no = Territory.objects.get(code="NO")
        self.claim(
            status=S.CONFIRMED, territory_mode="include", territories=[no]
        )
        with CaptureQueriesContext(connection) as one:
            self.client.get(self.url)
        for i in range(49):
            recording = self.add_recording(f"Innspilling {i}", member=True)
            self.claim(
                recording=recording,
                status=S.CONFIRMED,
                territory_mode="include",
                territories=[no],
            )
        with CaptureQueriesContext(connection) as many:
            self.client.get(self.url)
        self.assertEqual(len(one), len(many))

    def test_bulk_get_readonly_source_consensus_and_ownership_default(self):
        second = self.add_recording("Andre")
        for expected in (None, "partial", "different", "same"):
            if expected == "partial":
                source = self.source([self.recording])
            elif expected == "different":
                self.source([second], "Annen")
            elif expected == "same":
                # Earliest origin is used; a later shared source cannot replace it.
                self.source([self.recording, second], "Senere felles")
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(
                    self.bulk_url,
                    {"recordings": [self.recording.pk, second.pk]},
                )
            self.assertIsNone(response.context["form"].default_source)
            self.assertEqual(response.context["form"]["share"].value(), 100)
            self.assertNotIn("status", response.context["form"].fields)
            self.assertFalse(
                any(
                    q["sql"]
                    .lstrip()
                    .upper()
                    .startswith(("INSERT", "UPDATE", "DELETE"))
                    for q in queries
                )
            )
        single = self.client.get(
            self.bulk_url, {"recordings": [self.recording.pk]}
        )
        self.assertEqual(single.context["form"].default_source, source)
        third, fourth = self.add_recording("Tredje"), self.add_recording(
            "Fjerde"
        )
        shared = self.source([third, fourth])
        same = self.client.get(
            self.bulk_url, {"recordings": [third.pk, fourth.pk]}
        )
        self.assertEqual(same.context["form"].default_source, shared)
        with CaptureQueriesContext(connection) as queries:
            sources = catalogue_sources(
                [self.recording.pk, second.pk, third.pk, fourth.pk]
            )
        self.assertLessEqual(len(queries), 6)
        self.assertEqual(sources[third.pk], shared)

    def test_signed_bulk_unique_explicit_onboarding_and_provenance(self):
        ReleaseTrack.objects.create(
            release=self.release, recording=self.recording, sequence_number=2
        )
        source = self.source([self.recording])
        before = list(SourceRecord.objects.values())
        response = self.preview(
            recordings=[str(self.recording.pk)] * 2,
            source_record=str(source.pk),
        )
        self.assertEqual(len(response.context["plan"].rows), 1)
        self.assertContains(response, "Musikkarkiv-post vil bli opprettet")
        self.assertFalse(MusicLibraryEntry.objects.exists())
        self.assertFalse(RightsClaim.objects.exists())
        response = self.apply(response)
        self.assertEqual(response.json(), {"applied": 1})
        claim = RightsClaim.objects.get()
        self.assertEqual(
            (claim.status, claim.source_record), (S.UNVERIFIED, source)
        )
        self.assertEqual(ManagedRecording.objects.get().status, "pending")
        self.assertEqual(Recording.objects.count(), 1)
        self.assertFalse(
            RightsDecision.objects.exclude(decision="documented").exists()
        )
        self.assertEqual(list(SourceRecord.objects.values()), before)

    def test_onboarding_false_blocked_and_existing_member_can_register(self):
        response = self.preview(allow_managed_registration="")
        self.assertTrue(response.context["plan"].blockers)
        self.assertEqual(self.apply(response).status_code, 200)
        self.assertFalse(RightsClaim.objects.exists())
        self.member()
        self.assertEqual(
            self.apply(self.preview(allow_managed_registration="")).json(),
            {"applied": 1},
        )

    def test_scope_and_share_validation_at_gui_boundary(self):
        for values, field in (
            ({"legal_scope": ""}, "legal_scope"),
            ({"share": "10"}, "share"),
            (
                {
                    "right_type": "master_ownership",
                    "legal_scope": "release",
                    "share": "100",
                },
                "legal_scope",
            ),
        ):
            response = self.client.post(self.bulk_url, self.payload(**values))
            self.assertIn(field, response.context["form"].errors)
        response = self.preview(
            right_type="master_ownership", legal_scope="", share="35"
        )
        self.assertEqual(self.apply(response).status_code, 200)
        claim = RightsClaim.objects.get()
        self.assertEqual(str(claim.share), "35.00")
        self.assertIsNone(claim.release_scope)

    def test_explicit_current_release_scope_and_readonly_holder(self):
        other = Party.objects.create(name="Forged", kind="organization")
        response = self.preview(
            legal_scope="release", rights_holder=str(other.pk)
        )
        self.assertTrue(response.context["plan"].release_scoped)
        self.assertEqual(self.apply(response).json(), {"applied": 1})
        claim = RightsClaim.objects.get()
        self.assertEqual(
            (claim.rights_holder, claim.release_scope),
            (self.local, self.release),
        )

    def test_permissions_write_gate_and_old_post_not_a_bypass(self):
        self.assertEqual(
            self.client.post(self.url, {"action": "rights"}).status_code, 403
        )
        with self.settings(GUI_V2_WRITES_ENABLED=False):
            self.assertEqual(self.client.get(self.url).status_code, 200)
            self.assertEqual(
                self.client.post(self.bulk_url, self.payload()).status_code,
                403,
            )
        actor = get_user_model().objects.create_user("registrar")
        actor.user_permissions.add(
            *Permission.objects.filter(
                codename__in=(
                    "view_release",
                    "view_rightsclaim",
                    "add_rightsclaim",
                )
            )
        )
        self.client.force_login(actor)
        self.assertEqual(
            self.client.post(self.bulk_url, self.payload()).status_code, 403
        )
        response = self.preview(allow_managed_registration="")
        self.assertTrue(response.context["plan"].blockers)
        self.member()
        self.assertEqual(
            self.apply(self.preview(allow_managed_registration="")).json(),
            {"applied": 1},
        )

    def test_stale_inputs_actor_and_token_cannot_apply(self):
        for changed in (
            "track",
            "recording",
            "config",
            "membership",
            "claim",
            "token",
            "expired",
        ):
            with self.subTest(changed=changed):
                recording = self.add_recording(changed)
                response = self.preview(recordings=[str(recording.pk)])
                if changed == "track":
                    track = self.release.tracks.get(recording=recording)
                    track.title_override = "Endret"
                    track.save()
                elif changed == "recording":
                    recording.title = "Endret"
                    recording.save()
                elif changed == "config":
                    self.config.save()
                elif changed == "membership":
                    self.member(recording)
                elif changed == "claim":
                    self.claim(recording=recording)
                before = RightsClaim.objects.count()
                if changed == "expired":
                    with patch(
                        "django.core.signing.time.time",
                        return_value=timezone.now().timestamp() + 1900,
                    ):
                        result = self.apply(response)
                else:
                    result = self.apply(
                        response,
                        **(
                            {"preview_token": "tampered"}
                            if changed == "token"
                            else {}
                        ),
                    )
                self.assertEqual(result.status_code, 200)
                self.assertTrue(result.context["form"].errors)
                self.assertEqual(RightsClaim.objects.count(), before)

    def test_exact_and_ambiguous_positions_block_including_history(self):
        for status in (
            S.UNVERIFIED,
            S.CONFIRMED,
            S.DISPUTED,
            S.REJECTED,
            S.SUPERSEDED,
        ):
            recording = self.add_recording(status, member=True)
            claim = self.claim(recording=recording, right_type="distribution")
            if status == S.SUPERSEDED:
                services.supersede_rights_claim(
                    claim,
                    user=self.user,
                    rights_holder=self.local,
                    territory_mode="include",
                    territories=[Territory.objects.get(code="NO")],
                )
            elif status != S.UNVERIFIED:
                services.decide_rights_claim(claim, status, user=self.user)
            response = self.preview(
                recordings=[str(recording.pk)], notes="Ny dokumentasjon"
            )
            self.assertContains(response, "Eksisterende tilsvarende posisjon")
            self.assertTrue(response.context["plan"].blockers)
        self.member()
        self.claim(right_type="distribution", valid_from=self.day)
        self.assertContains(self.preview(), "Mulig overlappende posisjon")

    def test_distinct_territories_periods_and_release_scope(self):
        self.member()
        ReleaseTrack.objects.create(
            release=self.other_release,
            recording=self.recording,
            sequence_number=1,
        )
        self.claim(right_type="distribution", release_scope=self.other_release)
        current = self.preview(legal_scope="release")
        self.assertFalse(current.context["plan"].blockers)
        self.assertTrue(
            self.preview(legal_scope="general").context["plan"].blockers
        )
        recording = self.add_recording("Perioder", member=True)
        self.claim(
            recording=recording,
            right_type="distribution",
            valid_until=self.day - timedelta(days=1),
        )
        self.assertFalse(
            self.preview(
                recordings=[str(recording.pk)], valid_from=str(self.day)
            )
            .context["plan"]
            .blockers
        )

    def test_conflict_blocks_all_and_late_failure_rolls_back(self):
        self.member()
        other = Party.objects.create(name="Eier", kind="organization")
        self.claim(
            status=S.CONFIRMED,
            rights_holder=other,
            right_type="master_ownership",
            share=60,
        )
        response = self.preview(right_type="master_ownership", share=50)
        self.assertContains(response, "overstiger 100 %")
        self.assertTrue(response.context["plan"].blockers)
        second = self.add_recording("Ny")
        response = self.preview(
            recordings=[str(self.recording.pk), str(second.pk)]
        )
        original = services.create_rights_claim
        calls = []

        def fail_second(**kwargs):
            calls.append(1)
            if len(calls) == 2:
                from django.core.exceptions import ValidationError

                raise ValidationError("Simulert feil")
            return original(**kwargs)

        with patch(
            "rights.services.create_rights_claim", side_effect=fail_second
        ):
            result = self.apply(response)
        self.assertContains(result, "Simulert feil")
        self.assertEqual(RightsClaim.objects.count(), 1)
        self.assertEqual(ManagedRecording.objects.count(), 1)

    def test_empty_database_and_invalid_selection(self):
        empty = Release.objects.create(title="Tom")
        response = self.client.get(
            reverse("gui_v2:release_detail", args=[empty.pk]),
            {"tab": "rights"},
        )
        self.assertContains(response, "Ingen spor")
        self.assertEqual(self.client.get(self.bulk_url).status_code, 400)
        self.assertEqual(
            self.client.get(
                self.bulk_url, {"recordings": "not-a-uuid"}
            ).status_code,
            400,
        )

    def test_query_scaling_repeated_track_placements(self):
        self.member()
        self.claim(status=S.CONFIRMED)
        with CaptureQueriesContext(connection) as one:
            self.client.get(self.url)
        for n in range(2, 51):
            ReleaseTrack.objects.create(
                release=self.release,
                recording=self.recording,
                sequence_number=n,
            )
        with CaptureQueriesContext(connection) as many:
            response = self.client.get(self.url)
        self.assertEqual(response.context["unique_count"], 1)
        self.assertEqual(len(one), len(many))

    def test_full_scope_documentation_and_explicit_source_override(self):
        original = self.source([self.recording])
        alternative = SourceRecord.objects.create(
            source_system=original.source_system,
            source_locator="Avtalegrunnlag",
        )
        grantor = Party.objects.create(
            name="Rettighetsgiver", kind="organization"
        )
        agreement = Agreement.objects.create(
            title="Avtale", agreement_type="license"
        )
        no = Territory.objects.get(code="NO")
        response = self.preview(
            territory_mode="exclude",
            territories=[str(no.pk)],
            valid_from=str(self.day),
            valid_until=str(self.day + timedelta(days=30)),
            grantor=str(grantor.pk),
            agreement=str(agreement.pk),
            source_record=str(alternative.pk),
            evidence_strength="documented",
            notes="Felles rettighetsposisjon",
            legal_scope="release",
        )
        self.assertEqual(self.apply(response).json(), {"applied": 1})
        claim = RightsClaim.objects.get()
        self.assertEqual(
            (claim.grantor, claim.agreement, claim.source_record),
            (grantor, agreement, alternative),
        )
        self.assertEqual(claim.evidence_strength, "documented")
        self.assertEqual(list(claim.territories.all()), [no])
        self.assertEqual(claim.status, S.UNVERIFIED)
        self.assertEqual(
            MetadataAssertion.objects.get().source_record, original
        )
        self.assertEqual(SourceRecord.objects.count(), 2)

    def test_different_actor_and_removed_track_block_apply(self):
        response = self.preview()
        actor = get_user_model().objects.create_superuser("different-actor")
        self.client.force_login(actor)
        result = self.apply(response)
        self.assertTrue(result.context["form"].errors)
        self.assertFalse(RightsClaim.objects.exists())
        self.client.force_login(self.user)
        self.release.tracks.get().delete()
        self.assertEqual(self.apply(response).status_code, 400)
        self.assertFalse(RightsClaim.objects.exists())

    def test_new_claim_between_preview_and_apply_prevents_duplicate(self):
        self.member()
        response = self.preview(allow_managed_registration="")
        self.claim(right_type="distribution")
        result = self.apply(response)
        self.assertContains(result, "Grunnlaget er endret")
        self.assertEqual(RightsClaim.objects.count(), 1)

    def test_disjoint_territories_are_independent_positions(self):
        self.member()
        no, se = Territory.objects.get(code="NO"), Territory.objects.get(
            code="SE"
        )
        self.claim(
            right_type="distribution",
            territory_mode="include",
            territories=[no],
        )
        response = self.preview(
            territory_mode="include", territories=[str(se.pk)]
        )
        self.assertFalse(response.context["plan"].blockers)
        self.assertEqual(self.apply(response).json(), {"applied": 1})
        self.assertEqual(RightsClaim.objects.count(), 2)

    def test_missing_configuration_has_no_false_local_conclusion(self):
        self.config.delete()
        response = self.client.get(self.url)
        self.assertContains(response, "Lokal organisasjon må konfigureres")
        response = self.client.post(self.bulk_url, self.payload())
        self.assertIn("rights_holder", response.context["form"].errors)
        self.assertFalse(RightsClaim.objects.exists())

    def test_preview_edit_is_readonly_and_keeps_multiple_recordings(self):
        second = self.add_recording("Andre")
        response = self.preview(
            recordings=[str(self.recording.pk), str(second.pk)]
        )
        result = self.apply(response, stage="edit")
        self.assertIsNone(result.context["plan"])
        self.assertEqual(len(result.context["selected"]), 2)
        self.assertFalse(RightsClaim.objects.exists())
        self.assertFalse(ManagedRecording.objects.exists())
