from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError
from django.test import SimpleTestCase, TestCase

from catalogue.authority import recording_authority
from catalogue.models import Recording, Release, ReleaseTrack
from managed_music.models import ManagedRecording, ManagedRelease
from music_library.models import MusicLibraryEntry
from parties.models import Party
from rights.models import RightsClaim, RightsConfiguration, Territory
from rights.services import (
    create_release_rights_claims,
    create_rights_claim,
    decide_rights_claim,
    supersede_rights_claim,
)
from rights.scope import (
    COUNTRY_CODES,
    OwnershipCategory,
    ScopeContext,
    claim_applies,
    claims_for_recordings,
    evaluate_right,
    normalize_territory,
    period_contains_date,
    periods_overlap,
    release_scopes_overlap,
    resolve_management_basis,
    resolve_ownership,
    resolve_right,
    resolve_rights_for_recordings,
    territory_scope_contains,
    territory_scopes_overlap,
)
from rights.summaries import (
    classify_ownership,
    has_local_confirmed_right,
    local_confirmed_right_recording_ids,
    ownership_summaries_for_recordings,
)
from rights_core.models import VerificationStatus as Status

DAY = date(2026, 9, 17)
OWN = RightsClaim.RightType.OWNERSHIP
ADMIN = RightsClaim.RightType.ADMINISTRATION
DIST = RightsClaim.RightType.DISTRIBUTION
WORLD = RightsClaim.TerritoryMode.WORLD
INCLUDE = RightsClaim.TerritoryMode.INCLUDE
EXCLUDE = RightsClaim.TerritoryMode.EXCLUDE


class ScopePrimitiveTests(SimpleTestCase):
    def test_inclusive_and_open_periods(self):
        for start, end in ((None, None), (DAY, None), (None, DAY), (DAY, DAY)):
            with self.subTest(start=start, end=end):
                self.assertTrue(period_contains_date(start, end, DAY))
        self.assertFalse(period_contains_date(date(2027, 1, 1), None, DAY))
        self.assertFalse(period_contains_date(None, date(2025, 1, 1), DAY))
        self.assertTrue(periods_overlap(None, DAY, DAY, None))
        self.assertFalse(periods_overlap(None, date(2026, 9, 16), DAY, None))

    def test_territory_membership(self):
        for mode, codes, country, expected in (
            (WORLD, (), "NO", True),
            (WORLD, (), "SE", True),
            (INCLUDE, ("NO",), "NO", True),
            (INCLUDE, ("NO",), "SE", False),
            (EXCLUDE, ("SE",), "NO", True),
            (EXCLUDE, ("SE",), "SE", False),
        ):
            with self.subTest(mode=mode, country=country):
                self.assertEqual(
                    territory_scope_contains(mode, codes, country), expected
                )
        self.assertEqual(normalize_territory(" no "), "NO")
        for invalid in (None, "", "XX", "NOR", "2WL"):
            with self.assertRaises(ValidationError):
                normalize_territory(invalid)

    def test_overlap_is_exact_for_include_and_exclude(self):
        for left, a, right, b, expected in (
            (WORLD, (), INCLUDE, ("NO",), True),
            (INCLUDE, ("NO",), INCLUDE, ("SE",), False),
            (INCLUDE, ("NO", "SE"), EXCLUDE, ("NO",), True),
            (INCLUDE, ("NO",), EXCLUDE, ("NO",), False),
            (EXCLUDE, ("SE",), EXCLUDE, ("NO",), True),
            (EXCLUDE, COUNTRY_CODES - {"NO"}, EXCLUDE, ("NO",), False),
            (EXCLUDE, COUNTRY_CODES, WORLD, (), False),
        ):
            with self.subTest(left=left, right=right):
                self.assertEqual(
                    territory_scopes_overlap(left, a, right, b), expected
                )
                self.assertEqual(
                    territory_scopes_overlap(right, b, left, a), expected
                )

    def test_release_scope_overlap(self):
        self.assertTrue(release_scopes_overlap(None, "Y"))
        self.assertTrue(release_scopes_overlap("Y", None))
        self.assertTrue(release_scopes_overlap("Y", "Y"))
        self.assertFalse(release_scopes_overlap("Y", "Z"))


class RightsScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.recording = Recording.objects.create(title="Scope recording")
        cls.local = Party.objects.create(
            name="P7", kind=Party.Kind.ORGANIZATION
        )
        cls.other = Party.objects.create(
            name="Other", kind=Party.Kind.ORGANIZATION
        )
        cls.user = get_user_model().objects.create_user(
            username="scope-reviewer"
        )
        RightsConfiguration.objects.create(local_organization=cls.local)
        cls.release_y = Release.objects.create(title="Y")
        cls.release_z = Release.objects.create(title="Z")
        cls.track_y = ReleaseTrack.objects.create(
            release=cls.release_y, recording=cls.recording, sequence_number=1
        )
        cls.track_z = ReleaseTrack.objects.create(
            release=cls.release_z, recording=cls.recording, sequence_number=1
        )

    def claim(self, *, confirmed=True, **values):
        defaults = dict(
            recording=self.recording, right_type=OWN, rights_holder=self.local
        )
        defaults.update(values)
        claim = create_rights_claim(**defaults)
        if confirmed:
            decide_rights_claim(claim, Status.CONFIRMED, user=self.user)
            claim.refresh_from_db()
        return claim

    def own(self, territory="NO", **kwargs):
        return resolve_ownership(
            self.recording, territory=territory, on_date=DAY, **kwargs
        )

    def right(self, territory="NO", **kwargs):
        return resolve_right(
            self.recording, DIST, territory=territory, on_date=DAY, **kwargs
        )

    def test_world_does_not_require_database_territory(self):
        self.claim(share=100)
        absent = next(
            code
            for code in sorted(COUNTRY_CODES)
            if not Territory.objects.filter(code=code).exists()
        )
        self.assertEqual(self.own(absent).category, OwnershipCategory.FULL)
        self.assertFalse(Territory.objects.filter(code=absent).exists())
        with self.assertRaises(ValidationError):
            self.right(territory=None)

    def test_date_boundaries_default_timezone_and_retrospective_knowledge(
        self,
    ):
        c = self.claim(share=100, valid_from=date(2020, 1, 1), valid_until=DAY)
        for day in (c.valid_from, DAY):
            result = resolve_ownership(
                self.recording, territory="NO", on_date=day
            )
            self.assertEqual(result.category, OwnershipCategory.FULL)
        with patch(
            "rights.scope.timezone.localdate", return_value=date(2027, 1, 1)
        ):
            self.assertEqual(
                resolve_ownership(self.recording, territory="NO").category,
                OwnershipCategory.UNRESOLVED,
            )
        decide_rights_claim(c, Status.REJECTED, user=self.user)
        self.assertEqual(
            resolve_ownership(
                self.recording, territory="NO", on_date=date(2020, 1, 1)
            ).category,
            OwnershipCategory.UNRESOLVED,
        )

    def test_status_and_evidence_are_independent_without_exclusivity(self):
        confirmed = self.claim(
            right_type=DIST,
            evidence_strength=RightsClaim.EvidenceStrength.WEAK,
        )
        pending = self.claim(right_type=DIST, confirmed=False)
        dispute = self.claim(
            right_type=DIST, rights_holder=self.other, confirmed=False
        )
        decide_rights_claim(dispute, Status.DISPUTED, user=self.user)
        other = self.claim(right_type=DIST, rights_holder=self.other)
        rejected = self.claim(right_type=DIST, confirmed=False)
        decide_rights_claim(rejected, Status.REJECTED, user=self.user)
        result = self.right()
        self.assertEqual(
            {c.pk for c in result.confirmed_claims}, {confirmed.pk, other.pk}
        )
        self.assertEqual(
            [c.pk for c in result.local_unverified_claims], [pending.pk]
        )
        self.assertEqual([c.pk for c in result.disputed_claims], [dispute.pk])
        self.assertEqual(
            [c.pk for c in result.other_confirmed_claims], [other.pk]
        )
        self.assertTrue(result.has_local_confirmed)
        self.assertTrue(result.has_local_pending)
        self.assertTrue(result.has_dispute)
        self.assertEqual(result.local_disputed_claims, ())
        self.assertEqual(result.context.on_date, DAY)

    def test_right_types_are_not_inferred(self):
        self.claim(share=100)
        self.assertEqual(self.right().claims, ())
        self.assertEqual(
            resolve_right(self.recording, ADMIN, territory="NO").claims, ()
        )
        with self.assertRaises(ValidationError):
            resolve_right(self.recording, "publishing", territory="NO")

    def test_general_and_release_scoped_matrix(self):
        scoped = self.claim(right_type=DIST, release_scope=self.release_y)
        general = self.claim(
            right_type=DIST,
            territory_mode=INCLUDE,
            territories=(Territory.objects.get(code="NO"),),
        )
        for code, release, expected in (
            ("NO", None, {general.pk}),
            ("NO", self.release_y, {general.pk, scoped.pk}),
            ("NO", self.release_z, {general.pk}),
            ("SE", None, set()),
            ("SE", self.release_y, {scoped.pk}),
            ("SE", self.release_z, set()),
        ):
            with self.subTest(code=code, release=release):
                result = self.right(code, release=release)
                self.assertEqual({c.pk for c in result.claims}, expected)

    def test_release_scope_creation_type_and_membership(self):
        for kind in (ADMIN, DIST):
            self.claim(right_type=kind, release_scope=self.release_y)
        with self.assertRaises(ValidationError):
            self.claim(share=100, release_scope=self.release_y)
        with self.assertRaises(ValidationError):
            self.claim(
                right_type=DIST,
                release_scope=Release.objects.create(title="Unrelated"),
            )
        c = self.claim(share=50)
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.QuerySet(model=RightsClaim).filter(pk=c.pk).update(
                release_scope=self.release_y
            )

    def test_nonownership_share_is_rejected_but_unknown_ownership_is_valid(
        self,
    ):
        for kind in (ADMIN, DIST):
            with self.assertRaises(ValidationError):
                self.claim(right_type=kind, share=0)
        c = self.claim(share=None)
        self.assertIsNone(c.share)
        self.assertIsNone(self.own().local_share)
        self.assertEqual(self.own().category, OwnershipCategory.UNRESOLVED)

    def test_release_scope_immutable_and_superseding_can_correct_it(self):
        previous = self.claim(right_type=DIST, release_scope=self.release_y)
        previous.release_scope = self.release_z
        with self.assertRaises(ValidationError):
            previous.save()
        previous.refresh_from_db()
        replacement = supersede_rights_claim(
            previous,
            user=self.user,
            rights_holder=self.local,
            release_scope=self.release_z,
        )
        decide_rights_claim(replacement, Status.CONFIRMED, user=self.user)
        self.assertEqual(self.right(release=self.release_y).claims, ())
        self.assertEqual(
            [c.pk for c in self.right(release=self.release_z).claims],
            [replacement.pk],
        )
        previous.refresh_from_db()
        self.assertEqual(previous.release_scope, self.release_y)
        self.assertEqual(previous.status, Status.SUPERSEDED)
        self.assertEqual(previous.decisions.count(), 2)

    def test_removed_track_does_not_invalidate_claim_or_decisions(self):
        c = self.claim(
            right_type=DIST, release_scope=self.release_y, confirmed=False
        )
        self.track_y.delete()
        decide_rights_claim(c, Status.CONFIRMED, user=self.user)
        self.assertEqual(
            [item.pk for item in self.right(release=self.release_y).claims],
            [c.pk],
        )
        with self.assertRaises(ProtectedError):
            self.release_y.delete()

    def test_superseding_omitted_scope_is_preserved_and_explicit_null_is_general(
        self,
    ):
        previous = self.claim(right_type=DIST, release_scope=self.release_y)
        replacement = supersede_rights_claim(
            previous, user=self.user, rights_holder=self.local
        )
        self.assertEqual(replacement.release_scope, self.release_y)
        general = supersede_rights_claim(
            replacement,
            user=self.user,
            rights_holder=self.local,
            release_scope=None,
        )
        self.assertIsNone(general.release_scope_id)

    def test_existing_claim_displays_state_release_limitation(self):
        from django.template.loader import render_to_string

        scoped = self.claim(right_type=DIST, release_scope=self.release_y)
        html = render_to_string(
            "workbench/rights_claim_group.html", {"claims": [scoped]}
        )
        self.assertIn("Kun på utgivelsen", html)
        self.assertIn("Y", html)

    def test_bulk_workflow_context_is_not_legal_scope(self):
        entry = MusicLibraryEntry.objects.create(recording=self.recording)
        ManagedRecording.objects.create(library_entry=entry)
        values = dict(
            release=self.release_y,
            recordings=[self.recording],
            right_type=DIST,
            rights_holder=self.local,
        )
        general = create_release_rights_claims(**values)[0]
        scoped = create_release_rights_claims(
            **values, release_scope=self.release_y
        )[0]
        self.assertIsNone(general.release_scope_id)
        self.assertEqual(scoped.release_scope, self.release_y)
        with self.assertRaises(ValidationError):
            create_release_rights_claims(
                **values, release_scope=self.release_z
            )

    def test_management_basis_is_explicit_read_only_and_separate_from_6b(self):
        ManagedRelease.objects.create(
            release=self.release_y,
            relationship=ManagedRelease.Relationship.MANAGED_CATALOGUE,
        )
        self.assertEqual(self.right(release=self.release_y).claims, ())
        self.assertTrue(
            recording_authority(self.recording).managed_release_only
        )
        self.claim(right_type=DIST, release_scope=self.release_y)
        before = RightsClaim.objects.count()
        basis = resolve_management_basis(self.recording, on_date=DAY)
        self.assertTrue(basis.has_local_confirmed)
        self.assertFalse(self.right().has_local_confirmed)
        self.assertFalse(ManagedRecording.objects.exists())
        self.assertEqual(RightsClaim.objects.count(), before)
        authority = recording_authority(self.recording)
        self.assertTrue(authority.managed_release_only)
        self.assertFalse(authority.writeback)
        self.assertFalse(
            has_local_confirmed_right(
                self.recording.rights_claims.all(), self.local, DIST
            )
        )
        self.assertEqual(
            local_confirmed_right_recording_ids(
                [self.recording.pk], self.local, DIST
            ),
            set(),
        )

    def test_management_basis_excludes_expired_future_and_other_holders(self):
        self.claim(right_type=DIST, valid_until=date(2025, 1, 1))
        self.claim(right_type=ADMIN, valid_from=date(2027, 1, 1))
        self.claim(right_type=DIST, rights_holder=self.other)
        self.assertFalse(
            resolve_management_basis(
                self.recording, on_date=DAY
            ).has_local_confirmed
        )
        pending = self.claim(right_type=ADMIN, confirmed=False)
        result = resolve_management_basis(self.recording, on_date=DAY)
        self.assertEqual(
            [c.pk for c in result.local_unverified_claims], [pending.pk]
        )

    def test_empty_territorial_scope_is_not_management_basis(self):
        territories = [
            Territory.objects.get_or_create(
                code=code, defaults={"name_nb": code}
            )[0]
            for code in COUNTRY_CODES
        ]
        self.claim(
            right_type=DIST, territory_mode=EXCLUDE, territories=territories
        )
        self.assertFalse(
            resolve_management_basis(
                self.recording, on_date=DAY
            ).has_local_confirmed
        )

    def test_ownership_categories_require_positive_known_information(self):
        self.assertEqual(self.own().category, OwnershipCategory.UNRESOLVED)
        first = self.claim(share=40)
        self.assertEqual(self.own().category, OwnershipCategory.PARTIAL)
        self.assertEqual(self.own().local_share, Decimal("40"))
        self.claim(share=60)  # A second independent interest, same holder.
        self.assertEqual(self.own().category, OwnershipCategory.FULL)
        decide_rights_claim(first, Status.DISPUTED, user=self.user)
        self.assertEqual(self.own().category, OwnershipCategory.DISPUTED)

    def test_third_party_full_is_not_owned_but_administration_is_independent(
        self,
    ):
        self.claim(share=100, rights_holder=self.other)
        self.claim(right_type=ADMIN)
        self.assertEqual(self.own().category, OwnershipCategory.NOT_OWNED)

    def test_confirmed_unknown_other_prevents_full(self):
        self.claim(share=100)
        self.claim(share=None, rights_holder=self.other)
        self.assertEqual(self.own().category, OwnershipCategory.DISPUTED)
        self.assertEqual(len(self.own().unknown_share_claims), 1)

    def test_unknown_and_known_partial_remain_uncertain(self):
        self.claim(share=60)
        self.claim(share=None, rights_holder=self.other)
        self.assertEqual(self.own().category, OwnershipCategory.UNRESOLVED)
        self.assertEqual(
            classify_ownership(
                self.recording.rights_claims.all(), self.local
            ).category,
            OwnershipCategory.UNRESOLVED,
        )

    def test_partial_territorial_and_temporal_overlap_is_rejected(self):
        self.claim(
            share=60,
            valid_from=date(2020, 1, 1),
            valid_until=date(2030, 12, 31),
        )
        c = self.claim(
            confirmed=False,
            share=50,
            rights_holder=self.other,
            territory_mode=INCLUDE,
            territories=(Territory.objects.get(code="NO"),),
            valid_from=date(2026, 1, 1),
            valid_until=date(2028, 12, 31),
        )
        with self.assertRaisesMessage(ValidationError, "NO"):
            decide_rights_claim(c, Status.CONFIRMED, user=self.user)
        c.refresh_from_db()
        self.assertEqual(c.status, Status.UNVERIFIED)
        self.assertFalse(c.decisions.exists())
        # Simulate conflicting legacy data to verify defensive resolution.
        models.QuerySet(model=RightsClaim).filter(pk=c.pk).update(
            status=Status.CONFIRMED
        )
        self.assertEqual(self.own("NO").category, OwnershipCategory.DISPUTED)
        self.assertEqual(self.own("SE").category, OwnershipCategory.PARTIAL)

    def test_three_claims_sum_beyond_100(self):
        self.claim(share=40)
        self.claim(share=35, rights_holder=self.other)
        third = self.claim(share=30, confirmed=False)
        with self.assertRaisesMessage(ValidationError, "105"):
            decide_rights_claim(third, Status.CONFIRMED, user=self.user)

    def test_pairwise_overlap_does_not_imply_triple_overlap(self):
        countries = {
            c.code: c
            for c in Territory.objects.filter(code__in=("NO", "SE", "DK"))
        }
        for pair in (("NO", "SE"), ("SE", "DK"), ("DK", "NO")):
            self.claim(
                share=40,
                territory_mode=INCLUDE,
                territories=[countries[c] for c in pair],
            )
        self.assertEqual(self.own().local_share, Decimal("80"))

    def test_disjoint_time_and_inclusive_boundary(self):
        self.claim(share=70, valid_until=date(2025, 12, 31))
        self.claim(share=70, valid_from=date(2026, 1, 1))
        boundary = self.claim(
            share=40,
            confirmed=False,
            valid_from=date(2025, 12, 31),
            valid_until=date(2025, 12, 31),
        )
        with self.assertRaises(ValidationError):
            decide_rights_claim(boundary, Status.CONFIRMED, user=self.user)

    def test_disjoint_exclude_and_include_can_both_own_100(self):
        norway = Territory.objects.get(code="NO")
        self.claim(share=100, territory_mode=EXCLUDE, territories=(norway,))
        self.claim(share=100, territory_mode=INCLUDE, territories=(norway,))
        summary = classify_ownership(
            self.recording.rights_claims.all(), self.local
        )
        self.assertEqual(summary.category, OwnershipCategory.FULL)
        self.assertEqual(summary.local_share, Decimal("100"))

    def test_territorial_summary_does_not_invent_a_global_share(self):
        self.claim(
            share=40,
            territory_mode=INCLUDE,
            territories=(Territory.objects.get(code="NO"),),
        )
        summary = classify_ownership(
            self.recording.rights_claims.all(), self.local
        )
        self.assertEqual(summary.category, OwnershipCategory.PARTIAL)
        self.assertIsNone(summary.local_share)

    def test_batch_and_pure_evaluation_have_bounded_queries(self):
        self.claim(
            share=100,
            territory_mode=INCLUDE,
            territories=(Territory.objects.get(code="NO"),),
        )
        ids = [self.recording.pk]
        for number in range(5):
            recording = Recording.objects.create(title=f"Other {number}")
            self.claim(recording=recording, share=100)
            ids.append(recording.pk)
        with self.assertNumQueries(2):
            resolutions = resolve_rights_for_recordings(
                ids, OWN, territory="NO", local_organization=self.local
            )
        self.assertEqual(len(resolutions), 6)
        with self.assertNumQueries(2):
            summaries = ownership_summaries_for_recordings(ids, self.local)
            for summary in summaries.values():
                summary.local_share
        loaded = tuple(claims_for_recordings(ids))
        with self.assertNumQueries(0):
            result = evaluate_right(
                loaded,
                ScopeContext(self.recording.pk, OWN, "NO", DAY),
                local_organization=self.local,
            )
            self.assertTrue(result.has_local_confirmed)
        unloaded = RightsClaim.objects.get(pk=result.claims[0].pk)
        with self.assertRaisesMessage(ValueError, "Prefetch"):
            claim_applies(unloaded, result.context)

    def test_no_local_organization_remains_unresolved(self):
        self.claim(share=100)
        result = self.own(local_organization=None)
        self.assertEqual(result.category, OwnershipCategory.UNRESOLVED)
        self.assertFalse(result.rights.has_local_confirmed)
