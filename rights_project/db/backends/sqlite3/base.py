"""Preserve DMP's legacy index during SQLite table rebuilds on Django 5.2.

DMP 0009 rebuilds WorkAcknowledgement before 0010 renames its index_together
index. Django's rendered historical model no longer contains index_together,
so SQLite drops that index. Preserve just this known historical index, without
editing upstream migrations, changing their graph or faking applied migrations.
"""

from django.db import models
from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteWrapper
from django.db.backends.sqlite3.schema import DatabaseSchemaEditor


class DmpCompatibleSchemaEditor(DatabaseSchemaEditor):
    def _remake_table(self, model, *args, **kwargs):
        legacy = []
        if model._meta.db_table == "music_publisher_workacknowledgement":
            with self.connection.cursor() as cursor:
                indexes = self.connection.introspection.get_constraints(
                    cursor, model._meta.db_table
                )
            legacy = [
                name
                for name, details in indexes.items()
                if details["index"]
                and not details["unique"]
                and details["columns"] == ["society_code", "remote_work_id"]
            ]
        super()._remake_table(model, *args, **kwargs)
        if legacy:
            with self.connection.cursor() as cursor:
                remaining = self.connection.introspection.get_constraints(
                    cursor, model._meta.db_table
                )
            if not any(
                d["index"]
                and not d["unique"]
                and d["columns"] == ["society_code", "remote_work_id"]
                for d in remaining.values()
            ):
                self.add_index(
                    model,
                    models.Index(
                        fields=["society_code", "remote_work_id"],
                        name=legacy[0],
                    ),
                )


class DatabaseWrapper(SQLiteWrapper):
    SchemaEditorClass = DmpCompatibleSchemaEditor
