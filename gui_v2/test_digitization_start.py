"""Focused checks for the release-first digitization entry point."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from catalogue.models import (
    Recording,
    RecordingContribution,
    Release,
    ReleaseTrack,
)
from media_assets.models import DigitizationBatch


@override_settings(GUI_V2_WRITES_ENABLED=True)
class DigitizationStartTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("start-digitization")
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="media_assets",
                codename__in=(
                    "view_digitizationbatch",
                    "view_fileasset",
                    "view_filelocation",
                    "operate_digitization",
                ),
            ),
            *Permission.objects.filter(
                content_type__app_label="catalogue",
                codename__in=("view_release", "view_recording", "add_release"),
            ),
        )
        self.client.force_login(self.user)
        self.url = reverse("gui_v2:digitization_start")

    def test_search_results_show_real_credit_and_filters_paginate(self):
        releases = [
            Release.objects.create(
                title=f"Kassett {number}",
                release_type=Release.Type.CASSETTE,
                release_year=1980 + number,
            )
            for number in range(6)
        ]
        recording = Recording.objects.create(title="Sang")
        ReleaseTrack.objects.create(
            release=releases[0], recording=recording, sequence_number=1
        )
        RecordingContribution.objects.create(
            recording=recording,
            role=RecordingContribution.Role.PRIMARY,
            credited_as="K. Hansen",
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"].paginator.count, 6)
        self.assertEqual(len(response.context["page"].object_list), 5)
        self.assertContains(response, "K. Hansen")
        self.assertContains(response, "Neste →")
        filtered = self.client.get(
            self.url,
            {"q": "Hansen", "format": Release.Type.CASSETTE, "year": "1980"},
        )
        self.assertEqual(filtered.context["page"].paginator.count, 1)
        self.assertEqual(
            filtered.context["page"].object_list[0].pk, releases[0].pk
        )

    def test_new_release_requires_type_and_barcode_permission(self):
        payload = {"mode": "new", "release-title": "Ny utgivelse"}
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kontroller feltene")
        self.assertFalse(Release.objects.filter(title="Ny utgivelse").exists())
        payload.update(
            {
                "release-release_type": Release.Type.CD,
                "release-barcode": "12345670",
            }
        )
        self.assertEqual(self.client.post(self.url, payload).status_code, 403)
        self.assertFalse(Release.objects.filter(title="Ny utgivelse").exists())
        payload.pop("release-barcode")
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            DigitizationBatch.objects.filter(
                release__title="Ny utgivelse"
            ).exists()
        )
