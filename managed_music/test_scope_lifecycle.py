from datetime import date, datetime, timedelta, timezone as dt_timezone
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from catalogue.authority import recording_authority
from catalogue.models import Recording, Release, ReleaseTrack
from managed_music.lifecycle import (
    evaluate_management_state,
    management_state,
    management_states,
    reconcile_management_statuses,
    refresh_management_status,
)
from managed_music.models import ManagedRecording, ManagedRelease
from managed_music.services import return_to_music_library
from media_assets.models import FileAsset
from music_library.models import MusicLibraryEntry
from parties.models import Party
from provenance.models import SourceRecord
from rights.models import (
    RightsClaim,
    RightsConfiguration,
    RightsDecision,
    Territory,
)
from rights.scope import (
    COUNTRY_CODES,
    OwnershipCategory,
    resolve_ownership,
    resolve_right,
)
from rights.services import (
    create_rights_claim,
    decide_rights_claim,
    supersede_rights_claim,
)
from rights_core.models import VerificationStatus as Status

DAY = date(2026, 9, 17)
PENDING = ManagedRecording.Status.PENDING
ACTIVE = ManagedRecording.Status.ACTIVE
INACTIVE = ManagedRecording.Status.INACTIVE
ADMIN = RightsClaim.RightType.ADMINISTRATION
DIST = RightsClaim.RightType.DISTRIBUTION
OWN = RightsClaim.RightType.OWNERSHIP


class ScopeLifecycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.local = Party.objects.create(
            name="P7", kind=Party.Kind.ORGANIZATION
        )
        cls.other = Party.objects.create(
            name="Other", kind=Party.Kind.ORGANIZATION
        )
        cls.user = get_user_model().objects.create_user(
            username="lifecycle-reviewer"
        )
        cls.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="managed_music",
                codename="delete_managedrecording",
            )
        )
        RightsConfiguration.objects.create(local_organization=cls.local)
        cls.norway, _ = Territory.objects.get_or_create(
            code="NO", defaults={"name_nb": "Norge"}
        )

    def setUp(self):
        clock = patch(
            "django.utils.timezone.now",
            return_value=datetime(2026, 9, 17, 10, tzinfo=dt_timezone.utc),
        )
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.managed = self.member()
        self.recording = self.managed.recording

    def member(self, status=PENDING):
        entry = MusicLibraryEntry.objects.create(
            recording=Recording.objects.create(title="Lifecycle")
        )
        return ManagedRecording.objects.create(
            library_entry=entry, status=status
        )

    def claim(self, status=Status.UNVERIFIED, **values):
        defaults = dict(
            recording=self.recording,
            rights_holder=self.local,
            right_type=ADMIN,
        )
        defaults.update(values)
        claim = create_rights_claim(**defaults)
        if status != Status.UNVERIFIED:
            self.decide(claim, status)
        return claim

    def decide(self, claim, status):
        self.clock.return_value += timedelta(seconds=1)
        decide_rights_claim(claim, status, user=self.user)
        claim.refresh_from_db()

    def state(self, **kwargs):
        self.managed.refresh_from_db()
        return management_state(self.managed, **kwargs)

    def test_release_scoped_basis_activates_without_general_use_right(self):
        release = Release.objects.create(title="Specific release")
        release_management = ManagedRelease.objects.create(
            release=release,
            relationship=ManagedRelease.Relationship.MANAGED_CATALOGUE,
        )
        for kind in (ADMIN, DIST):
            with self.subTest(kind=kind):
                managed = self.member()
                track = ReleaseTrack.objects.create(
                    release=release,
                    recording=managed.recording,
                    sequence_number=1 if kind == ADMIN else 2,
                )
                claim = self.claim(
                    Status.CONFIRMED,
                    recording=managed.recording,
                    right_type=kind,
                    release_scope=release,
                    territory_mode=RightsClaim.TerritoryMode.INCLUDE,
                    territories=(self.norway,),
                )
                managed.refresh_from_db()
                self.assertEqual(managed.status, ACTIVE)
                self.assertFalse(
                    resolve_right(
                        managed.recording, kind, territory="NO"
                    ).has_local_confirmed
                )
                self.assertTrue(
                    resolve_right(
                        managed.recording,
                        kind,
                        territory="NO",
                        release=release,
                    ).has_local_confirmed
                )
                track.delete()
                self.assertTrue(management_state(managed).has_current_basis)
                self.decide(claim, Status.REJECTED)
                managed.refresh_from_db()
                self.assertEqual(managed.status, INACTIVE)
                self.assertTrue(
                    management_state(managed).has_confirmed_history
                )
        release_management.refresh_from_db()
        self.assertEqual(release_management.status, PENDING)
        self.assertEqual(release_management.revision, 1)

    def test_nonempty_include_and_exclude_qualify(self):
        for mode in (
            RightsClaim.TerritoryMode.INCLUDE,
            RightsClaim.TerritoryMode.EXCLUDE,
        ):
            managed = self.member()
            self.claim(
                Status.CONFIRMED,
                recording=managed.recording,
                territory_mode=mode,
                territories=(self.norway,),
            )
            self.assertTrue(management_state(managed).has_current_basis)

    def test_empty_territory_never_establishes_basis_or_history(self):
        territories = [
            Territory.objects.get_or_create(
                code=code, defaults={"name_nb": code}
            )[0]
            for code in COUNTRY_CODES
        ]
        claim = self.claim(
            territory_mode=RightsClaim.TerritoryMode.EXCLUDE,
            territories=territories,
        )
        for status in (
            Status.UNVERIFIED,
            Status.CONFIRMED,
            Status.DISPUTED,
            Status.REJECTED,
        ):
            if status != Status.UNVERIFIED:
                self.decide(claim, status)
            state = self.state()
            self.assertEqual(state.status, PENDING)
            self.assertFalse(state.has_current_basis)
            self.assertFalse(state.has_confirmed_history)
            self.assertFalse(state.has_plausible_basis)
            self.assertTrue(state.requires_follow_up)
            self.assertTrue(state.can_return_to_music_library)

    def test_unverified_is_normal_onboarding_but_dispute_requires_follow_up(
        self,
    ):
        claim = self.claim()
        state = self.state()
        self.assertTrue(state.has_unverified_basis)
        self.assertFalse(state.requires_follow_up)
        self.decide(claim, Status.DISPUTED)
        state = self.state()
        self.assertEqual(state.status, PENDING)
        self.assertTrue(state.has_disputed_basis)
        self.assertTrue(state.requires_follow_up)
        self.assertFalse(state.has_confirmed_history)

    def test_confirmed_then_disputed_keeps_history_and_membership(self):
        claim = self.claim(Status.CONFIRMED)
        self.decide(claim, Status.DISPUTED)
        state = self.state()
        self.assertEqual(state.status, INACTIVE)
        self.assertEqual(state.stored_status, INACTIVE)
        self.assertTrue(state.has_disputed_basis)
        self.assertTrue(state.has_confirmed_history)
        self.assertTrue(state.requires_follow_up)

    def test_another_confirmed_basis_overrides_dispute_but_not_follow_up(self):
        claim = self.claim(Status.CONFIRMED)
        self.claim(Status.CONFIRMED, right_type=DIST)
        self.decide(claim, Status.DISPUTED)
        state = self.state()
        self.assertEqual(state.status, ACTIVE)
        self.assertTrue(state.requires_follow_up)
        self.assertTrue(state.has_current_basis)

    def test_third_party_statuses_are_not_local_basis_or_follow_up(self):
        self.claim()
        for status in (Status.CONFIRMED, Status.DISPUTED, Status.UNVERIFIED):
            self.claim(status, rights_holder=self.other)
        state = self.state()
        self.assertEqual(state.status, PENDING)
        self.assertFalse(state.has_current_basis)
        self.assertFalse(state.has_confirmed_history)
        self.assertFalse(state.has_disputed_basis)
        self.assertFalse(state.requires_follow_up)

    def test_ownership_conflict_does_not_revoke_confirmed_local_basis(self):
        self.claim(Status.CONFIRMED, right_type=OWN, share=60)
        # Legacy inconsistent data: services correctly reject this new decision.
        RightsClaim.objects.create(
            recording=self.recording,
            rights_holder=self.other,
            right_type=OWN,
            share=50,
            status=Status.CONFIRMED,
        )
        self.assertEqual(
            resolve_ownership(self.recording, territory="NO").category,
            OwnershipCategory.DISPUTED,
        )
        self.assertEqual(self.state().status, ACTIVE)
        self.assertFalse(self.state().has_disputed_basis)

    def test_date_boundaries_and_materialized_status(self):
        start, end = DAY + timedelta(days=2), DAY + timedelta(days=4)
        self.claim(Status.CONFIRMED, valid_from=start, valid_until=end)
        state = self.state()
        self.assertEqual(state.status, PENDING)
        self.assertTrue(state.has_future_confirmed_basis)
        self.assertFalse(state.has_confirmed_history)
        self.assertFalse(state.requires_follow_up)
        for day, expected in (
            (start, ACTIVE),
            (end, ACTIVE),
            (end + timedelta(days=1), INACTIVE),
        ):
            with self.subTest(day=day):
                refresh_management_status(self.recording, on_date=day)
                self.managed.refresh_from_db()
                self.assertEqual(self.managed.status, expected)

    def test_history_has_priority_over_future_basis_then_reactivates(self):
        self.claim(Status.CONFIRMED, valid_until=DAY - timedelta(days=1))
        self.claim(
            Status.CONFIRMED,
            right_type=DIST,
            valid_from=DAY + timedelta(days=5),
        )
        state = self.state()
        self.assertEqual(state.status, INACTIVE)
        self.assertTrue(state.has_future_confirmed_basis)
        self.assertTrue(state.has_confirmed_history)
        refresh_management_status(
            self.recording, on_date=DAY + timedelta(days=5)
        )
        self.managed.refresh_from_db()
        self.assertEqual(self.managed.status, ACTIVE)

    def test_missed_entire_confirmed_period_still_establishes_history(self):
        self.claim(
            Status.CONFIRMED,
            valid_from=DAY + timedelta(days=1),
            valid_until=DAY + timedelta(days=2),
        )
        self.assertEqual(self.state().stored_status, PENDING)
        result = reconcile_management_statuses(on_date=DAY + timedelta(days=3))
        self.assertEqual(result.updated, 1)
        self.managed.refresh_from_db()
        self.assertEqual(self.managed.status, INACTIVE)

    def test_contiguous_and_overlapping_bases_do_not_create_inactive_gap(self):
        self.claim(Status.CONFIRMED, valid_until=DAY + timedelta(days=2))
        self.claim(
            Status.CONFIRMED,
            right_type=DIST,
            valid_from=DAY + timedelta(days=3),
        )
        self.claim(
            Status.CONFIRMED,
            valid_from=DAY + timedelta(days=1),
            valid_until=DAY + timedelta(days=2),
        )
        for offset in range(5):
            self.assertEqual(
                self.state(on_date=DAY + timedelta(days=offset)).status, ACTIVE
            )

    def test_historical_unverified_and_disputed_claims_block_return(self):
        for status in (Status.UNVERIFIED, Status.DISPUTED):
            managed = self.member()
            self.claim(
                status,
                recording=managed.recording,
                valid_until=date(2020, 1, 1),
            )
            state = management_state(managed)
            self.assertEqual(state.status, PENDING)
            self.assertTrue(state.has_plausible_basis)
            self.assertFalse(state.can_return_to_music_library)
            with self.assertRaises(ValidationError):
                return_to_music_library(
                    managed, user=self.user, reason="Historikk må avklares"
                )

    def test_confirmed_future_rejected_or_superseded_before_start_has_no_history(
        self,
    ):
        for decision in (Status.REJECTED, Status.SUPERSEDED):
            managed = self.member()
            claim = self.claim(
                Status.CONFIRMED,
                recording=managed.recording,
                valid_from=DAY + timedelta(days=10),
            )
            if decision == Status.SUPERSEDED:
                self.clock.return_value += timedelta(seconds=1)
                replacement = supersede_rights_claim(
                    claim,
                    user=self.user,
                    rights_holder=self.local,
                    valid_from=claim.valid_from,
                )
                self.decide(replacement, Status.REJECTED)
            else:
                self.decide(claim, decision)
            state = management_state(managed, on_date=DAY + timedelta(days=20))
            self.assertFalse(state.has_confirmed_history)
            self.assertTrue(state.can_return_to_music_library)

    def test_documentation_event_does_not_end_future_confirmation(self):
        claim = self.claim(
            Status.CONFIRMED,
            valid_from=DAY + timedelta(days=1),
            valid_until=DAY + timedelta(days=2),
        )
        RightsDecision.objects.create(
            claim=claim,
            decision=RightsDecision.Decision.DOCUMENTED,
            decided_by=self.user,
        )
        state = self.state(on_date=DAY + timedelta(days=3))
        self.assertTrue(state.has_confirmed_history)
        self.assertEqual(state.status, INACTIVE)

    def test_legacy_confirmed_expired_claim_without_decision_is_history(self):
        RightsClaim.objects.create(
            recording=self.recording,
            rights_holder=self.local,
            right_type=ADMIN,
            status=Status.CONFIRMED,
            valid_until=DAY - timedelta(days=1),
        )
        self.assertEqual(self.state().status, INACTIVE)
        self.assertTrue(self.state().has_confirmed_history)

    def test_legacy_active_and_inactive_remain_uncertain_without_history(self):
        for status in (ACTIVE, INACTIVE):
            managed = self.member(status=status)
            state = management_state(managed)
            self.assertIsNone(state.status)
            self.assertEqual(state.stored_status, status)
            self.assertTrue(state.history_uncertain)
            self.assertTrue(state.requires_follow_up)
            self.assertFalse(state.needs_reconciliation)
            self.assertFalse(state.can_return_to_music_library)
        result = reconcile_management_statuses()
        self.assertEqual(result.uncertain, 2)
        self.assertEqual(result.updated, 0)

    def test_read_only_state_and_refresh_idempotence(self):
        self.claim(Status.CONFIRMED, valid_until=DAY)
        self.managed.refresh_from_db()
        original = ManagedRecording.objects.values().get(pk=self.managed.pk)
        with CaptureQueriesContext(connection) as queries:
            state = management_state(
                self.managed, on_date=DAY + timedelta(days=1)
            )
        self.assertTrue(
            all(
                q["sql"].lstrip().upper().startswith("SELECT") for q in queries
            )
        )
        self.assertEqual(state.stored_status, ACTIVE)
        self.assertEqual(state.status, INACTIVE)
        self.assertTrue(state.needs_reconciliation)
        self.assertEqual(
            ManagedRecording.objects.values().get(pk=self.managed.pk), original
        )
        refresh_management_status(
            self.recording, on_date=DAY + timedelta(days=1)
        )
        after = ManagedRecording.objects.values().get(pk=self.managed.pk)
        self.assertEqual(after["revision"], original["revision"] + 1)
        refresh_management_status(
            self.recording, on_date=DAY + timedelta(days=1)
        )
        self.assertEqual(
            ManagedRecording.objects.values().get(pk=self.managed.pk), after
        )

    def test_batch_dry_run_idempotence_history_and_membership_preservation(
        self,
    ):
        self.claim(Status.CONFIRMED, valid_from=DAY + timedelta(days=1))
        ordinary = Recording.objects.create(title="No membership")
        self.claim(Status.CONFIRMED, recording=ordinary)
        legacy = self.member(status=INACTIVE)
        before_members = list(ManagedRecording.objects.order_by("pk").values())
        before_claims = list(RightsClaim.objects.order_by("pk").values())
        before_decisions = list(RightsDecision.objects.order_by("pk").values())
        before_sources = SourceRecord.objects.count()
        dry = reconcile_management_statuses(
            on_date=DAY + timedelta(days=2), batch_size=1, dry_run=True
        )
        self.assertEqual(
            (
                dry.examined,
                dry.updated,
                dry.needs_reconciliation,
                dry.uncertain,
            ),
            (2, 0, 1, 1),
        )
        self.assertEqual(
            list(ManagedRecording.objects.order_by("pk").values()),
            before_members,
        )
        result = reconcile_management_statuses(
            on_date=DAY + timedelta(days=2), batch_size=1
        )
        self.assertEqual(result.updated, 1)
        self.assertEqual(
            reconcile_management_statuses(
                on_date=DAY + timedelta(days=2)
            ).updated,
            0,
        )
        self.assertEqual(
            set(ManagedRecording.objects.values_list("pk", flat=True)),
            {self.managed.pk, legacy.pk},
        )
        self.assertIsNone(refresh_management_status(ordinary))
        self.assertEqual(
            list(RightsClaim.objects.order_by("pk").values()), before_claims
        )
        self.assertEqual(
            list(RightsDecision.objects.order_by("pk").values()),
            before_decisions,
        )
        self.assertEqual(SourceRecord.objects.count(), before_sources)

    def test_status_only_saves_do_not_queue_sync_but_creation_and_catalogue_do(
        self,
    ):
        entry = MusicLibraryEntry.objects.create(
            recording=Recording.objects.create(title="Sync source")
        )
        asset = FileAsset.objects.create(
            recording=entry.recording,
            filename="radio.flac",
            role=FileAsset.Role.RADIO_FLAC,
        )
        managed = ManagedRecording.objects.create(library_entry=entry)
        asset.refresh_from_db()
        self.assertEqual(asset.sync_status, FileAsset.SyncStatus.PENDING)
        self.claim(
            Status.CONFIRMED, recording=entry.recording, valid_until=DAY
        )
        asset.sync_status = FileAsset.SyncStatus.SYNCED
        asset.save(update_fields=("sync_status",))
        before_asset = FileAsset.objects.values().get(pk=asset.pk)
        for operation in (
            lambda: refresh_management_status(
                entry.recording, on_date=DAY + timedelta(days=1)
            ),
            lambda: reconcile_management_statuses(on_date=DAY),
        ):
            operation()
            self.assertEqual(
                FileAsset.objects.values().get(pk=asset.pk), before_asset
            )
        self.assertTrue(recording_authority(entry.recording).writeback)
        entry.recording.title = "Legitimate catalogue change"
        entry.recording.save(update_fields=("title",))
        asset.refresh_from_db()
        self.assertEqual(asset.sync_status, FileAsset.SyncStatus.PENDING)
        self.assertTrue(
            ManagedRecording.objects.filter(pk=managed.pk).exists()
        )

    def test_batch_and_read_queries_do_not_grow_per_recording(self):
        self.claim(Status.CONFIRMED, valid_from=DAY + timedelta(days=1))
        one = list(ManagedRecording.objects.select_related("library_entry"))
        with self.assertNumQueries(4):
            management_states(one)
        with CaptureQueriesContext(connection) as small:
            reconcile_management_statuses(dry_run=True)
        for _ in range(11):
            managed = self.member()
            self.claim(
                Status.CONFIRMED,
                recording=managed.recording,
                valid_from=DAY + timedelta(days=1),
            )
        many = list(ManagedRecording.objects.select_related("library_entry"))
        with self.assertNumQueries(4):
            states = management_states(many)
        self.assertEqual(len(states), 12)
        with CaptureQueriesContext(connection) as large:
            reconcile_management_statuses(dry_run=True)
        self.assertEqual(len(small), len(large))
        with CaptureQueriesContext(connection) as writes:
            result = reconcile_management_statuses(
                on_date=DAY + timedelta(days=1)
            )
        self.assertEqual(result.updated, 12)
        # Canonical saves cost per changed row; rights/history loading must not.
        for table in (
            "rights_rightsclaim",
            "rights_rightsdecision",
            "rights_claimterritory",
        ):
            self.assertEqual(sum(table in q["sql"] for q in writes), 1)

    def test_pure_evaluation_does_no_queries(self):
        claim = self.claim(Status.CONFIRMED)
        loaded = RightsClaim.objects.prefetch_related(
            "territories", "decisions"
        ).get(pk=claim.pk)
        with self.assertNumQueries(0):
            state = evaluate_management_state(
                self.managed,
                (loaded,),
                local_organization=self.local,
                on_date=DAY,
            )
        self.assertEqual(state.status, ACTIVE)

    def test_default_date_follows_django_timezone(self):
        self.claim(Status.CONFIRMED, valid_from=DAY + timedelta(days=1))
        self.clock.return_value = datetime(
            2026, 9, 17, 22, 30, tzinfo=dt_timezone.utc
        )
        with timezone.override("Europe/Oslo"):
            self.assertEqual(self.state().status, ACTIVE)
            self.assertEqual(
                reconcile_management_statuses().on_date,
                DAY + timedelta(days=1),
            )

    def test_command_supports_dry_run_date_and_invalid_configuration(self):
        self.claim(Status.CONFIRMED, valid_from=DAY + timedelta(days=1))
        out = StringIO()
        call_command(
            "reconcile_management_statuses",
            "--dry-run",
            "--on-date",
            "2026-09-18",
            stdout=out,
        )
        self.assertIn("1 med statusavvik, 0 oppdatert", out.getvalue())
        self.assertEqual(self.state().stored_status, PENDING)
        call_command(
            "reconcile_management_statuses",
            "--on-date",
            "2026-09-18",
            stdout=StringIO(),
        )
        self.assertEqual(self.state().stored_status, ACTIVE)
        with self.assertRaises(CommandError):
            call_command("reconcile_management_statuses", "--batch-size", "0")
        with self.assertRaises(CommandError):
            call_command(
                "reconcile_management_statuses", "--on-date", "not-a-date"
            )
        RightsConfiguration.objects.get().delete()
        with self.assertRaises(CommandError):
            call_command("reconcile_management_statuses")

    def test_empty_batch_is_safe(self):
        self.assertEqual(management_states([]), {})
        return_to_music_library(
            self.managed, user=self.user, reason="Onboarding avsluttet"
        )
        result = reconcile_management_statuses()
        self.assertEqual(result.examined, 0)
        self.assertEqual(result.updated, 0)
