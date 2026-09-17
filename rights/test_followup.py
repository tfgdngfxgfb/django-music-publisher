"""Pure 6I contracts, with the database forbidden during all evaluations."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from managed_music.lifecycle import ManagementState
from rights.followup import (
    ClaimFact,
    RecordingFacts,
    ReleaseFacts,
    TerritoryFact,
    evaluate_recording,
    evaluate_release,
    item_sort_key,
)
from rights.positions import (
    legal_position_identity,
    claims_applicable_in_release_context,
)


class FollowUpEvaluationTests(TestCase):
    day = date(2026, 9, 18)

    def claim(self, **values):
        return replace(
            ClaimFact("c", "r", "master_ownership", "p7", share=Decimal(100)),
            **values
        )

    def evaluate(self, *claims, **values):
        facts = RecordingFacts(
            "r", "Sangen", (), tuple(claims), frozenset(), **values
        )
        with self.assertNumQueries(0):
            return evaluate_recording(facts, local_id="p7", on_date=self.day)

    def codes(self, items):
        return {c for i in items for c in i.codes}

    def state(self, **values):
        return replace(
            ManagementState(
                "pending",
                "pending",
                False,
                False,
                False,
                False,
                False,
                False,
                True,
            ),
            **values
        )

    def test_verification_and_evidence_are_separate(self):
        for status, expected in (
            ("unverified", "claim.unverified"),
            ("disputed", "claim.disputed"),
            ("confirmed", "evidence.not_assessed"),
        ):
            with self.subTest(status=status):
                self.assertEqual(
                    self.codes(self.evaluate(self.claim(status=status))),
                    {expected},
                )
        for evidence in ("probable", "strong", "documented"):
            self.assertFalse(
                self.evaluate(
                    self.claim(status="confirmed", evidence_strength=evidence)
                )
            )
        self.assertEqual(
            self.codes(
                self.evaluate(
                    self.claim(status="confirmed", evidence_strength="weak")
                )
            ),
            {"evidence.weak"},
        )

    def test_signal_disappears_without_requiring_item_disappearance(self):
        before = self.evaluate(self.claim())[0]
        after = self.evaluate(self.claim(status="confirmed"))[0]
        self.assertEqual(before.key, after.key)
        self.assertEqual(before.category, "action")
        self.assertEqual(after.category, "quality")
        self.assertNotIn("claim.unverified", after.codes)
        self.assertEqual(
            self.evaluate(
                self.claim(status="confirmed", evidence_strength="documented")
            ),
            (),
        )

    def test_expired_and_terminal_positions_do_not_create_normal_issues(self):
        for status in (
            "unverified",
            "disputed",
            "confirmed",
            "rejected",
            "superseded",
        ):
            c = self.claim(
                status=status,
                valid_until=self.day - timedelta(days=1),
                release_scope_id="missing",
            )
            self.assertEqual(self.evaluate(c, replace(c, pk="other")), ())
        for status in ("rejected", "superseded"):
            c = self.claim(status=status, release_scope_id="missing")
            self.assertEqual(self.evaluate(c, replace(c, pk="other")), ())

    def test_unknown_share_and_third_party_non_completeness(self):
        self.assertIn(
            "ownership.local_share_unknown",
            self.codes(self.evaluate(self.claim(share=None))),
        )
        self.assertNotIn(
            "ownership.local_share_unknown",
            self.codes(self.evaluate(self.claim(share=Decimal(0)))),
        )
        self.assertEqual(
            self.evaluate(self.claim(rights_holder_id="external", share=None)),
            (),
        )

    def test_empty_territory_has_no_current_or_future_signal(self):
        self.assertEqual(
            self.evaluate(self.claim(territory_mode="include")), ()
        )

    def test_upcoming_inclusive_horizon_boundaries(self):
        for horizon in (30, 90, 180):
            for offset in (0, 1, 30, 90, 180, 181):
                c = self.claim(
                    status="confirmed",
                    evidence_strength="documented",
                    valid_until=self.day + timedelta(days=offset),
                )
                facts = RecordingFacts("r", "Sangen", (), (c,), frozenset())
                with self.assertNumQueries(0):
                    items = evaluate_recording(
                        facts, local_id="p7", on_date=self.day, horizon=horizon
                    )
                self.assertEqual(
                    "claim.expiring" in self.codes(items), offset <= horizon
                )
                c = replace(c, valid_from=c.valid_until, valid_until=None)
                with self.assertNumQueries(0):
                    items = evaluate_recording(
                        replace(facts, claims=(c,)),
                        local_id="p7",
                        on_date=self.day,
                        horizon=horizon,
                    )
                self.assertEqual(
                    "claim.starts_soon" in self.codes(items),
                    0 < offset <= horizon,
                )
        self.assertNotIn(
            "claim.expiring",
            self.codes(self.evaluate(self.claim(status="confirmed"))),
        )
        for status in ("unverified", "disputed"):
            self.assertFalse(
                self.codes(
                    self.evaluate(
                        self.claim(status=status, valid_until=self.day)
                    )
                )
                & {"claim.expiring", "claim.starts_soon"}
            )

    def test_future_claim_does_not_expire_before_it_starts(self):
        c = self.claim(
            status="confirmed",
            valid_from=self.day + timedelta(days=1),
            valid_until=self.day + timedelta(days=2),
        )
        item = self.evaluate(c)[0]
        self.assertIn("claim.starts_soon", item.codes)
        self.assertNotIn("claim.expiring", item.codes)
        self.assertEqual(item.category, "quality")

    def test_exact_legal_identity_and_overlap_are_different(self):
        c = self.claim()
        equivalent = replace(
            c, pk="b", evidence_strength="weak", details=(("Avtale", "Annen"),)
        )
        self.assertEqual(
            legal_position_identity(c), legal_position_identity(equivalent)
        )
        items = self.evaluate(c, equivalent)
        group = next(i for i in items if "claim.duplicate_position" in i.codes)
        self.assertEqual(len(group.claim_ids), 2)
        for field, value in {
            "recording_id": "other",
            "right_type": "distribution",
            "rights_holder_id": "other",
            "grantor_id": "other",
            "share": Decimal(99),
            "territory_mode": "exclude",
            "territories": (TerritoryFact("no", "NO"),),
            "valid_from": self.day,
            "valid_until": self.day,
            "release_scope_id": "release",
        }.items():
            with self.subTest(field=field):
                changed = replace(c, **{field: value})
                self.assertNotEqual(
                    legal_position_identity(c),
                    legal_position_identity(changed),
                )
                self.assertNotIn(
                    "claim.duplicate_position",
                    self.codes(self.evaluate(c, changed)),
                )
        no, se = TerritoryFact("no", "NO"), TerritoryFact("se", "SE")
        self.assertEqual(
            legal_position_identity(replace(c, territories=(no, se))),
            legal_position_identity(replace(c, territories=(se, no))),
        )

    def test_release_mismatch_is_removed_by_catalogue_relation(self):
        c = self.claim(
            right_type="distribution", share=None, release_scope_id="release"
        )
        self.assertIn("scope.release_mismatch", self.codes(self.evaluate(c)))
        facts = RecordingFacts("r", "Sangen", (), (c,), frozenset({"release"}))
        with self.assertNumQueries(0):
            items = evaluate_recording(facts, local_id="p7", on_date=self.day)
        self.assertNotIn("scope.release_mismatch", self.codes(items))

    def test_real_overlap_conflict_and_non_simultaneous_positions(self):
        a = self.claim(
            status="confirmed",
            share=Decimal(60),
            evidence_strength="documented",
        )
        b = replace(
            a,
            pk="b",
            rights_holder_id="external",
            share=Decimal(50),
            territory_mode="include",
            territories=(TerritoryFact("no", "NO"),),
        )
        self.assertIn("ownership.conflict", self.codes(self.evaluate(a, b)))
        self.assertNotIn(
            "ownership.conflict",
            self.codes(
                self.evaluate(
                    replace(
                        a, territory_mode="exclude", territories=b.territories
                    ),
                    b,
                )
            ),
        )
        future = self.day + timedelta(days=10)
        self.assertNotIn(
            "ownership.conflict",
            self.codes(
                self.evaluate(
                    replace(a, valid_until=future),
                    replace(b, valid_from=future + timedelta(days=1)),
                )
            ),
        )
        self.assertNotIn(
            "ownership.conflict",
            self.codes(self.evaluate(a, replace(b, status="unverified"))),
        )
        thirds = tuple(
            replace(a, pk=str(i), share=Decimal(40)) for i in range(3)
        )
        self.assertIn("ownership.conflict", self.codes(self.evaluate(*thirds)))

    def test_management_priority_uncertainty_and_reconciliation(self):
        state = self.state(
            status=None, stored_status="active", history_uncertain=True
        )
        codes = self.codes(
            self.evaluate(managed_id="m", state=state, show_management=True)
        )
        self.assertEqual(codes, {"management.history_uncertain"})
        state = self.state(
            status="inactive",
            stored_status="active",
            has_confirmed_history=True,
        )
        self.assertEqual(
            self.codes(
                self.evaluate(
                    managed_id="m", state=state, show_management=True
                )
            ),
            {"management.needs_reconciliation"},
        )
        self.assertEqual(
            self.codes(
                self.evaluate(
                    managed_id="m", state=self.state(), show_management=True
                )
            ),
            {"management.pending_without_basis"},
        )
        self.assertFalse(
            self.evaluate(
                managed_id="m",
                state=self.state(has_unverified_basis=True),
                show_management=True,
            )
        )
        self.assertFalse(
            self.evaluate(
                managed_id="m", state=self.state(), show_management=False
            )
        )

    def test_confirmed_basis_without_membership_is_narrower_than_guard(self):
        for status in ("unverified", "disputed", "confirmed"):
            for future in (False, True):
                c = self.claim(
                    status=status,
                    valid_from=(
                        self.day + timedelta(days=20) if future else None
                    ),
                )
                codes = self.codes(self.evaluate(c, show_management=True))
                self.assertEqual(
                    "management.local_basis_without_membership" in codes,
                    status == "confirmed",
                )
                self.assertNotIn(
                    "management.local_basis_without_membership",
                    self.codes(
                        self.evaluate(c, managed_id="m", show_management=True)
                    ),
                )

    def test_release_review_counts_unique_recordings_and_all_three_basis_types(
        self,
    ):
        for kind in (
            "master_ownership",
            "master_administration",
            "distribution",
        ):
            c = self.claim(right_type=kind, status="confirmed")
            facts = ReleaseFacts(
                "rel", "Album", "active", frozenset({"r"}), frozenset(), (c,)
            )
            with self.assertNumQueries(0):
                items = evaluate_release(
                    facts, local_id="p7", on_date=self.day
                )
            self.assertEqual(
                self.codes(items), {"release.managed_only_recordings"}
            )
            if kind != "master_ownership":
                for release, matches in (
                    (None, True),
                    ("rel", True),
                    ("other", False),
                ):
                    facts2 = replace(
                        facts, claims=(replace(c, release_scope_id=release),)
                    )
                    self.assertEqual(
                        "release.without_current_local_basis"
                        not in self.codes(
                            evaluate_release(
                                facts2, local_id="p7", on_date=self.day
                            )
                        ),
                        matches,
                    )
        for status in ("pending", "active", "inactive"):
            facts = ReleaseFacts(
                "rel", "Album", status, frozenset({"r"}), frozenset(), ()
            )
            items = evaluate_release(facts, local_id="p7", on_date=self.day)
            self.assertEqual(len(items), 0 if status == "inactive" else 1)
            if items:
                self.assertEqual(len(items[0].signals), 2)

    def test_shared_context_uses_nonempty_scope_without_implicit_confirmation(
        self,
    ):
        c = self.claim(release_scope_id="r1")
        with self.assertNumQueries(0):
            self.assertEqual(
                claims_applicable_in_release_context((c,), "r1", "p7"), (c,)
            )
            self.assertEqual(
                claims_applicable_in_release_context((c,), None, "p7"), ()
            )
            self.assertEqual(
                claims_applicable_in_release_context(
                    (replace(c, territory_mode="include"),), "r1", "p7"
                ),
                (),
            )

    def test_stable_keys_sort_and_unconfigured_context(self):
        a, b = self.claim(), self.claim(pk="b")
        first, second = self.evaluate(a, b), self.evaluate(b, a)
        self.assertEqual(
            [i.key for i in sorted(first, key=item_sort_key)],
            [i.key for i in sorted(second, key=item_sort_key)],
        )
        facts = RecordingFacts("r", "Sangen", (), (a,), frozenset())
        self.assertEqual(
            evaluate_recording(facts, local_id=None, on_date=self.day), ()
        )
