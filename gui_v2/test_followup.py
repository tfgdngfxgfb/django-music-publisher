"""6I read-only endpoints, permission boundaries, canonical transitions and scale."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalogue.models import Recording, Release, ReleaseTrack
from catalogue.authority import annotate_recording_authority
from managed_music.models import ManagedRecording, ManagedRelease
from music_library.models import MusicLibraryEntry
from parties.models import Party
from provenance.models import SourceRecord, SourceSystem
from rights import services, workflows
from rights.models import (
    Agreement,
    RightsClaim,
    RightsConfiguration,
    RightsDecision,
)
from rights.followup_queries import collect_items, load_recordings, Visibility
from rights.followup import evaluate_recording
from gui_v2.followup import category_counts, filtered_items


class FollowUpWorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("followup")
        self.client.force_login(self.user)
        self.local = Party.objects.create(name="P7", kind="organization")
        self.config = RightsConfiguration.objects.create(
            local_organization=self.local
        )
        self.recording = Recording.objects.create(title="Sangen")
        self.url = reverse("gui_v2:followup")
        self.day = timezone.localdate()

    def claim(self, recording=None, status="unverified", **values):
        c = services.create_rights_claim(
            recording=recording or self.recording,
            **{
                "rights_holder": self.local,
                "right_type": "master_administration",
                **values,
            },
        )
        if status != "unverified":
            services.decide_rights_claim(c, status, user=self.user)
            c.refresh_from_db()
        return c

    def member(self, recording=None, status="pending"):
        entry, _ = MusicLibraryEntry.objects.get_or_create(
            recording=recording or self.recording
        )
        return ManagedRecording.objects.create(
            library_entry=entry, status=status
        )

    def release(self, status="active", recording=None):
        r = Release.objects.create(title="Album")
        ReleaseTrack.objects.create(
            release=r, recording=recording or self.recording, sequence_number=1
        )
        ManagedRelease.objects.create(
            release=r, status=status, relationship="managed_catalogue"
        )
        return r

    def items(self, user=None):
        return collect_items(user or self.user, on_date=self.day)[0]

    def codes(self, user=None):
        return {c for i in self.items(user) for c in i.codes}

    def reader(self, *permissions):
        user = get_user_model().objects.create_user(
            "reader" + str(get_user_model().objects.count())
        )
        for value in permissions:
            app, code = value.split(".")
            user.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label=app, codename=code
                )
            )
        return user

    def test_native_navigation_player_and_no_mutation_endpoint(self):
        self.claim()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Oppfølging")
        self.assertContains(response, "data-p7-player-footer")
        self.assertContains(response, "Flere filtre")
        self.assertContains(response, "followup.css")
        self.assertNotContains(response, 'type="checkbox"')
        self.assertEqual(self.client.post(self.url, {}).status_code, 405)

    def test_all_gets_leave_domain_snapshots_unchanged(self):
        m = self.member(status="active")
        self.claim()
        models = (
            Recording,
            MusicLibraryEntry,
            ManagedRecording,
            ManagedRelease,
            RightsClaim,
            RightsDecision,
            SourceRecord,
            ReleaseTrack,
        )
        snapshot = lambda: [
            list(model.objects.order_by("pk").values()) for model in models
        ]
        before = snapshot()
        for query in (
            {},
            {"category": "all"},
            {"category": "all", "selected": f"management:{m.pk}"},
            {"category": "all", "horizon": "180", "sort": "date"},
        ):
            self.assertEqual(self.client.get(self.url, query).status_code, 200)
        self.assertEqual(snapshot(), before)
        m.refresh_from_db()
        self.assertEqual(m.status, "active")

    def test_workspace_requires_rights_view(self):
        self.client.force_login(self.reader("catalogue.view_recording"))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_permissions_precede_counts_options_and_pagination(self):
        self.claim()
        self.member(status="active")
        self.release()
        rights_only = self.reader("rights.view_rightsclaim")
        self.assertEqual(self.items(rights_only), ())
        claim_reader = self.reader(
            "rights.view_rightsclaim", "catalogue.view_recording"
        )
        self.assertEqual(self.codes(claim_reader), {"claim.unverified"})
        self.client.force_login(claim_reader)
        response = self.client.get(self.url, {"category": "all"})
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertNotContains(response, "Historikk må avklares")
        self.assertNotIn(
            "management",
            dict(response.context["form"].fields["target"].choices),
        )
        review_reader = self.reader(
            "rights.view_rightsclaim",
            "catalogue.view_release",
            "managed_music.view_managedrelease",
        )
        self.assertEqual(
            {i.target_type for i in self.items(review_reader)}, {"release"}
        )
        self.client.force_login(review_reader)
        response = self.client.get(self.url, {"category": "all"})
        self.assertNotContains(response, "Sangen")
        self.assertContains(response, "Album")
        manager = self.reader(
            "rights.view_rightsclaim",
            "catalogue.view_recording",
            "managed_music.view_managedrecording",
        )
        self.assertIn("management.history_uncertain", self.codes(manager))
        incomplete_review = self.reader(
            "rights.view_rightsclaim", "catalogue.view_release"
        )
        self.assertEqual(self.items(incomplete_review), ())

    def test_source_agreement_party_masking_and_search(self):
        system = SourceSystem.objects.create(
            name="HEMMELIG-KILDESYSTEM", kind="import"
        )
        source = SourceRecord.objects.create(
            source_system=system, source_locator="HEMMELIG-KILDEPOST"
        )
        agreement = Agreement.objects.create(
            title="HEMMELIG-AVTALE", agreement_type="license"
        )
        grantor = Party.objects.create(
            name="HEMMELIG-GIVER", kind="organization"
        )
        self.claim(source_record=source, agreement=agreement, grantor=grantor)
        reader = self.reader(
            "rights.view_rightsclaim", "catalogue.view_recording"
        )
        self.client.force_login(reader)
        response = self.client.get(self.url)
        self.assertNotContains(response, "HEMMELIG")
        self.assertEqual(
            response.context["form"].fields["source"].choices,
            [("", "Alle tilgjengelige kilder")],
        )
        for query in ("HEMMELIG-KILDE", "HEMMELIG-AVTALE", "HEMMELIG-GIVER"):
            self.assertEqual(
                filtered_items(self.items(reader), {"q": query}), []
            )
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        for label in (
            system.name,
            source.source_locator,
            agreement.title,
            grantor.name,
        ):
            self.assertContains(response, label)
        response = self.client.get(self.url, {"source": system.name})
        self.assertEqual(response.context["page"].paginator.count, 1)

    def test_legacy_correction_and_onboarding_remove_specific_signals(self):
        managed = self.member(status="active")
        old = self.claim(valid_until=self.day - timedelta(days=10))
        self.assertIn("management.history_uncertain", self.codes())
        # An expired unresolved claim remains evidence in the explicit history
        # inspector, not a normal current duplicate/mismatch warning.
        item = next(i for i in self.items() if i.target_type == "management")
        self.assertEqual(item.claim_ids, (old.pk,))
        self.assertNotIn("claim.unverified", self.codes())
        workflows.correct_legacy_management_history(
            managed, user=self.user, reason="Arvet status var feil"
        )
        self.assertNotIn("management.history_uncertain", self.codes())
        self.assertTrue(
            ManagedRecording.objects.filter(pk=managed.pk).exists()
        )
        r = Recording.objects.create(title="Eksplisitt onboarding")
        self.claim(recording=r, status="confirmed")
        self.assertIn(
            "management.local_basis_without_membership", self.codes()
        )
        workflows.onboard_managed_recording(
            user=self.user, recording=r, relationship_type="distribution"
        )
        self.assertNotIn(
            "management.local_basis_without_membership", self.codes()
        )

    def test_manual_guard_still_blocks_unverified_disputed_and_future_after_return(
        self,
    ):
        from django.core.exceptions import ValidationError
        from managed_music.services import return_to_music_library

        managed = self.member()
        c = self.claim(status="rejected")
        return_to_music_library(
            managed, user=self.user, reason="Avsluttet onboarding"
        )
        for status in ("disputed", "confirmed"):
            with self.assertRaises(ValidationError):
                workflows.decide_rights_claim(c, status, user=self.user)
            c.refresh_from_db()
            self.assertEqual(c.status, "rejected")
        self.assertNotIn(
            "management.local_basis_without_membership", self.codes()
        )

    def test_counts_are_mutually_exclusive_before_category_filter(self):
        self.claim(
            status="confirmed", valid_until=self.day + timedelta(days=2)
        )
        items = self.items()
        counts = dict((key, count) for key, _, count in category_counts(items))
        self.assertEqual(
            sum(
                counts[k] for k in ("action", "quality", "upcoming", "review")
            ),
            counts["all"],
        )
        self.assertEqual(counts["upcoming"], 0)
        self.assertIn("claim.expiring", self.codes())
        self.assertEqual(filtered_items(items, {"category": "upcoming"}), [])

    def test_filters_search_horizon_and_stable_pagination(self):
        for n in range(28):
            r = Recording.objects.create(title=f"Sang {n:02}")
            self.claim(recording=r)
        response = self.client.get(
            self.url, {"category": "all", "sort": "title", "page": 2}
        )
        keys = [r["item"].key for r in response.context["rows"]]
        again = self.client.get(
            self.url, {"category": "all", "sort": "title", "page": 2}
        )
        self.assertEqual(keys, [r["item"].key for r in again.context["rows"]])
        self.assertEqual(len(keys), 3)
        response = self.client.get(
            self.url,
            {
                "q": "Sang 03",
                "target": "claim",
                "status": "claim.unverified",
                "right_type": "master_administration",
                "signal": "claim.unverified",
            },
        )
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertContains(response, "Sang 03")
        self.claim(
            status="confirmed",
            evidence_strength="documented",
            valid_from=self.day + timedelta(days=60),
        )
        self.assertEqual(
            self.client.get(
                self.url, {"category": "upcoming", "horizon": "30"}
            )
            .context["page"]
            .paginator.count,
            0,
        )
        self.assertEqual(
            self.client.get(
                self.url, {"category": "upcoming", "horizon": "90"}
            )
            .context["page"]
            .paginator.count,
            1,
        )

    def test_links_keep_existing_workspaces_and_return_context(self):
        c = self.claim()
        self.release()
        response = self.client.get(self.url)
        self.assertContains(
            response,
            reverse(
                "gui_v2:recording_rights_claim", args=[self.recording.pk, c.pk]
            ),
        )
        self.assertContains(response, "return=%2Fv2%2Foppfolging%2F")
        response = self.client.get(self.url, {"category": "review"})
        self.assertContains(response, "tab=rights")

    def test_confirmation_and_documentation_remove_individual_signals(self):
        self.member()
        c = self.claim()
        self.assertIn("claim.unverified", self.codes())
        workflows.decide_rights_claim(c, "confirmed", user=self.user)
        self.assertNotIn("claim.unverified", self.codes())
        self.assertIn("evidence.not_assessed", self.codes())
        c.refresh_from_db()
        workflows.document_rights_claim(
            c, user=self.user, evidence_strength="documented"
        )
        self.assertNotIn("evidence.not_assessed", self.codes())
        self.assertEqual(self.items(), ())

    def test_scope_repair_and_duplicate_superseding(self):
        self.member()
        release = self.release()
        c = self.claim(release_scope=release)
        ReleaseTrack.objects.filter(release=release).delete()
        self.assertIn("scope.release_mismatch", self.codes())
        ReleaseTrack.objects.create(
            release=release, recording=self.recording, sequence_number=1
        )
        self.assertNotIn("scope.release_mismatch", self.codes())
        duplicate = self.claim(release_scope=release)
        self.assertIn("claim.duplicate_position", self.codes())
        workflows.decide_rights_claim(duplicate, "rejected", user=self.user)
        self.assertNotIn("claim.duplicate_position", self.codes())
        self.assertTrue(RightsClaim.objects.filter(pk=duplicate.pk).exists())
        self.assertTrue(RightsClaim.objects.filter(pk=c.pk).exists())

    def test_history_uses_closed_claims_without_normal_closed_claim_warnings(
        self,
    ):
        self.member()
        c = self.claim(
            status="confirmed", valid_until=self.day - timedelta(days=1)
        )
        self.assertNotIn("evidence.not_assessed", self.codes())
        self.assertNotIn("scope.release_mismatch", self.codes())
        services.decide_rights_claim(c, "rejected", user=self.user)
        self.assertNotIn("management.pending_without_basis", self.codes())
        self.assertNotIn("management.history_uncertain", self.codes())

    def test_release_review_unique_counts_and_inactive_authority(self):
        release = self.release()
        ReleaseTrack.objects.create(
            release=release, recording=self.recording, sequence_number=2
        )
        item = next(i for i in self.items() if i.target_type == "release")
        self.assertEqual(dict(item.details)["Unike innspillinger"], "1")
        self.claim(
            status="confirmed", right_type="master_ownership", share=100
        )
        item = next(i for i in self.items() if i.target_type == "release")
        self.assertNotIn("release.without_current_local_basis", item.codes)
        m = release.managed_release
        m.status = "inactive"
        m.save()
        self.assertFalse(any(i.target_type == "release" for i in self.items()))
        authority = annotate_recording_authority(Recording.objects.all()).get(
            pk=self.recording.pk
        )
        self.assertTrue(authority.authority_release_only)

    def test_missing_configuration_is_not_absence_of_basis(self):
        self.claim()
        self.member()
        self.release()
        self.config.delete()
        response = self.client.get(self.url, {"category": "all"})
        self.assertEqual(response.context["page"].paginator.count, 0)
        self.assertContains(response, "Lokal organisasjon må konfigureres")

    def test_management_link_targets_exact_recording_even_with_same_title(
        self,
    ):
        from gui_v2.followup import present_item
        from django.test import RequestFactory

        managed = self.member()
        other = Recording.objects.create(title=self.recording.title)
        self.member(other)
        item = next(i for i in self.items() if i.target_id == managed.pk)
        request = RequestFactory().get(self.url)
        row = present_item(item, request)
        response = self.client.get(row["links"][0][1])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["selected"]["managed"].pk, managed.pk
        )
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertContains(response, "Tilbake til forrige arbeidsflate")

    def test_loaded_facts_evaluate_without_queries(self):
        self.member()
        self.claim()
        facts = tuple(
            load_recordings(
                (self.recording.pk,),
                Visibility.for_user(self.user),
                self.local.pk,
                self.day,
            )
        )
        with self.assertNumQueries(0):
            evaluate_recording(
                facts[0], local_id=self.local.pk, on_date=self.day
            )

    def query_count(self):
        self.user.get_all_permissions()
        with CaptureQueriesContext(connection) as queries:
            self.items()
        return len(queries)

    def test_query_scale_one_to_100_claims(self):
        self.claim()
        baseline = self.query_count()
        for _ in range(99):
            self.claim()
        self.assertEqual(self.query_count(), baseline)

    def test_query_scale_one_to_100_managed_recordings(self):
        self.member()
        self.claim()
        baseline = self.query_count()
        for n in range(99):
            r = Recording.objects.create(title=f"Managed {n}")
            self.member(r)
            self.claim(r)
        self.assertEqual(self.query_count(), baseline)

    def test_query_scale_one_to_50_managed_releases(self):
        self.release()
        self.claim()
        baseline = self.query_count()
        for n in range(49):
            r = Recording.objects.create(title=f"Release track {n}")
            self.release(recording=r)
            self.claim(r)
        self.assertEqual(self.query_count(), baseline)

    def test_home_only_links_without_running_followup_engine(self):
        with patch(
            "rights.followup_queries.collect_items",
            side_effect=AssertionError("Must not derive on Home"),
        ):
            response = self.client.get(reverse("gui_v2:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.url)
