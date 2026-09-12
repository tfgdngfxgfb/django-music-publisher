"""Identity conventions shared by canonical entities; no publishing coupling."""

import uuid

from django.core.exceptions import ValidationError
from django.db import models, router, transaction


def validate_not_blank(value):
    """Reject empty or whitespace-only canonical names and titles."""
    if not value or not value.strip():
        raise ValidationError("This value cannot be blank or whitespace only.")


class CanonicalQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise TypeError(
            "Canonical changes must use instance.save() for validation and revision tracking."
        )

    def bulk_update(self, *args, **kwargs):
        raise TypeError("Canonical changes must use instance.save().")

    def bulk_create(self, *args, **kwargs):
        raise TypeError("Canonical creation must use instance.save() for validation.")


class CanonicalModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    revision = models.PositiveBigIntegerField(default=1, editable=False)

    objects = CanonicalQuerySet.as_manager()

    class Meta:
        abstract = True

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._saved_pk = instance.pk
        return instance

    def save(self, *args, **kwargs):
        if getattr(self, "_saved_pk", self.pk) != self.pk:
            raise ValidationError("Canonical UUIDs cannot be changed.")
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            if not self._state.adding:
                current = (
                    type(self).objects.using(using).select_for_update().get(pk=self.pk)
                )
                self.revision = current.revision + 1
            self.full_clean()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                    "revision",
                    "updated_at",
                }
            super().save(*args, **kwargs)
            self._saved_pk = self.pk

    def delete(self, *args, **kwargs):
        if getattr(self, "_saved_pk", self.pk) != self.pk:
            raise ValidationError("Canonical UUIDs cannot be changed.")
        return super().delete(*args, **kwargs)
