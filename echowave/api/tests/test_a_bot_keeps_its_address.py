"""A bot's handle has to survive the trip from the database to the roster.

Two failures this file exists for, both of the shape `api/AGENTS.md` names:
code doing exactly what it was told while something goes missing, with no
error and no symptom until somebody notices.

**The listing query.** The channel roster is built from
``get_all_workflows_for_listing``, which uses ``load_only`` -- an allowlist of
columns. A column left off it is not a slow screen, it is a bot that cannot be
addressed at all, and the person who typed the mention sees a message nobody
answered. The column was in fact missing when the roster was first written.

**The backfill.** The migration assigns every existing bot its address, and
every bot it skips is a bot a customer can see and cannot @-mention. The
numbering is run rather than read, because "it refuses collisions" and "it
numbers collisions" are one word apart in a docstring and opposite in effect.
"""

import ast
from pathlib import Path

API = Path(__file__).resolve().parents[1]


def _listing_columns() -> set[str]:
    """The columns ``get_all_workflows_for_listing`` actually SELECTs.

    Read out of the source rather than by running the query: there is no
    Postgres in unit tests, and the thing worth guarding is the allowlist
    itself, which is a fact about the code.
    """
    tree = ast.parse((API / "db" / "workflow_client.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "get_all_workflows_for_listing"
        ):
            return {
                argument.attr
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "load_only"
                for argument in call.args
                if isinstance(argument, ast.Attribute)
            }
    raise AssertionError("get_all_workflows_for_listing is gone")


class TestTheRosterCanSeeEveryFieldItReads:
    def test_the_handle_is_selected(self):
        assert "handle" in _listing_columns()

    def test_the_name_is_selected(self):
        # The fallback in `resolve` reads it when a handle was never assigned,
        # so losing it would take the fallback with it -- silently.
        assert "name" in _listing_columns()

    def test_the_channel_is_selected(self):
        # The roster is filtered by folder_id. Without it every bot in the
        # account would be addressable in every channel.
        assert "folder_id" in _listing_columns()


class TestTheBackfillGivesEveryBotAnAddress:
    """The migration's own logic, run against the names it will meet."""

    @staticmethod
    def _migration():
        import importlib.util

        path = API / "alembic" / "versions" / "f2c6a83e70d1_bot_handles.py"
        spec = importlib.util.spec_from_file_location("bot_handles_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_it_slugs_the_account_s_real_names(self):
        slug = self._migration()._slug
        assert slug("Narayani Dental front desk") == "narayani-dental-front-desk"
        assert (
            slug("Meera — Decibyl Sales Assistant") == "meera-decibyl-sales-assistant"
        )
        assert slug("Kriti Labs — e-lock support") == "kriti-labs-e-lock-support"

    def test_a_name_with_nothing_sluggable_slugs_to_nothing(self):
        # Left NULL by the migration rather than given a made-up address.
        assert self._migration()._slug("!!!") == ""

    def test_it_never_exceeds_the_column(self):
        module = self._migration()
        assert len(module._slug("a" * 500)) <= module.MAX_HANDLE

    def test_a_long_slug_does_not_end_on_a_hyphen(self):
        """Truncation can land mid-separator, and "@ops-bot-" is an address
        nobody would type and the mention regex would not produce."""
        module = self._migration()
        name = ("word " * 60).strip()
        assert not module._slug(name).endswith("-")
