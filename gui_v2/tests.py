import tempfile
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from mutagen.flac import FLAC

from catalogue.models import (
    DuplicateCandidate,
    ExternalIdentifier,
    Recording,
    RecordingContribution,
    Release,
    ReleaseTrack,
)
from managed_music.models import ManagedRecording
from media_assets.models import FileAsset, FileLocation
from music_library.models import (
    Channel,
    MusicLibraryChannel,
    MusicLibraryEntry,
)
from parties.models import Party
from rights.models import RightsClaim, RightsConfiguration


class GuiV2WorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="prototype-user", password="test-password"
        )
        self.release = Release.objects.create(title="Testutgivelse")
        self.recording = Recording.objects.create(
            title="Eksisterende innspilling"
        )
        self.entry = MusicLibraryEntry.objects.create(
            recording=self.recording, genre="Pop", energy=3
        )
        self.channel = Channel.objects.create(code="p7-test", name="P7 Test")
        MusicLibraryChannel.objects.create(
            library_entry=self.entry, channel=self.channel
        )

    def _superuser(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)

    def test_login_redirect_keeps_requested_v2_url(self):
        requested = reverse("gui_v2:music_library")
        response = self.client.get(requested)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            parse_qs(urlparse(response.url).query)["next"], [requested]
        )

    def test_sections_enforce_view_permissions(self):
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(reverse("gui_v2:home")).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse("gui_v2:music_library")).status_code, 403
        )
        self.assertEqual(
            self.client.get(reverse("gui_v2:release_list")).status_code, 403
        )
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="music_library",
                codename="view_musiclibraryentry",
            ),
            Permission.objects.get(
                content_type__app_label="catalogue", codename="view_release"
            ),
        )
        self.client.force_login(get_user_model().objects.get(pk=self.user.pk))
        self.assertContains(
            self.client.get(reverse("gui_v2:music_library")),
            "Eksisterende innspilling",
        )
        self.assertContains(
            self.client.get(reverse("gui_v2:release_list")), "Testutgivelse"
        )

    def test_library_filter_any_all_and_selection(self):
        other = Channel.objects.create(code="annen", name="Annen")
        self._superuser()
        response = self.client.get(
            reverse("gui_v2:music_library"),
            {
                "channels": [self.channel.pk, other.pk],
                "channel_mode": "any",
                "selected": self.entry.pk,
            },
        )
        self.assertContains(response, "Eksisterende innspilling")
        response = self.client.get(
            reverse("gui_v2:music_library"),
            {"channels": [self.channel.pk, other.pk], "channel_mode": "all"},
        )
        self.assertContains(response, "Ingen innspillinger passer")

    def test_library_can_filter_recordings_with_multiple_flacs_and_an_isrc(
        self,
    ):
        self._superuser()
        ExternalIdentifier.objects.create(
            recording=self.recording,
            scheme=ExternalIdentifier.Scheme.ISRC,
            value="NO-P7T-26-00421",
        )
        for number in range(2):
            FileAsset.objects.create(
                recording=self.recording,
                filename=f"kollisjon-{number}.flac",
                role=FileAsset.Role.RADIO_FLAC,
            )
        ordinary = Recording.objects.create(title="Bare én fil")
        MusicLibraryEntry.objects.create(recording=ordinary)
        ExternalIdentifier.objects.create(
            recording=ordinary,
            scheme=ExternalIdentifier.Scheme.ISRC,
            value="NO-P7T-26-00422",
        )
        FileAsset.objects.create(
            recording=ordinary,
            filename="vanlig.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )

        response = self.client.get(
            reverse("gui_v2:music_library"),
            {"isrc_file_collision": "on", "selected": self.entry.pk},
        )

        self.assertEqual(
            list(response.context["page"].object_list), [self.entry]
        )
        self.assertContains(response, "Flere radio-FLAC med samme ISRC")
        self.assertContains(response, 'name="isrc_file_collision"')

    def test_library_whole_row_selects_inspector_content(self):
        self._superuser()
        response = self.client.get(
            reverse("gui_v2:music_library"), {"selected": self.entry.pk}
        )

        self.assertContains(response, "data-library-row")
        self.assertContains(response, "data-row-href=")
        self.assertNotContains(response, 'class="row-link"')
        self.assertContains(response, 'id="v2-inspector"')

    def test_library_inspector_uses_deterministic_cover_for_selected_recording(
        self,
    ):
        self._superuser()
        newer = Release.objects.create(
            title="Nyere utgivelse", release_year=2020
        )
        older = Release.objects.create(
            title="Eldre utgivelse", release_year=1990
        )
        ReleaseTrack.objects.create(
            release=newer, recording=self.recording, sequence_number=1
        )
        ReleaseTrack.objects.create(
            release=older, recording=self.recording, sequence_number=1
        )
        newer_cover = FileAsset.objects.create(
            release=newer,
            filename="cover.png",
            role=FileAsset.Role.COVER_IMAGE,
        )
        older_cover = FileAsset.objects.create(
            release=older,
            filename="cover.png",
            role=FileAsset.Role.COVER_IMAGE,
        )
        for cover, path in (
            (newer_cover, "covers/newer.png"),
            (older_cover, "covers/older.png"),
        ):
            FileLocation.objects.create(
                asset=cover,
                storage_type=FileLocation.StorageType.NAS,
                relative_path=path,
                status=FileLocation.Status.ACTIVE,
            )

        response = self.client.get(
            reverse("gui_v2:music_library"), {"selected": self.entry.pk}
        )

        self.assertEqual(
            response.context["selected"].cover["asset"], older_cover
        )
        self.assertContains(
            response, reverse("workbench:cover_image", args=[older_cover.pk])
        )
        self.assertNotContains(
            response, reverse("workbench:cover_image", args=[newer_cover.pk])
        )
        self.assertContains(response, "Omslag fra Eldre utgivelse")
        self.assertContains(response, 'class="library-cover-thumb"')
        self.assertContains(response, 'class="library-inspector-header"')
        self.assertContains(response, "data-toggle-library-inspector")
        self.assertContains(response, "Skjul detaljer")
        self.assertNotContains(response, 'class="inspector-hide"')

        fragment = self.client.get(
            reverse("gui_v2:music_library"),
            {"selected": self.entry.pk},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(fragment.status_code, 200)
        self.assertContains(fragment, 'id="v2-inspector"')
        self.assertContains(fragment, self.recording.title)
        self.assertNotContains(fragment, 'class="library-table"')

    def test_library_opens_gui_v2_recording_overview_with_return_context(self):
        self._superuser()
        response = self.client.get(
            reverse("gui_v2:music_library"),
            {"q": "Eksisterende", "selected": self.entry.pk},
        )
        detail_url = reverse(
            "gui_v2:recording_detail", args=[self.recording.pk]
        )
        self.assertContains(response, detail_url)
        self.assertNotContains(
            response,
            f'href="{reverse("workbench:recording", args=[self.recording.pk])}?return=',
        )

    def test_recording_overview_requires_recording_view_permission(self):
        self.client.force_login(self.user)
        url = reverse("gui_v2:recording_detail", args=[self.recording.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="catalogue", codename="view_recording"
            )
        )
        self.client.force_login(get_user_model().objects.get(pk=self.user.pk))
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_release_track_inspector_opens_gui_v2_recording_overview(self):
        self._superuser()
        ReleaseTrack.objects.create(
            release=self.release, recording=self.recording, sequence_number=1
        )
        response = self.client.get(
            reverse("gui_v2:release_detail", args=[self.release.pk])
        )
        self.assertContains(
            response,
            reverse("gui_v2:recording_detail", args=[self.recording.pk]),
        )

    def test_recording_overview_shows_identity_radio_release_and_managed_status(
        self,
    ):
        self._superuser()
        self.entry.language = "nb"
        self.entry.rotation_suitability = (
            MusicLibraryEntry.RotationSuitability.SUITABLE
        )
        self.entry.save()
        ManagedRecording.objects.create(
            library_entry=self.entry, status=ManagedRecording.Status.ACTIVE
        )
        RecordingContribution.objects.create(
            recording=self.recording,
            role=RecordingContribution.Role.PRIMARY,
            credited_as="Kreditert artist",
        )
        RecordingContribution.objects.create(
            recording=self.recording,
            role=RecordingContribution.Role.COMPOSER,
            credited_as="Uavklart komponist",
        )
        ExternalIdentifier.objects.create(
            recording=self.recording,
            scheme=ExternalIdentifier.Scheme.ISRC,
            value="NO-P7T-26-00601",
        )
        self.release.release_type = Release.Type.CD
        self.release.release_year = 2026
        self.release.catalogue_number = "P7-601"
        self.release.save()
        track = ReleaseTrack.objects.create(
            release=self.release,
            recording=self.recording,
            sequence_number=1,
            track_number=1,
        )
        cover = FileAsset.objects.create(
            release=self.release,
            filename="cover.png",
            role=FileAsset.Role.COVER_IMAGE,
        )
        FileLocation.objects.create(
            asset=cover,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="covers/overview.png",
            status=FileLocation.Status.ACTIVE,
        )
        radio = FileAsset.objects.create(
            recording=self.recording,
            filename="radio.flac",
            role=FileAsset.Role.RADIO_FLAC,
            mime_type="audio/flac",
            technical_metadata={
                "sample_rate": 48000,
                "bits_per_sample": 24,
                "channels": 2,
                "duration_ms": 183000,
            },
        )
        FileLocation.objects.create(
            asset=radio,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="radio/radio.flac",
            status=FileLocation.Status.ACTIVE,
        )

        response = self.client.get(
            reverse("gui_v2:recording_detail", args=[self.recording.pk]),
            {"return": reverse("gui_v2:music_library") + "?genre=Pop"},
        )

        self.assertEqual(response.status_code, 200)
        for text in (
            "Eksisterende innspilling",
            "Kreditert artist",
            "NOP7T2600601",
            "Katalogtilhørighet",
            "Aktiv forvaltning",
            "Forvaltet betyr ikke",
            "Radiometadata",
            "Norsk bokmål",
            "P7 Test",
            "Utgivelsesforekomster (1)",
            "Testutgivelse",
            "radio.flac",
            "48 kHz",
            "24 bit",
            "2 (stereo)",
            "Identitet ikke avklart",
        ):
            self.assertContains(response, text)
        self.assertContains(
            response, reverse("gui_v2:release_detail", args=[self.release.pk])
        )
        self.assertContains(
            response, reverse("workbench:cover_image", args=[cover.pk])
        )
        self.assertContains(response, "genre%3DPop")
        self.assertContains(response, "Ingen åpne oppgaver")
        self.assertNotContains(response, "Krever oppfølging")
        self.assertNotContains(
            response, "Kreditering trenger identitetsavklaring"
        )
        self.assertEqual(
            response.context["overview"]["releases"][0].pk, track.pk
        )

    def test_recording_overview_does_not_choose_between_multiple_radio_files(
        self,
    ):
        self._superuser()
        for number in range(2):
            FileAsset.objects.create(
                recording=self.recording,
                filename=f"alternativ-{number}.flac",
                role=FileAsset.Role.RADIO_FLAC,
            )
        response = self.client.get(
            reverse("gui_v2:recording_detail", args=[self.recording.pk])
        )
        self.assertContains(response, "2 radiofiler registrert")
        self.assertContains(response, "Ingen fil velges automatisk")
        self.assertNotContains(response, "alternativ-0.flac")
        self.assertIsNone(response.context["overview"]["single_radio_file"])

    def test_recording_overview_handles_missing_file_and_empty_recording(self):
        self._superuser()
        asset = FileAsset.objects.create(
            recording=self.recording,
            filename="savnet.flac",
            role=FileAsset.Role.RADIO_FLAC,
            sync_status=FileAsset.SyncStatus.MISSING,
        )
        FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="savnet/savnet.flac",
            status=FileLocation.Status.MISSING,
        )
        response = self.client.get(
            reverse("gui_v2:recording_detail", args=[self.recording.pk])
        )
        self.assertContains(response, "Radiofilen er ikke tilgjengelig")
        self.assertContains(
            response, "Innspillingen og katalogdataene er fortsatt bevart"
        )

        empty = Recording.objects.create(title="Bare en katalogpost")
        response = self.client.get(
            reverse("gui_v2:recording_detail", args=[empty.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bare en katalogpost")
        self.assertContains(
            response, "Ingen utgivelsesforekomster er registrert"
        )

    def test_recording_overview_shows_known_isrc_collision_as_resolved(self):
        self._superuser()
        other = Recording.objects.create(title="Annen innspilling")
        DuplicateCandidate.objects.create(
            recording_a=self.recording,
            recording_b=other,
            signals=["reported_isrc_collision", "manual_separate_recordings"],
            score=100,
            status=DuplicateCandidate.Status.DISMISSED,
            notes="Kontrollert og holdes adskilt.",
        )
        response = self.client.get(
            reverse("gui_v2:recording_detail", args=[self.recording.pk])
        )
        self.assertContains(response, "Kjent ISRC-kollisjon · avklart")
        self.assertContains(response, "Innspillingene skal holdes adskilt")
        self.assertNotContains(response, "ISRC-kollisjon må vurderes")

    def test_recording_overview_get_is_read_only_for_media_and_database(self):
        self._superuser()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "overview.flac"
            path.write_bytes(b"recording overview must be read only")
            asset = FileAsset.objects.create(
                recording=self.recording,
                filename=path.name,
                role=FileAsset.Role.RADIO_FLAC,
            )
            FileLocation.objects.create(
                asset=asset,
                storage_type=FileLocation.StorageType.NAS,
                relative_path=path.name,
            )
            before_file = (path.read_bytes(), path.stat().st_mtime_ns)
            before_revision = self.recording.revision
            with override_settings(P7_MUSIC_ROOT=root, P7_NAS_ROOT=root):
                response = self.client.get(
                    reverse(
                        "gui_v2:recording_detail", args=[self.recording.pk]
                    )
                )
            self.recording.refresh_from_db()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                (path.read_bytes(), path.stat().st_mtime_ns), before_file
            )
            self.assertEqual(self.recording.revision, before_revision)

    def test_library_renders_channel_choices_and_serves_custom_logo(self):
        self._superuser()
        built_in = Channel.objects.create(code="p7_riks", name="P7 Riks")
        response = self.client.get(
            reverse("gui_v2:music_library"),
            {"channels": [built_in.pk]},
        )
        self.assertContains(response, "P7 Riks")
        self.assertContains(response, 'name="channels"')
        self.assertContains(response, "checked")

        with tempfile.TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root
        ):
            self.channel.logo.save(
                "p7-test.png", ContentFile(b"custom-channel-logo")
            )
            response = self.client.get(reverse("gui_v2:music_library"))
            self.assertContains(
                response,
                reverse("gui_v2:channel_logo", args=[self.channel.pk]),
            )
            response = self.client.get(
                reverse("gui_v2:channel_logo", args=[self.channel.pk])
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                b"".join(response.streaming_content), b"custom-channel-logo"
            )

    def test_library_orders_channel_filters_by_usage_then_name(self):
        self._superuser()
        popular = Channel.objects.create(code="popular", name="Mest brukt")
        alphabetical = Channel.objects.create(
            code="alphabetical", name="Alfabetisk"
        )
        last_alphabetically = Channel.objects.create(code="zulu", name="Zulu")
        second_recording = Recording.objects.create(title="Andre innspilling")
        second_entry = MusicLibraryEntry.objects.create(
            recording=second_recording
        )
        MusicLibraryChannel.objects.create(
            library_entry=self.entry, channel=popular
        )
        MusicLibraryChannel.objects.create(
            library_entry=second_entry, channel=popular
        )

        response = self.client.get(reverse("gui_v2:music_library"))
        channels = response.context["channel_filter_options"]

        self.assertEqual(channels[0], popular)
        self.assertLess(
            channels.index(self.channel), channels.index(alphabetical)
        )
        self.assertLess(
            channels.index(alphabetical), channels.index(last_alphabetically)
        )

    def test_library_supports_user_selected_page_size_and_automatic_filters(
        self,
    ):
        self._superuser()
        second_recording = Recording.objects.create(title="Andre innspilling")
        MusicLibraryEntry.objects.create(recording=second_recording)

        response = self.client.get(
            reverse("gui_v2:music_library"),
            {"per_page": "all", "genre": "Pop"},
        )

        self.assertEqual(response.context["page_size"], "all")
        self.assertEqual(response.context["page"].paginator.per_page, 1)
        self.assertContains(response, "data-auto-submit-filters")
        self.assertContains(response, "data-filter-direction")
        self.assertContains(response, "data-filter-label")
        self.assertContains(response, "Skjul filtre")
        self.assertContains(response, 'class="filter-toggle"')
        self.assertGreater(
            response.content.index(b'class="filter-toggle"'),
            response.content.index(b'class="list-heading"'),
        )
        self.assertContains(response, 'class="player library-player"')
        self.assertNotContains(response, 'class="player header-player"')
        self.assertContains(response, 'class="library-player-dock"')
        self.assertNotContains(response, '<footer class="player"')
        self.assertNotContains(response, "Bruk filtre")
        self.assertNotContains(response, "Radiomusikk")
        self.assertEqual(
            response.content.count(b'aria-label="Nullstill alle filtre"'), 1
        )
        self.assertNotContains(response, ">Nullstill</a>")
        self.assertContains(
            response, '<option value="all" selected>Alle</option>', html=True
        )

        home_response = self.client.get(reverse("gui_v2:home"))
        self.assertContains(home_response, 'class="player header-player"')
        self.assertNotContains(home_response, 'class="player library-player"')

    def test_library_uses_table_headers_for_sorting_and_keeps_filters(self):
        self._superuser()
        second_recording = Recording.objects.create(title="Andre innspilling")
        MusicLibraryEntry.objects.create(
            recording=second_recording, genre="Jazz"
        )

        response = self.client.get(
            reverse("gui_v2:music_library"),
            {"ordering": "-title", "per_page": "40"},
        )

        self.assertEqual(response.context["current_ordering"], "-title")
        self.assertEqual(
            response.context["page"].object_list[0].recording, self.recording
        )
        self.assertContains(response, 'class="sort-header"')
        self.assertContains(response, "ordering=title")
        self.assertNotContains(response, 'name="ordering" id="id_ordering"')
        self.assertNotContains(response, "data-column-filter-form")
        self.assertNotContains(response, "data-column-filter-trigger")
        self.assertContains(response, '<select name="genre"')
        self.assertContains(response, 'type="checkbox" name="channels"')
        self.assertContains(response, "↕", count=13)
        for ordering in (
            "channels",
            "targets",
            "file_status",
            "managed",
            "follow_up",
        ):
            sorted_response = self.client.get(
                reverse("gui_v2:music_library"), {"ordering": ordering}
            )
            self.assertEqual(sorted_response.status_code, 200)
            self.assertEqual(
                sorted_response.context["current_ordering"], ordering
            )

    def test_library_places_page_navigation_beside_heading(self):
        self._superuser()
        for number in range(40):
            recording = Recording.objects.create(
                title=f"Innspilling {number:02d}"
            )
            MusicLibraryEntry.objects.create(recording=recording)

        response = self.client.get(reverse("gui_v2:music_library"))
        content = response.content

        self.assertEqual(response.context["page"].paginator.num_pages, 2)
        self.assertEqual(content.count(b"pagination pagination-top"), 1)
        self.assertLess(
            content.index(b"pagination pagination-top"),
            content.index(b"<table"),
        )

    def test_channel_logo_requires_library_permission(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root
        ):
            self.channel.logo.save(
                "p7-test.png", ContentFile(b"custom-channel-logo")
            )
            self.client.force_login(self.user)
            response = self.client.get(
                reverse("gui_v2:channel_logo", args=[self.channel.pk])
            )
            self.assertEqual(response.status_code, 403)

    def test_channel_logo_can_be_configured_in_admin(self):
        self._superuser()
        response = self.client.get(
            reverse(
                "admin:music_library_channel_change", args=[self.channel.pk]
            )
        )
        self.assertContains(response, 'name="logo"')

    def test_library_uses_observed_genre_and_named_language_filters(self):
        self._superuser()
        self.entry.genre = "Pop; Evangelisk"
        self.entry.language = "no"
        self.entry.save(update_fields=("genre", "language"))
        second_recording = Recording.objects.create(
            title="En annen norsk innspilling"
        )
        MusicLibraryEntry.objects.create(
            recording=second_recording, genre="Pop", language="no"
        )

        response = self.client.get(reverse("gui_v2:music_library"))
        self.assertContains(
            response,
            '<option value="Evangelisk">Evangelisk</option>',
            html=True,
        )
        self.assertContains(
            response, '<option value="Pop">Pop</option>', html=True
        )
        self.assertContains(
            response, '<option value="no">Norsk</option>', html=True
        )
        self.assertEqual(
            response.content.count(b'<option value="no">Norsk</option>'), 1
        )
        self.assertNotContains(
            response, 'type="checkbox" name="language" value="no"'
        )
        self.assertContains(response, "data-library-search")

        filtered = self.client.get(
            reverse("gui_v2:music_library"), {"genre": "Evangelisk"}
        )
        self.assertContains(filtered, "Eksisterende innspilling")
        self.assertContains(filtered, "Sjanger: Evangelisk")

        jazz_recording = Recording.objects.create(title="Jazzinnspilling")
        MusicLibraryEntry.objects.create(
            recording=jazz_recording, genre="Jazz"
        )
        multi_filtered = self.client.get(
            reverse("gui_v2:music_library"), {"genre": ["Evangelisk", "Jazz"]}
        )
        self.assertContains(multi_filtered, "Eksisterende innspilling")
        self.assertContains(multi_filtered, "Jazzinnspilling")
        self.assertContains(multi_filtered, "Sjanger: Evangelisk, Jazz")

    def test_rotation_filter_derives_suitable_from_channel_membership(self):
        self._superuser()
        response = self.client.get(
            reverse("gui_v2:music_library"),
            {
                "rotation_suitability": MusicLibraryEntry.RotationSuitability.SUITABLE
            },
        )
        self.assertContains(response, "Eksisterende innspilling")
        self.assertContains(response, "Rotasjonsverdig")

        self.entry.rotation_suitability = (
            MusicLibraryEntry.RotationSuitability.NOT_SUITABLE
        )
        self.entry.save(update_fields=("rotation_suitability",))
        response = self.client.get(
            reverse("gui_v2:music_library"),
            {
                "rotation_suitability": MusicLibraryEntry.RotationSuitability.SUITABLE
            },
        )
        self.assertContains(response, "Ingen innspillinger passer")
        response = self.client.get(
            reverse("gui_v2:music_library"),
            {
                "rotation_suitability": MusicLibraryEntry.RotationSuitability.NOT_SUITABLE
            },
        )
        self.assertContains(response, "Ikke rotasjonsverdig")

        self.entry.rotation_suitability = (
            MusicLibraryEntry.RotationSuitability.UNASSESSED
        )
        self.entry.save(update_fields=("rotation_suitability",))
        response = self.client.get(
            reverse("gui_v2:music_library"),
            {
                "rotation_suitability": MusicLibraryEntry.RotationSuitability.SUITABLE
            },
        )
        self.assertContains(response, "Ingen innspillinger passer")
        response = self.client.get(
            reverse("gui_v2:music_library"),
            {
                "rotation_suitability": MusicLibraryEntry.RotationSuitability.UNASSESSED
            },
        )
        self.assertContains(response, "Ikke vurdert")

    def test_library_and_grid_render_keyboard_workbench(self):
        self._superuser()
        self.entry.rotation_suitability = (
            MusicLibraryEntry.RotationSuitability.NOT_SUITABLE
        )
        self.entry.save(update_fields=("rotation_suitability",))
        library = self.client.get(
            reverse("gui_v2:music_library"), {"genre": "Pop"}
        )
        self.assertContains(library, "Sjanger: Pop")
        self.assertContains(library, "data-row-href")
        self.assertContains(library, "data-library-row")
        self.assertContains(library, "Krever oppfølging")
        self.assertContains(library, "Ikke rotasjonsverdig")
        self.assertContains(library, 'id="v2-inspector"')
        grid = self.client.get(
            reverse("gui_v2:release_detail", args=[self.release.pk])
        )
        self.assertContains(grid, 'role="grid"')
        self.assertContains(grid, 'data-field="recording_title"')

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_release_can_be_created_before_tracks_are_registered(self):
        self._superuser()
        response = self.client.post(
            reverse("gui_v2:release_list"),
            {
                "release-title": "Ny testutgivelse",
                "release-release_type": "",
                "release-release_date": "",
                "release-release_year": "",
                "release-label": "",
                "release-catalogue_number": "",
                "release-verification_status": "unverified",
                "release-notes": "",
                "release-barcode": "",
            },
        )
        created = Release.objects.get(title="Ny testutgivelse")
        self.assertRedirects(
            response,
            reverse("gui_v2:release_detail", args=[created.pk]),
        )
        self.assertFalse(created.tracks.exists())

    @override_settings(GUI_V2_WRITES_ENABLED=False)
    def test_release_creation_is_blocked_in_read_only_mode(self):
        self._superuser()
        response = self.client.post(
            reverse("gui_v2:release_list"),
            {"release-title": "Skal ikke opprettes"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            Release.objects.filter(title="Skal ikke opprettes").exists()
        )

    def test_release_tabs_integrate_cover_details_files_and_rights(self):
        self._superuser()
        cover = FileAsset.objects.create(
            release=self.release,
            filename="cover.png",
            role=FileAsset.Role.COVER_IMAGE,
        )
        FileLocation.objects.create(
            asset=cover,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="covers/cover.png",
            status=FileLocation.Status.ACTIVE,
        )
        detail_url = reverse("gui_v2:release_detail", args=[self.release.pk])
        response = self.client.get(detail_url)
        self.assertContains(
            response, reverse("workbench:cover_image", args=[cover.pk])
        )
        for label in (
            "Sporliste",
            "Utgivelsesdetaljer",
            "Filer og kilder",
            "Rettigheter",
        ):
            self.assertContains(response, label)
        self.assertNotContains(
            response, reverse("workbench:release", args=[self.release.pk])
        )
        details = self.client.get(detail_url, {"tab": "details"})
        self.assertContains(details, 'name="release-title"')
        files = self.client.get(detail_url, {"tab": "files"})
        self.assertContains(files, "cover.png")
        rights = self.client.get(detail_url, {"tab": "rights"})
        self.assertContains(
            rights, "Registrer rettigheter for valgte innspillinger"
        )
        self.assertContains(rights, 'id="id_rights-recordings"')

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_integrated_rights_tab_creates_claim_on_selected_recording(self):
        self._superuser()
        local = Party.objects.create(
            name="Lokal testorganisasjon", kind=Party.Kind.ORGANIZATION
        )
        RightsConfiguration.objects.create(local_organization=local)
        ReleaseTrack.objects.create(
            release=self.release, recording=self.recording, sequence_number=1
        )
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]),
            {
                "action": "rights",
                "tab": "rights",
                "rights-recordings": str(self.recording.pk),
                "rights-right_type": RightsClaim.RightType.DISTRIBUTION,
                "rights-rights_holder": str(local.pk),
                "rights-grantor": "",
                "rights-share": "",
                "rights-territory_mode": RightsClaim.TerritoryMode.WORLD,
                "rights-valid_from": "",
                "rights-valid_until": "",
                "rights-evidence_strength": RightsClaim.EvidenceStrength.NOT_ASSESSED,
                "rights-source_record": "",
                "rights-agreement": "",
                "rights-notes": "Testgrunnlag",
            },
        )
        self.assertRedirects(
            response,
            f"{reverse('gui_v2:release_detail', args=[self.release.pk])}?tab=rights",
        )
        claim = RightsClaim.objects.get()
        self.assertEqual(claim.recording, self.recording)
        self.assertEqual(claim.right_type, RightsClaim.RightType.DISTRIBUTION)

    def test_recording_search_matches_credited_artist(self):
        RecordingContribution.objects.create(
            recording=self.recording,
            role=RecordingContribution.Role.PRIMARY,
            credited_as="Signalverket",
        )
        self._superuser()
        response = self.client.get(
            reverse("gui_v2:recording_search"), {"q": "Signal"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["results"][0]["id"], str(self.recording.pk)
        )

    @override_settings(GUI_V2_WRITES_ENABLED=False)
    def test_writes_are_blocked_outside_isolated_mode(self):
        self._superuser()
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]),
            {
                "tracks-TOTAL_FORMS": "0",
                "tracks-INITIAL_FORMS": "0",
                "tracks-MIN_NUM_FORMS": "0",
                "tracks-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ReleaseTrack.objects.exists())

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_grid_creates_track_atomically_without_scheduling_writeback(self):
        self._superuser()
        asset = FileAsset.objects.create(
            recording=self.recording,
            filename="sentinel.flac",
            role=FileAsset.Role.RADIO_FLAC,
            sync_status=FileAsset.SyncStatus.SYNCED,
        )
        payload = {
            "tracks-TOTAL_FORMS": "1",
            "tracks-INITIAL_FORMS": "0",
            "tracks-MIN_NUM_FORMS": "0",
            "tracks-MAX_NUM_FORMS": "1000",
            "tracks-0-sequence_number": "1",
            "tracks-0-disc_number": "1",
            "tracks-0-side": "A",
            "tracks-0-track_number": "1",
            "tracks-0-title_override": "Utgivelsestittel",
            "tracks-0-recording_id": str(self.recording.pk),
            "tracks-0-recording_title": self.recording.title,
            "tracks-0-artists": "",
            "tracks-0-composers": "",
            "tracks-0-lyricists": "",
            "tracks-0-duration": "03:12",
            "tracks-0-isrc": "",
            "tracks-0-arrangers": "",
        }
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]), payload
        )
        self.assertEqual(response.status_code, 302)
        track = ReleaseTrack.objects.get()
        self.assertEqual(track.recording, self.recording)
        self.assertEqual(track.duration_ms, 192000)
        asset.refresh_from_db()
        self.assertEqual(asset.sync_status, FileAsset.SyncStatus.SYNCED)

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_grid_saves_arranger_as_recording_credit(self):
        self._superuser()
        payload = {
            "tracks-TOTAL_FORMS": "1",
            "tracks-INITIAL_FORMS": "0",
            "tracks-MIN_NUM_FORMS": "0",
            "tracks-MAX_NUM_FORMS": "1000",
            "tracks-0-sequence_number": "1",
            "tracks-0-recording_id": str(self.recording.pk),
            "tracks-0-recording_title": self.recording.title,
            "tracks-0-arrangers": "Ada Arrange",
            "tracks-0-update_shared_recording": "on",
        }
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]), payload
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            self.recording.contributions.filter(
                role=RecordingContribution.Role.ARRANGER,
                credited_as="Ada Arrange",
            ).exists()
        )

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_release_metadata_and_barcode_are_edited_in_same_workspace(self):
        self._superuser()
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]),
            {
                "action": "release",
                "release-title": "Korrigert utgivelse",
                "release-release_type": Release.Type.CD,
                "release-release_year": "1998",
                "release-catalogue_number": "P7-101",
                "release-verification_status": "confirmed",
                "release-notes": "Kontrollert mot cover.",
                "release-barcode": "036000291452",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.release.refresh_from_db()
        self.assertEqual(
            (self.release.title, self.release.catalogue_number),
            ("Korrigert utgivelse", "P7-101"),
        )
        identifier = self.release.identifiers.get()
        self.assertEqual(
            (identifier.scheme, identifier.normalized_value),
            (ExternalIdentifier.Scheme.UPC, "036000291452"),
        )
        identifier_id = identifier.pk
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]),
            {
                "action": "release",
                "release-title": self.release.title,
                "release-release_type": Release.Type.CD,
                "release-release_year": "1998",
                "release-catalogue_number": "P7-101",
                "release-verification_status": "confirmed",
                "release-notes": "",
                "release-barcode": "0 36000 29145 2",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.release.identifiers.get().pk, identifier_id)

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_release_metadata_write_requires_change_permission(self):
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="catalogue", codename="view_release"
            )
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]),
            {"action": "release", "release-title": "Ikke tillatt"},
        )
        self.assertEqual(response.status_code, 403)
        self.release.refresh_from_db()
        self.assertEqual(self.release.title, "Testutgivelse")

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_track_with_linked_file_has_human_deletion_error(self):
        self._superuser()
        track = ReleaseTrack.objects.create(
            release=self.release, recording=self.recording, sequence_number=1
        )
        FileAsset.objects.create(
            recording=self.recording,
            release_track=track,
            filename="03 Amazing Grace.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]),
            {
                "action": "tracks",
                "tracks-TOTAL_FORMS": "1",
                "tracks-INITIAL_FORMS": "0",
                "tracks-MIN_NUM_FORMS": "0",
                "tracks-MAX_NUM_FORMS": "1000",
                "tracks-0-track_id": str(track.pk),
                "tracks-0-sequence_number": "1",
                "tracks-0-remove": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "03 Amazing Grace.flac")
        self.assertContains(response, "Koble filen til riktig spor")
        self.assertTrue(ReleaseTrack.objects.filter(pk=track.pk).exists())

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_invalid_grid_preserves_entered_data_and_creates_nothing(self):
        self._superuser()
        payload = {
            "tracks-TOTAL_FORMS": "1",
            "tracks-INITIAL_FORMS": "0",
            "tracks-MIN_NUM_FORMS": "0",
            "tracks-MAX_NUM_FORMS": "1000",
            "tracks-0-sequence_number": "1",
            "tracks-0-recording_title": "Ny testinnspilling",
            "tracks-0-duration": "3:99",
        }
        response = self.client.post(
            reverse("gui_v2:release_detail", args=[self.release.pk]), payload
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ny testinnspilling")
        self.assertContains(response, "Bruk mm:ss")
        self.assertFalse(ReleaseTrack.objects.exists())
        self.assertEqual(Recording.objects.count(), 1)

    def test_csrf_is_required_for_direct_write(self):
        self._superuser()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(
            strict.post(
                reverse("gui_v2:release_detail", args=[self.release.pk]), {}
            ).status_code,
            403,
        )

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_catalogue_viewer_cannot_rescan_by_direct_post(self):
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="music_library",
                codename="view_musiclibraryentry",
            )
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse(
                "gui_v2:rescan_library_file", args=[self.entry.pk, uuid4()]
            )
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_catalogue_viewer_cannot_split_file_by_direct_post(self):
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="music_library",
                codename="view_musiclibraryentry",
            )
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse(
                "gui_v2:split_library_file", args=[self.entry.pk, uuid4()]
            ),
            {"confirmed": "yes"},
        )
        self.assertEqual(response.status_code, 403)

    def test_existing_workbench_and_prototype_do_not_touch_files(self):
        self._superuser()
        with tempfile.TemporaryDirectory() as folder:
            sentinel = Path(folder) / "skal-ikke-skrives.flac"
            sentinel.write_bytes(b"read-only prototype sentinel")
            before = (sentinel.read_bytes(), sentinel.stat().st_mtime_ns)
            with override_settings(
                P7_MUSIC_ROOT=folder,
                P7_NAS_ROOT=folder,
                GUI_V2_WRITES_ENABLED=False,
            ):
                for name in (
                    "home",
                    "workbench:library",
                    "workbench:releases",
                    "gui_v2:home",
                    "gui_v2:music_library",
                    "gui_v2:release_list",
                ):
                    self.assertEqual(
                        self.client.get(reverse(name)).status_code, 200
                    )
            self.assertEqual(
                (sentinel.read_bytes(), sentinel.stat().st_mtime_ns), before
            )

    def _radio_file(self, root, recording, *, title, genre, energy):
        relative_path = "radio/test.flac"
        path = Path(root) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            Path(__file__).parents[1]
            / "flac_ingest"
            / "test_fixtures"
            / "silence.flac",
            path,
        )
        audio = FLAC(path)
        audio["TITLE"] = title
        audio["GENRE"] = genre
        audio["RATING"] = str(energy)
        audio["P7UUID"] = str(recording.pk)
        audio.save()
        asset = FileAsset.objects.create(
            recording=recording,
            filename=path.name,
            role=FileAsset.Role.RADIO_FLAC,
            sha256="0" * 64,
            sync_status=FileAsset.SyncStatus.SYNCED,
        )
        FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path=relative_path,
            status=FileLocation.Status.ACTIVE,
            is_current=True,
        )
        return asset

    def test_onetagger_uses_configured_client_folder(self):
        self._superuser()
        with tempfile.TemporaryDirectory() as root, override_settings(
            P7_MUSIC_ROOT=root,
            P7_MUSIC_CLIENT_ROOT=r"\\P7-CLIENT\Music",
        ):
            self._radio_file(
                root,
                self.recording,
                title=self.recording.title,
                genre="Pop",
                energy=3,
            )
            response = self.client.get(
                reverse("gui_v2:music_library"), {"selected": self.entry.pk}
            )
            selected = response.context["selected"]
            copied_path = (
                selected.radio_files[0].current_locations[0].onetagger_path
            )
            self.assertEqual(copied_path, r"\\P7-CLIENT\Music\radio")
            self.assertNotIn("WindowsPath(", copied_path)
            self.assertNotIn("(", copied_path)
            self.assertContains(response, "Kopier mappe til OneTagger")

    def test_onetagger_does_not_fall_back_to_server_path(self):
        self._superuser()
        with tempfile.TemporaryDirectory() as root, override_settings(
            P7_MUSIC_ROOT=root,
            P7_MUSIC_CLIENT_ROOT="",
        ):
            self._radio_file(
                root,
                self.recording,
                title=self.recording.title,
                genre="Pop",
                energy=3,
            )
            response = self.client.get(
                reverse("gui_v2:music_library"), {"selected": self.entry.pk}
            )
            selected = response.context["selected"]
            self.assertEqual(
                selected.radio_files[0].current_locations[0].onetagger_path, ""
            )
            self.assertContains(
                response,
                "Windows-/klientsti er ikke konfigurert for denne lagringsroten.",
            )
            self.assertNotContains(response, "Kopier relativ filsti")
            self.assertNotContains(response, str(Path(root).resolve()))

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_split_file_has_preview_and_keeps_flac_read_only(self):
        self._superuser()
        ExternalIdentifier.objects.create(
            recording=self.recording,
            scheme=ExternalIdentifier.Scheme.ISRC,
            value="NO-P7T-26-00555",
        )
        with tempfile.TemporaryDirectory() as root, override_settings(
            P7_MUSIC_ROOT=root
        ):
            first_asset = self._radio_file(
                root,
                self.recording,
                title="Eksisterende innspilling",
                genre="Pop",
                energy=3,
            )
            first_path = Path(root) / "radio/test.flac"
            first_audio = FLAC(first_path)
            first_audio["ISRC"] = "NO-P7T-26-00555"
            first_audio.save()

            second_path = Path(root) / "radio/annen.flac"
            shutil.copyfile(
                Path(__file__).parents[1]
                / "flac_ingest"
                / "test_fixtures"
                / "silence.flac",
                second_path,
            )
            second_audio = FLAC(second_path)
            second_audio["TITLE"] = "En helt annen sang"
            second_audio["ARTIST"] = "En annen artist"
            second_audio["ISRC"] = "NO-P7T-26-00555"
            second_audio.save()
            second_asset = FileAsset.objects.create(
                recording=self.recording,
                filename=second_path.name,
                role=FileAsset.Role.RADIO_FLAC,
            )
            FileLocation.objects.create(
                asset=second_asset,
                storage_type=FileLocation.StorageType.NAS,
                relative_path="radio/annen.flac",
            )
            before = (first_path.read_bytes(), second_path.read_bytes())

            library = self.client.get(
                reverse("gui_v2:music_library"), {"selected": self.entry.pk}
            )
            split_url = reverse(
                "gui_v2:split_library_file",
                args=[self.entry.pk, second_asset.pk],
            )
            self.assertContains(library, split_url)
            preview = self.client.get(split_url)
            self.assertContains(
                preview, "Skill ut radiofil som egen innspilling"
            )
            self.assertContains(preview, "En helt annen sang")
            self.assertContains(preview, "Ingen lydfil endres")

            response = self.client.post(split_url, {"confirmed": "yes"})
            self.assertEqual(response.status_code, 302)
            second_asset.refresh_from_db()
            self.assertNotEqual(second_asset.recording_id, self.recording.pk)
            self.assertEqual(
                second_asset.recording.title, "En helt annen sang"
            )
            self.assertTrue(
                MusicLibraryEntry.objects.filter(
                    recording_id=second_asset.recording_id
                ).exists()
            )
            self.assertEqual(
                (first_path.read_bytes(), second_path.read_bytes()), before
            )
            self.assertEqual(first_asset.recording_id, self.recording.pk)

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_single_file_rescan_updates_unmanaged_catalogue_and_radio(self):
        self._superuser()
        with tempfile.TemporaryDirectory() as root, override_settings(
            P7_MUSIC_ROOT=root
        ):
            asset = self._radio_file(
                root,
                self.recording,
                title="Tittel fra FLAC",
                genre="Rock",
                energy=5,
            )
            path = Path(root) / "radio/test.flac"
            audio = FLAC(path)
            audio["KANAL"] = "P7 Test"
            audio["COMMENT"] = "TXXX:Rotasjon - Ikke Rotasjonsverdig"
            audio.save()
            response = self.client.post(
                reverse(
                    "gui_v2:rescan_library_file",
                    args=[self.entry.pk, asset.pk],
                ),
                {"return": reverse("gui_v2:music_library")},
            )
            self.assertEqual(response.status_code, 302)
            self.recording.refresh_from_db()
            self.entry.refresh_from_db()
            self.assertEqual(self.recording.title, "Tittel fra FLAC")
            self.assertEqual(
                (self.entry.genre, self.entry.energy), ("Rock", 5)
            )
            self.assertEqual(
                self.entry.rotation_suitability,
                MusicLibraryEntry.RotationSuitability.NOT_SUITABLE,
            )
            self.assertEqual(list(self.entry.channels.all()), [self.channel])

            # Removing authoritative radio tags in OneTagger/FLAC removes the
            # corresponding values from the music library on the next re-read.
            audio = FLAC(path)
            del audio["KANAL"]
            del audio["GENRE"]
            del audio["COMMENT"]
            audio.save()
            response = self.client.post(
                reverse(
                    "gui_v2:rescan_library_file",
                    args=[self.entry.pk, asset.pk],
                ),
                {"return": reverse("gui_v2:music_library")},
            )
            self.assertEqual(response.status_code, 302)
            self.entry.refresh_from_db()
            self.assertEqual(self.entry.genre, "")
            self.assertEqual(self.entry.rotation_suitability, "")
            self.assertFalse(self.entry.channels.exists())

            # A later explicit re-read must also repair stale relations left by
            # an older importer, even when the FLAC itself is now unchanged.
            MusicLibraryChannel.objects.create(
                library_entry=self.entry, channel=self.channel
            )
            response = self.client.post(
                reverse(
                    "gui_v2:rescan_library_file",
                    args=[self.entry.pk, asset.pk],
                ),
                {"return": reverse("gui_v2:music_library")},
            )
            self.assertEqual(response.status_code, 302)
            self.assertFalse(self.entry.channels.exists())

    @override_settings(GUI_V2_WRITES_ENABLED=True)
    def test_single_file_rescan_preserves_managed_catalogue_but_updates_radio(
        self,
    ):
        self._superuser()
        ManagedRecording.objects.create(library_entry=self.entry)
        with tempfile.TemporaryDirectory() as root, override_settings(
            P7_MUSIC_ROOT=root
        ):
            asset = self._radio_file(
                root,
                self.recording,
                title="Skal ikke brukes",
                genre="Ny radiosjanger",
                energy=4,
            )
            rights_before = ManagedRecording.objects.count()
            response = self.client.post(
                reverse(
                    "gui_v2:rescan_library_file",
                    args=[self.entry.pk, asset.pk],
                ),
                {"return": reverse("gui_v2:music_library")},
            )
            self.assertEqual(response.status_code, 302)
            self.recording.refresh_from_db()
            self.entry.refresh_from_db()
            self.assertEqual(self.recording.title, "Eksisterende innspilling")
            self.assertEqual(
                (self.entry.genre, self.entry.energy), ("Ny radiosjanger", 4)
            )
            self.assertFalse(self.entry.channels.exists())
            self.assertEqual(ManagedRecording.objects.count(), rights_before)
