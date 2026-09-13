from django.db import transaction

from rights_core.models import VerificationStatus

from .models import AssertionDecision, MetadataAssertion


@transaction.atomic
def decide_assertion(assertion, decision, *, user=None, note=""):
    allowed = {
        VerificationStatus.CONFIRMED,
        VerificationStatus.DISPUTED,
        VerificationStatus.REJECTED,
    }
    if decision not in allowed:
        raise ValueError("Ugyldig avgjørelse for metadatapåstand.")
    assertion = MetadataAssertion.objects.select_for_update().get(pk=assertion.pk)
    result = AssertionDecision.objects.create(
        assertion=assertion, decision=decision, decided_by=user, note=note
    )
    assertion.status = decision
    assertion.save(update_fields=("status",))
    return result


@transaction.atomic
def supersede_assertion(previous, replacement):
    previous = MetadataAssertion.objects.select_for_update().get(pk=previous.pk)
    replacement.supersedes = previous
    replacement.save()
    previous.status = VerificationStatus.SUPERSEDED
    previous.save(update_fields=("status",))
    AssertionDecision.objects.create(
        assertion=previous,
        decision=VerificationStatus.SUPERSEDED,
        note=f"Erstattet av {replacement.pk}",
    )
    return replacement
