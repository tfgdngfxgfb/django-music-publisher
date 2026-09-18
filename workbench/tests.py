from urllib.parse import parse_qs, quote, urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from catalogue.models import (
    ExternalIdentifier,
    Recording,
    Release,
    ReleaseTrack,
)
from media_assets.models import FileAsset, FileLocation
from managed_music.models import ManagedRecording
from managed_music.services import create_managed_recording
from music_library.models import Channel, MusicLibraryEntry, TargetAudience
from parties.models import Party
from provenance.models import (
    AppliedMetadataChange,
    MetadataAssertion,
    SourceRecord,
    SourceSystem,
)
from provenance.services import ConcurrentCatalogueChange, apply_assertion
from rights.models import Agreement, RightsClaim, RightsConfiguration
from rights.services import decide_rights_claim
from rights.summaries import OwnershipCategory
from rights_core.models import VerificationStatus


class WorkbenchTestCase(TestCase):
    password = "workbench-password"

    def create_user(
        self, username="cataloguer", *, superuser=False, permissions=()
    ):
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

    def configure_local_organization(self):
        local = Party.objects.create(
            name="Lokal testorganisasjon", kind=Party.Kind.ORGANIZATION
        )
        RightsConfiguration.objects.create(local_organization=local)
        return local

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
    def test_global_catalogue_search_follows_library_permission(self):
        user = self.create_user()
        self.login(user)
        self.assertNotContains(
            self.client.get(reverse("workbench:home")), 'id="global-search"'
        )

        viewer = self.create_user(
            username="search-viewer",
            permissions=("music_library.view_musiclibraryentry",),
        )
        self.login(viewer)
        response = self.client.get(reverse("workbench:home"))
        self.assertContains(response, 'id="global-search"')
        self.assertContains(
            response, 'action="%s"' % reverse("workbench:library")
        )

    def test_logout_page_is_norwegian(self):
        self.login(self.create_user())
        response = self.client.post(reverse("admin:logout"))
        self.assertContains(response, "Du er nå logget ut")
        self.assertNotContains(response, "Thanks for spending")

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
        response = self.client.get(reverse("workbench:home"))
        self.assertContains(response, "Musikkarkiv")
        self.assertContains(response, "Utgivelser")
        self.assertNotContains(response, "Forvaltet musikk")
        self.assertNotContains(response, "Administrasjon")
        self.assertEqual(
            self.client.get(reverse("workbench:managed")).status_code, 403
        )
        release = Release.objects.create(title="Beskyttet")
        response = self.client.post(
            reverse("workbench:release", args=(release.pk,)),
            {"title": "Endret"},
        )
        self.assertEqual(response.status_code, 403)
        release.refresh_from_db()
        self.assertEqual(release.title, "Beskyttet")

    def test_rights_decisions_are_enforced_on_direct_post(self):
        recording = Recording.objects.create(title="Rettighetsbeskyttet")
        holder = Party.objects.create(
            name="Rettighetshaver", kind=Party.Kind.ORGANIZATION
        )
        claim = RightsClaim.objects.create(
            recording=recording,
            right_type=RightsClaim.RightType.OWNERSHIP,
            rights_holder=holder,
        )
        viewer = self.create_user(
            permissions=("catalogue.view_recording", "rights.view_rightsclaim")
        )
        self.login(viewer)
        detail = (
            reverse("workbench:recording", args=(recording.pk,))
            + "?fane=rights"
        )
        response = self.client.get(detail)
        self.assertContains(response, "Rettighetskontroll er derfor skjult")
        self.assertNotContains(response, "Rettighetshaver")
        self.assertNotContains(response, ">Bekreft<")
        response = self.client.post(
            reverse("workbench:rights_claim_decide", args=(claim.pk,)),
            {"action": "confirm"},
        )
        self.assertEqual(response.status_code, 403)
        claim.refresh_from_db()
        self.assertEqual(claim.status, VerificationStatus.UNVERIFIED)

        reviewer = self.create_user(
            username="rights-reviewer",
            permissions=(
                "catalogue.view_recording",
                "rights.view_rightsclaim",
                "rights.decide_rightsclaim",
            ),
        )
        self.login(reviewer)
        response = self.client.post(
            reverse("workbench:rights_claim_decide", args=(claim.pk,)),
            {"action": "confirm", "note": "Kontrollert dokumentasjon"},
        )
        self.assertEqual(response.status_code, 403)
        claim.refresh_from_db()
        self.assertEqual(claim.status, VerificationStatus.UNVERIFIED)
        self.assertFalse(claim.decisions.exists())

    def test_managed_recording_page_does_not_claim_ownership(self):
        self.configure_local_organization()
        recording = Recording.objects.create(title="Forvaltet uten eier")
        create_managed_recording(
            recording=recording,
            relationship_type=RightsClaim.RightType.ADMINISTRATION,
        )
        viewer = self.create_user(
            permissions=("catalogue.view_recording", "rights.view_rightsclaim")
        )
        self.login(viewer)
        response = self.client.get(
            reverse("workbench:recording", args=(recording.pk,))
            + "?fane=rights"
        )
        self.assertContains(response, "Forvaltet av lokal organisasjon")
        self.assertContains(response, "Ja")
        self.assertContains(response, "Eierskap er uavklart")
        self.assertNotContains(response, "P7 eier")

    def test_authoritative_help_and_accessible_tooltip_render(self):
        self.configure_local_organization()
        recording = Recording.objects.create(title="Hjelpetest")
        create_managed_recording(
            recording=recording,
            relationship_type=RightsClaim.RightType.ADMINISTRATION,
        )
        viewer = self.create_user(
            permissions=(
                "catalogue.view_recording",
                "managed_music.view_managedrecording",
                "rights.view_rightsclaim",
            )
        )
        self.login(viewer)
        response = self.client.get(
            reverse("workbench:recording", args=(recording.pk,))
            + "?fane=rights"
        )
        self.assertContains(response, 'class="help-tip"')
        self.assertContains(response, 'aria-expanded="false"')
        self.assertContains(response, 'role="tooltip"')
        self.assertContains(response, reverse("help") + "#mastereierskap")

        help_response = self.client.get(reverse("help"))
        self.assertContains(help_response, "Felles brukerhjelp")
        self.assertContains(help_response, "Forvaltet ≠ eid")
        self.assertContains(help_response, 'id="dokumentasjonsstyrke"')

    def test_superseding_preserves_hidden_source_and_agreement(self):
        recording = Recording.objects.create(title="Krav med grunnlag")
        holder = Party.objects.create(
            name="Dokumentert part", kind=Party.Kind.ORGANIZATION
        )
        source_system = SourceSystem.objects.create(
            name="Historisk import", kind=SourceSystem.Kind.IMPORT
        )
        source_record = SourceRecord.objects.create(
            source_system=source_system, external_record_id="H-1"
        )
        agreement = Agreement.objects.create(
            title="Skjermet avtale", agreement_type=Agreement.Type.LICENSE
        )
        claim = RightsClaim.objects.create(
            recording=recording,
            right_type=RightsClaim.RightType.OWNERSHIP,
            rights_holder=holder,
            source_record=source_record,
            agreement=agreement,
        )
        reviewer = self.create_user(
            permissions=(
                "catalogue.view_recording",
                "rights.view_rightsclaim",
                "rights.add_rightsclaim",
                "rights.decide_rightsclaim",
            )
        )
        self.login(reviewer)
        response = self.client.post(
            reverse("workbench:rights_claim_supersede", args=(claim.pk,)),
            {
                "right_type": RightsClaim.RightType.DISTRIBUTION,
                "rights_holder": holder.pk,
                "grantor": "",
                "share": "75",
                "territory_mode": RightsClaim.TerritoryMode.WORLD,
                "evidence_strength": RightsClaim.EvidenceStrength.STRONG,
                "valid_from": "",
                "valid_until": "",
                "source_record": "",
                "agreement": "",
                "notes": "Ny vurdering",
            },
        )
        self.assertEqual(response.status_code, 302)
        replacement = RightsClaim.objects.get(supersedes=claim)
        self.assertEqual(
            replacement.right_type, RightsClaim.RightType.OWNERSHIP
        )
        self.assertEqual(replacement.source_record, source_record)
        self.assertEqual(replacement.agreement, agreement)

    def test_claim_creation_and_agreement_management_permissions(self):
        self.configure_local_organization()
        recording = Recording.objects.create(title="Nytt krav")
        create_managed_recording(
            recording=recording,
            relationship_type=RightsClaim.RightType.ADMINISTRATION,
        )
        holder = Party.objects.create(
            name="Kravpart", kind=Party.Kind.ORGANIZATION
        )
        cataloguer = self.create_user(
            permissions=(
                "catalogue.view_recording",
                "rights.view_rightsclaim",
                "rights.add_rightsclaim",
                "rights.view_agreement",
            )
        )
        self.login(cataloguer)
        response = self.client.post(
            reverse("workbench:rights_claim_add", args=(recording.pk,)),
            {
                "right_type": RightsClaim.RightType.OWNERSHIP,
                "rights_holder": holder.pk,
                "share": "",
                "territory_mode": RightsClaim.TerritoryMode.WORLD,
                "evidence_strength": RightsClaim.EvidenceStrength.NOT_ASSESSED,
                "valid_from": "",
                "valid_until": "",
                "grantor": "",
                "source_record": "",
                "agreement": "",
                "notes": "Historisk opplysning",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            RightsClaim.objects.get(
                recording=recording,
                rights_holder=holder,
                right_type=RightsClaim.RightType.OWNERSHIP,
            ).status,
            VerificationStatus.UNVERIFIED,
        )
        self.assertEqual(
            self.client.post(
                reverse("workbench:agreement_add"),
                {
                    "title": "Ikke tillatt",
                    "agreement_type": Agreement.Type.LICENSE,
                    "status": Agreement.Status.DRAFT,
                },
            ).status_code,
            403,
        )

        manager = self.create_user(
            username="agreement-manager",
            permissions=("rights.view_agreement", "rights.manage_agreement"),
        )
        self.login(manager)
        response = self.client.post(
            reverse("workbench:agreement_add"),
            {
                "title": "Tillatt avtale",
                "internal_reference": "A-1",
                "agreement_type": Agreement.Type.LICENSE,
                "effective_date": "",
                "expiry_date": "",
                "status": Agreement.Status.DRAFT,
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        agreement = Agreement.objects.get(title="Tillatt avtale")
        response = self.client.post(
            reverse("workbench:agreement_edit", args=(agreement.pk,)),
            {
                "title": "Oppdatert avtale",
                "internal_reference": "A-1",
                "agreement_type": Agreement.Type.LICENSE,
                "effective_date": "",
                "expiry_date": "",
                "status": Agreement.Status.ACTIVE,
                "notes": "Kontrollert",
            },
        )
        self.assertEqual(response.status_code, 302)
        agreement.refresh_from_db()
        self.assertEqual(agreement.title, "Oppdatert avtale")

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


class ManagedRightsOverviewTests(WorkbenchTestCase):
    def setUp(self):
        self.local = Party.objects.create(
            name="Lokal organisasjon", kind=Party.Kind.ORGANIZATION
        )
        self.other = Party.objects.create(
            name="Ekstern eier", kind=Party.Kind.ORGANIZATION
        )
        RightsConfiguration.objects.create(local_organization=self.local)
        self.reviewer = self.create_user(
            username="oversikt",
            permissions=(
                "catalogue.view_recording",
                "managed_music.view_managedrecording",
                "rights.view_rightsclaim",
            ),
        )
        self.decision_user = self.create_user(username="beslutter")
        self.login(self.reviewer)

    def managed_recording(self, title):
        recording = Recording.objects.create(title=title)
        create_managed_recording(
            recording=recording,
            relationship_type=RightsClaim.RightType.ADMINISTRATION,
        )
        return recording

    def confirmed_claim(self, recording, holder, share, right_type=None):
        claim = RightsClaim.objects.create(
            recording=recording,
            right_type=right_type or RightsClaim.RightType.OWNERSHIP,
            rights_holder=holder,
            share=share,
            territory_mode=RightsClaim.TerritoryMode.WORLD,
        )
        decide_rights_claim(
            claim, VerificationStatus.CONFIRMED, user=self.decision_user
        )
        claim.refresh_from_db()
        return claim

    def test_managed_list_filters_derived_ownership_categories(self):
        full = self.managed_recording("Heleid demo")
        partial = self.managed_recording("Deleid demo")
        not_owned = self.managed_recording("Ikke eid demo")
        unresolved = self.managed_recording("Uavklart demo")
        disputed = self.managed_recording("Bestridt demo")
        self.confirmed_claim(full, self.local, "100")
        self.confirmed_claim(partial, self.local, "40")
        self.confirmed_claim(not_owned, self.other, "100")
        disputed_claim = RightsClaim.objects.create(
            recording=disputed,
            right_type=RightsClaim.RightType.OWNERSHIP,
            rights_holder=self.other,
        )
        decide_rights_claim(
            disputed_claim,
            VerificationStatus.DISPUTED,
            user=self.decision_user,
        )

        for category, expected, absent in (
            (OwnershipCategory.FULL, full.title, unresolved.title),
            (OwnershipCategory.PARTIAL, partial.title, full.title),
            (OwnershipCategory.NOT_OWNED, not_owned.title, unresolved.title),
            (OwnershipCategory.UNRESOLVED, unresolved.title, not_owned.title),
            (OwnershipCategory.DISPUTED, disputed.title, full.title),
        ):
            response = self.client.get(
                reverse("workbench:managed"), {"ownership": category}
            )
            self.assertContains(response, expected)
            self.assertNotContains(response, absent)

    def test_administration_and_distribution_filters_are_independent(self):
        recording = self.managed_recording("Bare forvaltning")
        other = self.managed_recording("Lokal admin og distribusjon")
        self.confirmed_claim(
            other,
            self.local,
            None,
            RightsClaim.RightType.ADMINISTRATION,
        )
        self.confirmed_claim(
            other,
            self.local,
            None,
            RightsClaim.RightType.DISTRIBUTION,
        )
        response = self.client.get(
            reverse("workbench:managed"),
            {"local_administration": "yes", "local_distribution": "yes"},
        )
        self.assertContains(response, other.title)
        self.assertNotContains(response, recording.title)
        self.assertContains(response, "Eierskap uavklart")

    def test_rights_filters_are_not_exposed_without_rights_permission(self):
        recording = self.managed_recording("Skjermet oversikt")
        cataloguer = self.create_user(
            username="uten-rights",
            permissions=("managed_music.view_managedrecording",),
        )
        self.login(cataloguer)
        response = self.client.get(reverse("workbench:managed"))
        self.assertContains(response, recording.title)
        self.assertNotContains(response, 'name="ownership"')
        self.assertNotContains(response, "Dokumentasjonsstyrke")


class ReleaseRightsWorkflowTests(WorkbenchTestCase):
    def setUp(self):
        self.local = self.configure_local_organization()
        self.release = Release.objects.create(title="Rettighetsutgivelse")
        self.first = Recording.objects.create(title="Første spor")
        self.second = Recording.objects.create(title="Andre spor")
        ReleaseTrack.objects.create(
            release=self.release, recording=self.first, sequence_number=1
        )
        ReleaseTrack.objects.create(
            release=self.release, recording=self.second, sequence_number=2
        )

    def post_data(self):
        return {
            "recordings": (str(self.first.pk), str(self.second.pk)),
            "right_type": RightsClaim.RightType.DISTRIBUTION,
            "rights_holder": str(self.local.pk),
            "grantor": "",
            "share": "",
            "territory_mode": RightsClaim.TerritoryMode.WORLD,
            "valid_from": "",
            "valid_until": "",
            "evidence_strength": RightsClaim.EvidenceStrength.NOT_ASSESSED,
            "source_record": "",
            "agreement": "",
            "notes": "Felles grunnlag fra utgivelsen",
        }

    def test_admin_creates_one_recording_claim_per_selected_release_recording(
        self,
    ):
        admin = self.create_user(superuser=True)
        self.login(admin)
        data = {**self.post_data(), "allow_managed_registration": "on"}
        response = self.client.post(
            reverse("workbench:release_rights_add", args=(self.release.pk,)),
            data,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(RightsClaim.objects.exists())
        self.assertFalse(ManagedRecording.objects.exists())
        data.update(
            preview_token=response.context["rights_plan"].token,
            rights_stage="apply",
        )
        response = self.client.post(
            reverse("workbench:release_rights_add", args=(self.release.pk,)),
            data,
        )
        self.assertRedirects(
            response,
            reverse("workbench:release", args=(self.release.pk,)),
        )
        self.assertEqual(ManagedRecording.objects.count(), 2)
        self.assertEqual(
            RightsClaim.objects.filter(
                right_type=RightsClaim.RightType.DISTRIBUTION,
                rights_holder=self.local,
                status=VerificationStatus.UNVERIFIED,
            ).count(),
            2,
        )

    def test_rights_user_cannot_indirectly_register_unmanaged_recordings(self):
        rights_user = self.create_user(
            permissions=(
                "catalogue.view_release",
                "rights.view_rightsclaim",
                "rights.add_rightsclaim",
            )
        )
        self.login(rights_user)
        response = self.client.post(
            reverse("workbench:release_rights_add", args=(self.release.pk,)),
            self.post_data(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bare en administrator")
        self.assertFalse(ManagedRecording.objects.exists())
        self.assertFalse(RightsClaim.objects.exists())


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
            "duration_display",
            "duration_ms",
            "force_create",
        )
        for index in range(5):
            row = rows[index] if index < len(rows) else {}
            for field in fields:
                data[f"tracks-{index}-{field}"] = row.get(field, "")
        return data

    def test_filtered_release_list_is_preserved_in_detail_return_link(self):
        release = Release.objects.create(
            title="Nordlys", catalogue_number="LYN-1"
        )
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
        expected_tracks = reverse(
            "workbench:release_tracks", args=(release.pk,)
        )
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

    def test_radio_form_groups_and_saves_multiple_channels_and_targets(self):
        recording = Recording.objects.create(title="Radiovalg")
        entry = MusicLibraryEntry.objects.create(recording=recording)
        channels = [
            Channel.objects.create(code="p7_evangelisk", name="P7 Evangelisk"),
            Channel.objects.create(code="p7_riks", name="P7 Riks"),
        ]
        audiences = [
            TargetAudience.objects.create(code="60", name="60+"),
            TargetAudience.objects.create(code="30_60", name="30–60"),
        ]
        url = reverse("workbench:radio_edit", args=(recording.pk,))

        response = self.client.get(url)
        self.assertContains(response, "Beskrivelse for radio")
        self.assertContains(response, "Bruk i radio")
        self.assertContains(
            response, '<div id="id_channels" class="radio-choice-grid">'
        )
        self.assertContains(
            response,
            '<div id="id_target_audiences" class="radio-choice-grid">',
        )
        self.assertContains(response, "P7 Evangelisk")
        self.assertContains(response, "60+")

        response = self.client.post(
            url,
            {
                "genre": "Evangelisk",
                "language": "no",
                "gender": "group",
                "energy": "1",
                "channels": [str(channel.pk) for channel in channels],
                "target_audiences": [str(target.pk) for target in audiences],
                "verification_status": VerificationStatus.UNVERIFIED,
                "notes": "",
            },
        )
        self.assertRedirects(
            response,
            reverse("workbench:recording", args=(recording.pk,))
            + "?fane=radio",
        )
        self.assertEqual(
            set(entry.channels.values_list("name", flat=True)),
            {"P7 Evangelisk", "P7 Riks"},
        )
        self.assertEqual(
            set(entry.target_audiences.values_list("name", flat=True)),
            {"60+", "30–60"},
        )

    def test_multiple_tracks_are_atomic_and_existing_recording_keeps_radio_data(
        self,
    ):
        release = Release.objects.create(title="Album")
        existing = Recording.objects.create(title="Gjenbruk meg")
        library = MusicLibraryEntry.objects.create(
            recording=existing, genre="Pop"
        )
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
                    "duration_display": "3:07",
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
        self.assertEqual(
            ReleaseTrack.objects.filter(release=release).count(), 2
        )
        self.assertEqual(Recording.objects.count(), 2)
        new_track = ReleaseTrack.objects.get(recording__title="Ny master")
        self.assertEqual(new_track.duration_ms, 187000)
        self.assertEqual(new_track.recording.duration_ms, 187000)
        library.refresh_from_db()
        self.assertEqual(library.genre, "Pop")

    def test_duplicate_sequence_rolls_back_every_track_and_preserves_input(
        self,
    ):
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
        self.assertContains(
            response, "Rekkefølgen er brukt i flere utfylte rader"
        )
        self.assertEqual(ReleaseTrack.objects.count(), 0)
        self.assertEqual(Recording.objects.count(), 0)

    def test_track_entry_is_a_table_with_bulk_paste_and_single_save_action(
        self,
    ):
        release = Release.objects.create(title="Tabellalbum")
        response = self.client.get(
            reverse("workbench:release_tracks", args=(release.pk,))
        )
        self.assertContains(response, "data-track-entry")
        self.assertContains(response, 'class="track-entry-grid"')
        self.assertContains(response, "Sporplassering")
        self.assertContains(response, "Koblet innspilling")
        self.assertContains(response, "Lim inn spor fra regneark eller tabell")
        self.assertContains(response, "Lagre sporlisten", count=1)
        self.assertContains(response, "data-track-submit")

    def test_duplicate_candidate_is_shown_on_its_row_without_creating_data(
        self,
    ):
        existing = Recording.objects.create(title="Samme innspilling")
        release = Release.objects.create(title="Dublettkontroll")
        data = self.track_formset_data(
            release,
            [
                {
                    "new_recording_title": "Samme innspilling",
                    "title_override": "Sportittelen beholdes",
                    "sequence_number": "1",
                }
            ],
        )
        response = self.client.post(
            reverse("workbench:release_tracks", args=(release.pk,)), data
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mulig eksisterende innspilling")
        self.assertContains(response, f'data-use-recording="{existing.pk}"')
        self.assertContains(response, "Sportittelen beholdes")
        self.assertEqual(ReleaseTrack.objects.count(), 0)
        self.assertEqual(Recording.objects.count(), 1)

    def test_track_entry_requires_both_release_change_and_track_add_permissions(
        self,
    ):
        release = Release.objects.create(title="Beskyttet utgivelse")
        url = reverse("workbench:release_tracks", args=(release.pk,))
        only_release = self.create_user(
            username="bare-utgivelse",
            permissions=("catalogue.change_release",),
        )
        self.login(only_release)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                url, self.track_formset_data(release, [])
            ).status_code,
            403,
        )
        only_track = self.create_user(
            username="bare-spor",
            permissions=("catalogue.add_releasetrack",),
        )
        self.login(only_track)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                url, self.track_formset_data(release, [])
            ).status_code,
            403,
        )

    def test_invalid_display_duration_stays_on_the_affected_row(self):
        release = Release.objects.create(title="Varighetskontroll")
        data = self.track_formset_data(
            release,
            [
                {
                    "new_recording_title": "Bevart tittel",
                    "sequence_number": "1",
                    "duration_display": "3:75",
                }
            ],
        )
        response = self.client.post(
            reverse("workbench:release_tracks", args=(release.pk,)), data
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bruk minutter:sekunder")
        self.assertContains(response, "Bevart tittel")
        self.assertEqual(ReleaseTrack.objects.count(), 0)
        self.assertEqual(Recording.objects.count(), 0)

    def test_recording_autocomplete_is_bounded_and_does_not_render_full_catalogue(
        self,
    ):
        recordings = [
            Recording.objects.create(title=f"Søkbar {index:02}")
            for index in range(25)
        ]
        release = Release.objects.create(title="Søk")
        response = self.client.get(
            reverse("workbench:release_tracks", args=(release.pk,))
        )
        self.assertNotContains(response, recordings[0].title)
        response = self.client.get(
            reverse("workbench:recording_search") + "?q=Søkbar"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 20)

    def test_library_list_prefetches_related_catalogue_data(self):
        for index in range(10):
            MusicLibraryEntry.objects.create(
                recording=Recording.objects.create(
                    title=f"Innspilling {index}"
                )
            )
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("workbench:library"))
            self.assertEqual(response.status_code, 200)
        # Channel and target-audience are separate normalized multivalue
        # relations. Their two fixed prefetches keep the count independent of
        # catalogue size and avoid per-row queries.
        self.assertLessEqual(len(queries), 14)


class ProvenanceApplicationTests(WorkbenchTestCase):
    def setUp(self):
        self.user = self.create_user(superuser=True)
        self.login(self.user)

    def test_confirm_and_apply_title_is_atomic_and_audited(self):
        recording = Recording.objects.create(title="Gammel tittel")
        assertion = self.assertion(
            recording, field_name="title", value="Ny tittel"
        )
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
        self.assertEqual(
            change.assertion.source_record.source_system.name, "LP-cover"
        )

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
        assertion = self.assertion(
            recording, field_name="title", value="Fra kilde"
        )
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
        assertion = self.assertion(
            recording, field_name="duration_ms", value="42"
        )
        with self.assertRaisesMessage(ValueError, "kan ikke brukes"):
            apply_assertion(
                assertion,
                expected_revision=recording.revision,
                user=self.user,
            )

    def test_correction_preserves_original_and_records_reviewer(self):
        recording = Recording.objects.create(title="Original")
        assertion = self.assertion(
            recording, field_name="title", value="Feil verdi"
        )
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
        asset = FileAsset.objects.create(
            filename="radio.flac", role="radio_flac"
        )
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
    def test_library_workspace_shows_identifier_radio_and_file_state(self):
        self.login(self.create_user(superuser=True))
        recording = Recording.objects.create(
            title="Visuell kontroll", duration_ms=193000
        )
        ExternalIdentifier.objects.create(
            recording=recording,
            scheme=ExternalIdentifier.Scheme.ISRC,
            value="NO-P7K-26-00999",
        )
        MusicLibraryEntry.objects.create(
            recording=recording,
            genre="Pop",
            language="nb",
            energy=4,
        )
        asset = FileAsset.objects.create(
            recording=recording,
            filename="radio.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        FileLocation.objects.create(
            asset=asset,
            storage_type=FileLocation.StorageType.NAS,
            relative_path="Demo/Visuell kontroll/radio.flac",
            verification_status=FileLocation.VerificationStatus.VERIFIED,
        )

        response = self.client.get(reverse("workbench:library"))
        self.assertContains(response, "NOP7K2600999")
        self.assertContains(response, "Kontrollert")
        self.assertContains(response, "Radio-FLAC registrert")
        self.assertContains(response, "4 av 5")
        self.assertContains(response, 'class="global-search"')

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
        self.assertEqual(
            response.context["selected_entry"].recording_id, recording.pk
        )
        self.assertContains(response, "preview-radio")
        self.assertContains(response, "q%3DBl")
        self.assertIsNone(
            self.client.get(
                reverse("workbench:library"), {"selected": "none"}
            ).context["selected_entry"]
        )
        self.assertEqual(
            self.client.get(reverse("workbench:library"), {"genre": "Jazz"})
            .context["page"]
            .paginator.count,
            0,
        )

    def test_cover_permissions_and_confined_raster_preview(self):
        import tempfile
        from io import BytesIO
        from pathlib import Path
        from PIL import Image

        asset = FileAsset.objects.create(
            filename="cover.png", role="cover_image"
        )
        location = FileLocation.objects.create(
            asset=asset,
            storage_type="nas",
            relative_path="cover.png",
            status="active",
        )
        url = reverse("workbench:cover_image", args=[asset.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.login(self.create_user())
        self.assertEqual(self.client.get(url).status_code, 403)
        self.login(self.create_user(username="cover-admin", superuser=True))
        with tempfile.TemporaryDirectory() as folder, self.settings(
            P7_NAS_ROOT=folder, P7_MUSIC_ROOT=folder
        ):
            Image.new("RGB", (200, 100)).save(Path(folder) / "cover.png")
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "image/jpeg")
            compact_response = self.client.get(url, {"size": "64"})
            self.assertEqual(compact_response.status_code, 200)
            self.assertEqual(
                Image.open(BytesIO(compact_response.content)).size, (64, 32)
            )
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
