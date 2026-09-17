from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse

from catalogue.models import Recording, Release
from delivery.models import Delivery, DeliveryProfile
from media_assets.models import DigitizationBatch, DigitizationFile, FileAsset


class HomeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="home-user")
        self.client.force_login(self.user)
        self.home_url = reverse("gui_v2:home")

    def grant(self, *codenames):
        self.user.user_permissions.add(
            *Permission.objects.filter(codename__in=codenames)
        )
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)

    def test_empty_home_and_permissions(self):
        response = self.client.get(self.home_url)
        self.assertContains(response, "Fortsett arbeidet")
        self.assertContains(response, "Ingen objekter er åpnet")
        self.assertNotContains(response, "Musikkarkiv</strong>")
        self.assertContains(response, 'id="v2-audio"')

    def test_recent_objects_are_user_specific_and_permission_checked(self):
        self.grant("view_recording", "view_release")
        recording = Recording.objects.create(title="Nylig innspilling")
        release = Release.objects.create(title="Nylig utgivelse")
        self.client.get(
            reverse("gui_v2:recording_detail", args=[recording.pk])
        )
        self.client.get(reverse("gui_v2:release_detail", args=[release.pk]))
        response = self.client.get(self.home_url)
        self.assertEqual(
            [item["title"] for item in response.context["recent_items"]],
            ["Nylig utgivelse", "Nylig innspilling"],
        )
        other = get_user_model().objects.create_user(
            username="second-home-user"
        )
        second_client = Client()
        second_client.force_login(other)
        self.assertNotContains(
            second_client.get(self.home_url), "Nylig utgivelse"
        )
        self.user.user_permissions.clear()
        self.assertEqual(
            self.client.get(self.home_url).context["recent_items"], []
        )

    def test_partial_digitization_permissions_do_not_offer_dead_links(self):
        self.grant("view_digitizationbatch")
        response = self.client.get(self.home_url)
        self.assertNotContains(response, 'href="/v2/digitalisering/"')

    def test_followup_count_uses_actual_open_batches(self):
        self.grant(
            "view_digitizationbatch",
            "view_fileasset",
            "view_filelocation",
            "view_release",
            "view_recording",
        )
        release = Release.objects.create(title="Arbeidsutgivelse")
        batch = DigitizationBatch.objects.create(
            release=release, title="Arbeidsbatch", created_by=self.user
        )
        response = self.client.get(self.home_url)
        self.assertContains(response, "Digitaliseringer under arbeid")
        self.assertEqual(response.context["followups"][0]["count"], 1)
        self.assertIn("status=open", response.context["followups"][0]["url"])
        self.client.get(reverse("gui_v2:digitization_detail", args=[batch.pk]))
        self.assertEqual(
            self.client.get(self.home_url).context["continue_items"][0][
                "title"
            ],
            "Arbeidsbatch",
        )

    def test_unlinked_master_followup_opens_filtered_batch(self):
        self.grant(
            "view_digitizationbatch",
            "view_fileasset",
            "view_filelocation",
            "view_release",
            "view_recording",
        )
        release = Release.objects.create(title="Kildeutgivelse")
        batch = DigitizationBatch.objects.create(
            release=release, title="Koblingsarbeid", created_by=self.user
        )
        other = DigitizationBatch.objects.create(
            release=release, title="Annen batch", created_by=self.user
        )
        asset = FileAsset.objects.create(
            filename="master.wav", role=FileAsset.Role.EDITED_WAV_MASTER
        )
        DigitizationFile.objects.create(batch=batch, asset=asset)
        home = self.client.get(self.home_url)
        followup = next(
            item
            for item in home.context["followups"]
            if "uten innspilling" in item["title"]
        )
        self.assertEqual(followup["count"], 1)
        response = self.client.get(followup["url"])
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertEqual(response.context["page"][0].pk, batch.pk)
        self.assertNotEqual(response.context["page"][0].pk, other.pk)

    def test_delivery_followup_filters_only_attention_statuses(self):
        self.grant("view_delivery")
        for status in (Delivery.Status.READY, Delivery.Status.PARTIAL):
            Delivery.objects.create(
                created_by=self.user,
                status=status,
                profile=DeliveryProfile.INTERNAL_COMPLETE,
                purpose=Delivery.Purpose.INTERNAL,
                recipient_name="Intern",
            )
        home = self.client.get(self.home_url)
        followup = next(
            item
            for item in home.context["followups"]
            if "Leveranser med avvik" in item["title"]
        )
        self.assertEqual(followup["count"], 1)
        history = self.client.get(followup["url"])
        self.assertEqual(len(history.context["deliveries"]), 1)
        self.assertEqual(
            history.context["deliveries"][0].status, Delivery.Status.PARTIAL
        )
        delivery = history.context["deliveries"][0]
        self.client.get(reverse("delivery:detail", args=[delivery.pk]))
        recent = self.client.get(self.home_url).context["recent_items"]
        self.assertEqual(recent[0]["label"], "Leveranse")

    def test_recent_list_resolves_in_batches(self):
        self.grant("view_recording")
        recordings = [
            Recording.objects.create(title=f"Spor {index}")
            for index in range(7)
        ]
        for recording in recordings:
            self.client.get(
                reverse("gui_v2:recording_detail", args=[recording.pk])
            )
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.home_url)
        self.assertEqual(len(response.context["recent_items"]), 7)
        self.assertLessEqual(len(queries), 8)
