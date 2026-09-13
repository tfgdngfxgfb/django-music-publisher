from urllib.parse import parse_qs, quote, urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue.models import Recording, Release, ReleaseTrack
from media_assets.models import FileAsset, FileLocation
from music_library.models import MusicLibraryEntry
from provenance.models import (
    AppliedMetadataChange,
    MetadataAssertion,
    SourceRecord,
    SourceSystem,
)
from provenance.services import ConcurrentCatalogueChange, apply_assertion
from rights_core.models import VerificationStatus


class WorkbenchTestCase(TestCase):
    password = "workbench-password"

    def create_user(self, username="cataloguer", *, superuser=False, permissions=()):
        user = get_user_model().objects.create_user(
            username=username,
            password=self.password,
            is_staff=True,
            is_superuser=superuser,
        )
        for permission in permissions:
            app_label, codename = permission.split(".")
            user.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label=app_label, codename=codename
                )
            )
        return user

    def login(self, user):
        self.client.force_login(user)

    def assertion(self, recording, *, field_name, value):
        source = SourceSystem.objects.create(name="LP-cover", kind="physical")
        record = SourceRecord.objects.create(
            source_system=source, source_locator="Coverets bakside"
        )
        return MetadataAssertion.objects.create(
            source_record=record,
            entity_type=MetadataAssertion.EntityType.RECORDING,
            entity_uuid=recording.pk,
            field_name=field_name,
            raw_value=value,
        )


class AuthenticationAndPermissionTests(WorkbenchTestCase):
    def test_root_login_preserves_internal_destination(self):
        response = self.client.get(reverse("workbench:releases"))
        parsed = urlparse(response.url)
        self.assertEqual(parsed.path, reverse("admin:login"))
        self.assertEqual(
            parse_qs(parsed.query)["next"], [reverse("workbench:releases")]
        )

    def test_navigation_and_direct_post_use_server_side_permissions(self):
        user = self.create_user(
            permissions=(
                "catalogue.view_release",
                "music_library.view_musiclibraryentry",
            )
        )
        self.login(user)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Musikkarkiv")
        self.assertContains(response, "Utgivelser")
        self.assertNotContains(response, "Forvaltet musikk")
        self.assertNotContains(response, "Administrasjon")
        self.assertEqual(self.client.get(reverse("workbench:managed")).status_code, 403)
        release = Release.objects.create(title="Beskyttet")
        response = self.client.post(
            reverse("workbench:release", args=(release.pk,)), {"title": "Endret"}
        )
        self.assertEqual(response.status_code, 403)
        release.refresh_from_db()
        self.assertEqual(release.title, "Beskyttet")

    def test_external_return_url_is_not_used(self):
        user = self.create_user(superuser=True)
        self.login(user)
        release = Release.objects.create(title="Trygg retur")
        response = self.client.post(
            reverse("workbench:release", args=(release.pk,))
            + "?return=https://evil.example/",
            {
                "title": "Trygg retur",
                "release_type": "",
                "release_date": "",
                "release_year": "",
                "label": "",
                "catalogue_number": "",
                "verification_status": VerificationStatus.UNVERIFIED,
                "notes": "",
                "return": "https://evil.example/",
            },
        )
        self.assertRedirects(
            response,
            reverse("workbench:release", args=(release.pk,)),
            fetch_redirect_response=False,
        )


class CatalogueWorkflowTests(WorkbenchTestCase):
    def setUp(self):
        self.user = self.create_user(superuser=True)
        self.login(self.user)

    def track_formset_data(self, release, rows):
        data = {
            "tracks-TOTAL_FORMS": "5",
            "tracks-INITIAL_FORMS": "0",
            "tracks-MIN_NUM_FORMS": "0",
            "tracks-MAX_NUM_FORMS": "25",
        }
        fields = (
            "recording",
            "new_recording_title",
            "new_isrc",
            "artist_identity",
            "disc_number",
            "side",
            "track_number",
            "sequence_number",
            "title_override",
            "duration_ms",
            "force_create",
        )
        for index in range(5):
            row = rows[index] if index < len(rows) else {}
            for field in fields:
                data[f"tracks-{index}-{field}"] = row.get(field, "")
        return data

    def test_filtered_release_list_is_preserved_in_detail_return_link(self):
        release = Release.objects.create(title="Nordlys", catalogue_number="LYN-1")
        response = self.client.get(reverse("workbench:releases") + "?q=LYN-1")
        self.assertContains(response, "Nordlys")
        self.assertContains(response, "return=/arbeid/utgivelser/%3Fq%3DLYN-1")
        response = self.client.get(
            reverse("workbench:release", args=(release.pk,))
            + "?return=%2Farbeid%2Futgivelser%2F%3Fq%3DLYN-1"
        )
        self.assertContains(response, "/arbeid/utgivelser/?q=LYN-1")

    def test_release_track_and_radio_forms_preserve_work_context(self):
        release = Release.objects.create(title="Arbeidskontekst")
        recording = Recording.objects.create(title="Radio")
        MusicLibraryEntry.objects.create(recording=recording)
        release_detail = reverse("workbench:release", args=(release.pk,))
        filtered_list = reverse("workbench:releases") + "?q=Arbeid"
        response = self.client.get(
            release_detail + "?return=" + quote(filtered_list, safe="")
        )
        expected_tracks = reverse("workbench:release_tracks", args=(release.pk,))
        self.assertContains(response, expected_tracks)

        recording_detail = (
            reverse("workbench:recording", args=(recording.pk,))
            + "?fane=radio&return="
            + quote(reverse("workbench:library") + "?q=Radio", safe="")
        )
        response = self.client.get(recording_detail)
        radio_edit = reverse("workbench:radio_edit", args=(recording.pk,))
        self.assertContains(response, radio_edit)
        response = self.client.get(
            radio_edit + "?return=" + quote(recording_detail, safe="")
        )
        self.assertContains(response, 'name="return"')
        self.assertContains(response, "fane=radio")

    def test_multiple_tracks_are_atomic_and_existing_recording_keeps_radio_data(self):
        release = Release.objects.create(title="Album")
        existing = Recording.objects.create(title="Gjenbruk meg")
        library = MusicLibraryEntry.objects.create(recording=existing, genre="Pop")
        data = self.track_formset_data(
            release,
            [
                {
                    "recording": str(existing.pk),
                    "sequence_number": "1",
                    "track_number": "1",
                },
                {
                    "new_recording_title": "Ny master",
                    "sequence_number": "2",
                    "track_number": "2",
                },
            ],
        )
        response = self.client.post(
            reverse("workbench:release_tracks", args=(release.pk,)), data
        )
        self.assertRedirects(
            response,
            reverse("workbench:release", args=(release.pk,)),
            fetch_redirect_response=False,
        )
        self.assertEqual(ReleaseTrack.objects.filter(release=release).count(), 2)
        self.assertEqual(Recording.objects.count(), 2)
        library.refresh_from_db()
        self.assertEqual(library.genre, "Pop")

    def test_duplicate_sequence_rolls_back_every_track_and_preserves_input(self):
        release = Release.objects.create(title="Album")
        data = self.track_formset_data(
            release,
            [
                {"new_recording_title": "Første", "sequence_number": "1"},
                {"new_recording_title": "Andre", "sequence_number": "1"},
            ],
        )
        response = self.client.post(
            reverse("workbench:release_tracks", args=(release.pk,)), data
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Første")
        self.assertContains(response, "Andre")
        self.assertContains(response, "må være unik")
        self.assertEqual(ReleaseTrack.objects.count(), 0)
        self.assertEqual(Recording.objects.count(), 0)

    def test_recording_autocomplete_is_bounded_and_does_not_render_full_catalogue(self):
        recordings = [
            Recording.objects.create(title=f"Søkbar {index:02}") for index in range(25)
        ]
        release = Release.objects.create(title="Søk")
        response = self.client.get(
            reverse("workbench:release_tracks", args=(release.pk,))
        )
        self.assertNotContains(response, recordings[0].title)
        response = self.client.get(reverse("workbench:recording_search") + "?q=Søkbar")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 20)

    def test_library_list_prefetches_related_catalogue_data(self):
        for index in range(10):
            MusicLibraryEntry.objects.create(
                recording=Recording.objects.create(title=f"Innspilling {index}")
            )
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("workbench:library"))
            self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 12)


class ProvenanceApplicationTests(WorkbenchTestCase):
    def setUp(self):
        self.user = self.create_user(superuser=True)
        self.login(self.user)

    def test_confirm_and_apply_title_is_atomic_and_audited(self):
        recording = Recording.objects.create(title="Gammel tittel")
        assertion = self.assertion(recording, field_name="title", value="Ny tittel")
        original_uuid = recording.pk
        response = self.client.post(
            reverse("workbench:assertion_action", args=(assertion.pk,)),
            {
                "action": "confirm_apply",
                "expected_revision": recording.revision,
                "note": "Kontrollert mot cover",
                "return": reverse("workbench:recording", args=(recording.pk,))
                + "?fane=sources",
            },
        )
        self.assertEqual(response.status_code, 302)
        recording.refresh_from_db()
        assertion.refresh_from_db()
        change = AppliedMetadataChange.objects.get()
        self.assertEqual(recording.pk, original_uuid)
        self.assertEqual(recording.title, "Ny tittel")
        self.assertEqual(assertion.status, VerificationStatus.CONFIRMED)
        self.assertEqual(change.before_value, "Gammel tittel")
        self.assertEqual(change.after_value, "Ny tittel")
        self.assertEqual(change.changed_by, self.user)
        self.assertEqual(change.assertion.source_record.source_system.name, "LP-cover")

    def test_invalid_language_preserves_catalogue_and_assertion(self):
        recording = Recording.objects.create(title="Språktest", language="nb")
        assertion = self.assertion(
            recording, field_name="language", value="ugyldig språk!"
        )
        with self.assertRaises(ValidationError):
            apply_assertion(
                assertion,
                expected_revision=recording.revision,
                user=self.user,
                confirm=True,
            )
        recording.refresh_from_db()
        assertion.refresh_from_db()
        self.assertEqual(recording.language, "nb")
        self.assertEqual(assertion.status, VerificationStatus.UNVERIFIED)
        self.assertFalse(AppliedMetadataChange.objects.exists())

    def test_concurrent_change_is_not_overwritten(self):
        recording = Recording.objects.create(title="Før")
        assertion = self.assertion(recording, field_name="title", value="Fra kilde")
        stale_revision = recording.revision
        recording.title = "Redigert av annen bruker"
        recording.save()
        with self.assertRaises(ConcurrentCatalogueChange):
            apply_assertion(
                assertion,
                expected_revision=stale_revision,
                user=self.user,
            )
        recording.refresh_from_db()
        self.assertEqual(recording.title, "Redigert av annen bruker")
        self.assertFalse(AppliedMetadataChange.objects.exists())

    def test_unsupported_field_cannot_be_applied(self):
        recording = Recording.objects.create(title="Test")
        assertion = self.assertion(recording, field_name="duration_ms", value="42")
        with self.assertRaisesMessage(ValueError, "kan ikke brukes"):
            apply_assertion(
                assertion,
                expected_revision=recording.revision,
                user=self.user,
            )

    def test_correction_preserves_original_and_records_reviewer(self):
        recording = Recording.objects.create(title="Original")
        assertion = self.assertion(recording, field_name="title", value="Feil verdi")
        response = self.client.post(
            reverse("workbench:assertion_action", args=(assertion.pk,)),
            {
                "action": "correct",
                "correction": "Korrigert verdi",
                "note": "Kontrollert på nytt",
                "return": reverse("workbench:recording", args=(recording.pk,))
                + "?fane=sources",
            },
        )
        self.assertEqual(response.status_code, 302)
        assertion.refresh_from_db()
        replacement = MetadataAssertion.objects.get(supersedes=assertion)
        decision = assertion.decisions.get()
        self.assertEqual(assertion.raw_value, "Feil verdi")
        self.assertEqual(assertion.status, VerificationStatus.SUPERSEDED)
        self.assertEqual(replacement.raw_value, "Korrigert verdi")
        self.assertEqual(replacement.status, VerificationStatus.UNVERIFIED)
        self.assertEqual(decision.decided_by, self.user)


class FileWorkspaceTests(WorkbenchTestCase):
    def test_file_page_distinguishes_reference_from_verified_presence(self):
        user = self.create_user(superuser=True)
        self.login(user)
        asset = FileAsset.objects.create(filename="radio.flac", role="radio_flac")
        FileLocation.objects.create(
            asset=asset,
            storage_type="nas",
            relative_path="Mudi/Album/radio.flac",
        )
        response = self.client.get(reverse("workbench:file", args=(asset.pk,)))
        self.assertContains(response, "Registrert referanse")
        self.assertContains(response, "plassering ikke kontrollert automatisk")
        self.assertContains(response, "Kopier sti")
        self.assertNotContains(response, "file://")

    def test_verified_location_is_explicit(self):
        user = self.create_user(superuser=True)
        self.login(user)
        asset = FileAsset.objects.create(
            filename="master.wav", role="edited_wav_master"
        )
        FileLocation.objects.create(
            asset=asset,
            storage_type="nas",
            relative_path="Master/master.wav",
            verification_status=FileLocation.VerificationStatus.VERIFIED,
        )
        response = self.client.get(reverse("workbench:file", args=(asset.pk,)))
        self.assertContains(response, "Kontrollert plassering")


class CatalogueInspectorTests(WorkbenchTestCase):
    def test_filter_selection_and_return_context(self):
        self.login(self.create_user(superuser=True))
        recording = Recording.objects.create(title="Blå kveld")
        MusicLibraryEntry.objects.create(
            recording=recording, genre="Gospel", language="nb"
        )
        response = self.client.get(
            reverse("workbench:library"),
            {
                "q": "Blå",
                "genre": "Gospel",
                "language": "nb",
                "selected": str(recording.pk),
            },
        )
        self.assertEqual(response.context["selected_entry"].recording_id, recording.pk)
        self.assertContains(response, "preview-radio")
        self.assertContains(response, "q%3DBl")
        self.assertIsNone(
            self.client.get(reverse("workbench:library"), {"selected": "none"}).context[
                "selected_entry"
            ]
        )
        self.assertEqual(
            self.client.get(reverse("workbench:library"), {"genre": "Jazz"})
            .context["page"]
            .paginator.count,
            0,
        )

    def test_cover_permissions_and_confined_raster_preview(self):
        import tempfile
        from pathlib import Path
        from PIL import Image

        asset = FileAsset.objects.create(filename="cover.png", role="cover_image")
        location = FileLocation.objects.create(
            asset=asset, storage_type="nas", relative_path="cover.png", status="active"
        )
        url = reverse("workbench:cover_image", args=[asset.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.login(self.create_user())
        self.assertEqual(self.client.get(url).status_code, 403)
        self.login(self.create_user(username="cover-admin", superuser=True))
        with tempfile.TemporaryDirectory() as folder, self.settings(P7_NAS_ROOT=folder):
            Image.new("RGB", (20, 20)).save(Path(folder) / "cover.png")
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "image/jpeg")
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE media_assets_filelocation SET relative_path = %s WHERE id = %s",
                    [
                        "../outside.png",
                        (
                            location.pk.hex
                            if connection.vendor == "sqlite"
                            else location.pk
                        ),
                    ],
                )
            self.assertEqual(self.client.get(url).status_code, 404)
