import uuid

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase
from django.urls import reverse

from music_publisher.models import CWRExport, Work, Writer
from music_publisher.models import Recording as DmpRecording
from parties.models import ArtistIdentity, Party
from catalogue.models import (
    ExternalIdentifier,
    Recording,
    RecordingContribution,
)


class CatalogueTests(TransactionTestCase):
    # DMP's upstream tests assume specific integer IDs. These integration
    # fixtures use real rollback/sequence reset rather than consuming those IDs.
    reset_sequences = True

    def test_title_only_is_independent(self):
        recording = Recording.objects.create(title="Test")
        self.assertIsInstance(recording.pk, uuid.UUID)
        self.assertEqual(recording.metadata_status, "draft")
        for model in (Work, DmpRecording, Writer, CWRExport):
            self.assertEqual(model.objects.count(), 0)
        self.assertEqual(recording.identifiers.count(), 0)
        self.assertEqual(recording.contributions.count(), 0)

    def test_isrc_added_later_preserves_uuid(self):
        recording = Recording.objects.create(title="Test")
        original = recording.pk
        identifier = ExternalIdentifier.objects.create(
            recording=recording, value="no-abc-26-00001"
        )
        recording.title = "New title"
        recording.save()
        recording.refresh_from_db()
        self.assertEqual(recording.pk, original)
        self.assertEqual(recording.revision, 2)
        self.assertEqual(identifier.normalized_value, "NOABC2600001")
        self.assertEqual(identifier.value, "no-abc-26-00001")
        identifier.value = "no-abc-26-00002"
        identifier.save(update_fields=["value"])
        identifier.refresh_from_db()
        self.assertEqual(identifier.normalized_value, "NOABC2600002")

    def test_contribution_added_without_ownership(self):
        recording = Recording.objects.create(title="Test")
        party = Party.objects.create(name="Artist", kind="person")
        artist = ArtistIdentity.objects.create(party=party, display_name="Stage name")
        credit = RecordingContribution.objects.create(
            recording=recording,
            party=party,
            artist_identity=artist,
            role="vocalist",
        )
        self.assertEqual(credit.party, party)
        self.assertEqual(recording.contributions.count(), 1)
        self.assertFalse(
            any("ownership" in name for name in connection.introspection.table_names())
        )
        self.assertEqual(Work.objects.count(), 0)

    def test_unicode(self):
        title = "Blåbær – Håkon / 東京 🎵"
        name = "Bjørn Guðmundsdóttir 李"
        recording = Recording.objects.create(title=title)
        party = Party.objects.create(name=name, kind="person")
        self.assertEqual(Recording.objects.get(pk=recording.pk).title, title)
        self.assertEqual(Party.objects.get(pk=party.pk).name, name)

    def test_required_names_reject_whitespace_only(self):
        invalid_objects = (
            Recording(title=" \t "),
            Party(name="  ", kind=Party.Kind.PERSON),
        )
        for instance in invalid_objects:
            with self.subTest(model=type(instance).__name__), self.assertRaises(
                ValidationError
            ):
                instance.save()
        party = Party.objects.create(name="Valid", kind=Party.Kind.PERSON)
        with self.assertRaises(ValidationError):
            ArtistIdentity.objects.create(party=party, display_name="\n ")

        recording = Recording.objects.create(title="Valid")
        identity = ArtistIdentity.objects.create(
            party=party, display_name="Valid identity"
        )
        for table, field, primary_key in (
            ("catalogue_recording", "title", recording.pk),
            ("parties_party", "name", party.pk),
            ("parties_artistidentity", "display_name", identity.pk),
        ):
            with self.subTest(table=table), self.assertRaises(
                IntegrityError
            ), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"UPDATE {table} SET {field} = %s WHERE id = %s",
                        [
                            "   ",
                            (
                                primary_key.hex
                                if connection.vendor == "sqlite"
                                else primary_key
                            ),
                        ],
                    )

    def test_all_party_kinds(self):
        for kind in Party.Kind.values:
            with self.subTest(kind=kind):
                party = Party.objects.create(name=kind, kind=kind)
                self.assertIsInstance(party.pk, uuid.UUID)

    def test_dmp_isolation(self):
        work = Work.objects.create(title="Publishing work")
        dmp_recording = DmpRecording.objects.create(
            work=work, recording_title="Publishing recording"
        )
        writer = Writer.objects.create(last_name="Writer")
        master = Recording.objects.create(title="Master")
        master.title = "Changed"
        master.save()
        master.delete()
        for model, pk in (
            (Work, work.pk),
            (DmpRecording, dmp_recording.pk),
            (Writer, writer.pk),
        ):
            self.assertTrue(model.objects.filter(pk=pk).exists())
        independent = Recording.objects.create(title="Other")
        dmp_recording.delete()
        work.delete()
        self.assertTrue(Recording.objects.filter(pk=independent.pk).exists())

    def test_uuid_cannot_change(self):
        recording = Recording.objects.create(title="Test")
        original = recording.pk
        recording.pk = uuid.uuid4()
        with self.assertRaises(ValidationError):
            recording.save()
        loaded = Recording.objects.get(pk=original)
        loaded.pk = uuid.uuid4()
        with self.assertRaises(ValidationError):
            loaded.save()
        with self.assertRaises(TypeError):
            Recording.objects.filter(pk=original).update(id=uuid.uuid4())
        self.assertEqual(Recording.objects.count(), 1)

    def test_invalid_isrc_rejected(self):
        master = Recording.objects.create(title="Test")
        for value in ("", "123", "NO-ABC-26-0000X", "NO_ABC_26_00001"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ExternalIdentifier.objects.create(recording=master, value=value)

    def test_isrc_duplicate_rejected_after_normalization(self):
        first = Recording.objects.create(title="First")
        other = Recording.objects.create(title="Other")
        ExternalIdentifier.objects.create(recording=first, value="NOABC2600001")
        with self.assertRaises(ValidationError):
            ExternalIdentifier.objects.create(recording=other, value="no-abc-26-00001")

    def test_database_identifier_constraints(self):
        first = Recording.objects.create(title="First")
        second = Recording.objects.create(title="Second")
        identifier = ExternalIdentifier.objects.create(
            recording=first, value="NOABC2600001"
        )
        # Raw SQL deliberately bypasses model validation to prove DB enforcement.
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE catalogue_externalidentifier SET normalized_value = %s WHERE id = %s",
                    [
                        "bad",
                        (
                            identifier.pk.hex
                            if connection.vendor == "sqlite"
                            else identifier.pk
                        ),
                    ],
                )
        other = ExternalIdentifier.objects.create(
            recording=second, value="NOABC2600002"
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE catalogue_externalidentifier SET normalized_value = %s WHERE id = %s",
                    [
                        identifier.normalized_value,
                        (other.pk.hex if connection.vendor == "sqlite" else other.pk),
                    ],
                )

    def test_unknown_scheme_and_namespace_rejected(self):
        recording = Recording.objects.create(title="Test")
        for kwargs in ({"scheme": "UPC"}, {"namespace": "local"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                ExternalIdentifier.objects.create(
                    recording=recording, value="NOABC2600001", **kwargs
                )

    def test_contribution_identity_matches_party(self):
        recording = Recording.objects.create(title="Test")
        first = Party.objects.create(name="First", kind="person")
        second = Party.objects.create(name="Second", kind="group")
        identity = ArtistIdentity.objects.create(
            party=first, display_name="First stage name"
        )
        with self.assertRaises(ValidationError):
            RecordingContribution.objects.create(
                recording=recording,
                party=second,
                artist_identity=identity,
                role="musician",
            )
        identity.party = second
        with self.assertRaises(ValidationError):
            identity.save()


class CatalogueAdminTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "catalogue-admin", password="test-only-password"
        )
        self.client.force_login(self.user)

    def recording_form(self, title):
        return {
            "title": title,
            "metadata_status": "draft",
            "contributions-TOTAL_FORMS": "0",
            "contributions-INITIAL_FORMS": "0",
            "contributions-MIN_NUM_FORMS": "0",
            "contributions-MAX_NUM_FORMS": "1000",
            "identifiers-TOTAL_FORMS": "0",
            "identifiers-INITIAL_FORMS": "0",
            "identifiers-MIN_NUM_FORMS": "0",
            "identifiers-MAX_NUM_FORMS": "1000",
            "_save": "Save",
        }

    def test_admin_models_and_pages(self):
        admin_index = self.client.get(reverse("admin:index"))
        for text in (
            "P7 Arkiv og rettigheter",
            "Katalog",
            "Innspillinger",
            "Personer og organisasjoner",
            "Musikalske verk",
            "Hjelp",
        ):
            with self.subTest(text=text):
                self.assertContains(admin_index, text)
        for model in (
            Recording,
            RecordingContribution,
            ExternalIdentifier,
            Party,
            ArtistIdentity,
        ):
            self.assertIn(model, admin.site._registry)
            response = self.client.get(
                reverse(
                    f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist"
                )
            )
            self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("admin:catalogue_recording_add"))
        self.assertNotIn("work", response.context["adminform"].form.fields)
        self.assertEqual(response.status_code, 200)

    def test_admin_create_reopen_edit_and_add_isrc(self):
        response = self.client.post(
            reverse("admin:catalogue_recording_add"),
            self.recording_form("Title only"),
        )
        self.assertEqual(response.status_code, 302)
        recording = Recording.objects.get()
        url = reverse("admin:catalogue_recording_change", args=[recording.pk])
        self.assertContains(self.client.get(url), "Title only")
        data = self.recording_form("Blåbær edited")
        data.update(
            {
                "identifiers-TOTAL_FORMS": "1",
                "identifiers-0-scheme": "ISRC",
                "identifiers-0-value": "no-abc-26-00001",
            }
        )
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        recording.refresh_from_db()
        self.assertEqual(recording.title, "Blåbær edited")
        self.assertEqual(recording.identifiers.get().normalized_value, "NOABC2600001")
        self.assertContains(self.client.get(url), str(recording.pk))
        self.assertEqual(Work.objects.count(), 0)

    def test_admin_bad_isrc_is_form_error(self):
        data = self.recording_form("Bad identifier")
        data.update(
            {
                "identifiers-TOTAL_FORMS": "1",
                "identifiers-0-scheme": "ISRC",
                "identifiers-0-value": "bad",
            }
        )
        response = self.client.post(reverse("admin:catalogue_recording_add"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Skriv inn en ISRC med 12 tegn")
        self.assertEqual(Recording.objects.count(), 0)

    def test_landing_dmp_and_login(self):
        self.assertContains(self.client.get("/"), "P7 Arkiv og rettigheter")
        help_response = self.client.get("/hjelp/")
        self.assertContains(
            help_response, "Work ≠ Recording ≠ Release ≠ Track ≠ lydfil"
        )
        self.assertContains(help_response, "innebærer ikke eierskap")
        self.assertEqual(
            self.client.get(
                reverse("admin:music_publisher_work_changelist")
            ).status_code,
            200,
        )
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("admin:catalogue_recording_add")).status_code,
            302,
        )
        self.assertEqual(self.client.get("/admin/login/").status_code, 200)
