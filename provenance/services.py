from django.db import transaction

from rights_core.models import VerificationStatus

from catalogue.models import Recording

from .models import AppliedMetadataChange, AssertionDecision, MetadataAssertion


class ConcurrentCatalogueChange(ValueError):
    pass


def _assertion_value(assertion):
    value = assertion.normalized_value
    return assertion.raw_value if value is None else value


SUPPORTED_CATALOGUE_FIELDS = {
    (MetadataAssertion.EntityType.RECORDING, "title"): (Recording, "title"),
    (MetadataAssertion.EntityType.RECORDING, "language"): (Recording, "language"),
}


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
def supersede_assertion(previous, replacement, *, user=None, note=""):
    previous = MetadataAssertion.objects.select_for_update().get(pk=previous.pk)
    replacement.supersedes = previous
    replacement.save()
    previous.status = VerificationStatus.SUPERSEDED
    previous.save(update_fields=("status",))
    AssertionDecision.objects.create(
        assertion=previous,
        decision=VerificationStatus.SUPERSEDED,
        decided_by=user,
        note=note or f"Erstattet av {replacement.pk}",
    )
    return replacement


@transaction.atomic
def apply_assertion(assertion, *, expected_revision, user, confirm=False, note=""):
    assertion = (
        MetadataAssertion.objects.select_for_update()
        .select_related("source_record__source_system")
        .get(pk=assertion.pk)
    )
    mapping = SUPPORTED_CATALOGUE_FIELDS.get(
        (assertion.entity_type, assertion.field_name)
    )
    if not mapping:
        raise ValueError(
            "Dette feltet kan ikke brukes som gjeldende katalogverdi ennå."
        )
    model, attribute = mapping
    target = model.objects.select_for_update().get(pk=assertion.entity_uuid)
    if target.revision != expected_revision:
        raise ConcurrentCatalogueChange(
            "Katalogverdien er endret siden siden ble åpnet. Last inn på nytt og vurder kilden igjen."
        )
    before = getattr(target, attribute)
    after = _assertion_value(assertion)
    if not isinstance(after, str):
        raise ValueError("Kildeverdien har feil datatype for dette feltet.")
    after = after.strip()
    setattr(target, attribute, after)
    target.save(update_fields=(attribute,))
    change = AppliedMetadataChange.objects.create(
        assertion=assertion,
        entity_type=assertion.entity_type,
        entity_uuid=assertion.entity_uuid,
        field_name=assertion.field_name,
        before_value=before,
        after_value=after,
        base_revision=expected_revision,
        result_revision=target.revision,
        changed_by=user,
    )
    if confirm:
        decide_assertion(assertion, VerificationStatus.CONFIRMED, user=user, note=note)
    return change


@transaction.atomic
def correct_assertion(assertion, *, raw_value, user, note=""):
    assertion = MetadataAssertion.objects.select_for_update().get(pk=assertion.pk)
    replacement = MetadataAssertion(
        source_record=assertion.source_record,
        entity_type=assertion.entity_type,
        entity_uuid=assertion.entity_uuid,
        field_name=assertion.field_name,
        raw_value=raw_value.strip(),
    )
    return supersede_assertion(assertion, replacement, user=user, note=note)
