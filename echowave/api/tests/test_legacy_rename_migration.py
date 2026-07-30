"""The rebrand renamed columns inside a migration that had already run.

That is invisible to every test, every fresh install and every CI run — a
database created after the edit has the new names and is correct. Only a
database created *before* it is broken, and it is broken permanently, because
alembic is long past the revision that would have renamed anything. The symptom
is `UndefinedColumnError` on every query touching the table.

So the test here is not "does the migration work" — that was verified by
replaying a legacy-shaped database. It is the cheaper, more durable guard: if
somebody renames another column the same way, the mapping must grow with it.
"""

from api.alembic.versions.f2b9d47c30ae_rename_legacy_dograh_columns import RENAMES
from api.db import models

#: The brand that was renamed away. Any column still carrying the new brand in
#: the models must have a documented path from its old name.
NEW_MARKER = "decibyl"
OLD_MARKER = "dograh"


def _model_columns_carrying_the_brand() -> set[tuple[str, str]]:
    return {
        (table.name, column.name)
        for table in models.Base.metadata.tables.values()
        for column in table.columns
        if NEW_MARKER in column.name
    }


class TestEveryRenamedColumnHasAPath:
    def test_the_mapping_covers_every_branded_column(self):
        """A column named `*_decibyl_*` that no migration renames is a column
        that exists on new databases and not on old ones."""
        covered = {(table, new) for table, _old, new in RENAMES}

        assert _model_columns_carrying_the_brand() <= covered, (
            "A model column carries the new brand but nothing renames it from "
            "the old one. Either add it to RENAMES, or — if it never existed "
            "under the old name — this test needs to know that explicitly."
        )

    def test_each_entry_actually_renames_old_to_new(self):
        for table, old, new in RENAMES:
            assert OLD_MARKER in old, f"{table}.{old} is not an old-brand name"
            assert NEW_MARKER in new, f"{table}.{new} is not a new-brand name"
            assert old != new

    def test_the_target_column_is_one_the_models_declare(self):
        """A rename to a column nothing reads would be silent dead DDL."""
        declared = {
            (table.name, column.name)
            for table in models.Base.metadata.tables.values()
            for column in table.columns
        }
        for table, _old, new in RENAMES:
            assert (table, new) in declared, f"{table}.{new} is not in the models"

    def test_downgrade_does_not_reintroduce_the_old_name(self):
        """Putting the old name back would recreate the same split, pointing the
        other way — a database downgraded once could never be upgraded again
        without hitting this a second time."""
        import inspect

        from api.alembic.versions import (
            f2b9d47c30ae_rename_legacy_dograh_columns as migration,
        )

        source = inspect.getsource(migration.downgrade)
        assert OLD_MARKER not in source
