"""One head, or the deploy stops.

`scripts/ci_deploy.sh` runs `alembic upgrade head` unattended. With two heads
that command does not pick one — it fails with *Multiple head revisions are
present*, and the deploy halts after the new image is already going out.

Two heads is the normal consequence of ordinary work: two branches cut from the
same parent, each adding a migration, each perfectly fine alone and green in its
own CI. Nothing is wrong until they are both on main, which is the one moment
nobody is running migrations by hand. It happened here — an OAuth credential
type and a password-recovery column, both parented on `e9b4c72a1f36`.

So this is a merge-time guard rather than a test of any behaviour. It fails on
the pull request that would create the second head, where the fix is a one-line
`alembic merge` and nobody is waiting on a deploy.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).resolve().parents[1]


def _script_directory() -> ScriptDirectory:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


class TestTheMigrationGraph:
    def test_there_is_exactly_one_head(self):
        """The assertion the deploy makes, made here instead."""
        heads = _script_directory().get_heads()
        assert len(heads) == 1, (
            f"{len(heads)} migration heads: {sorted(heads)}. "
            "`alembic upgrade head` refuses to choose, so the deploy will stop. "
            'Create a merge revision — `alembic merge -m "merge" '
            f"{' '.join(sorted(heads))}` — and commit it with this change."
        )

    def test_every_revision_is_reachable_from_that_head(self):
        """A migration whose parent does not exist never runs, and nothing says
        so: the table it was meant to create is simply missing in production."""
        script = _script_directory()
        head = script.get_heads()[0]
        reachable = {rev.revision for rev in script.iterate_revisions(head, "base")}
        every = {rev.revision for rev in script.walk_revisions()}
        orphaned = every - reachable
        assert not orphaned, (
            f"{len(orphaned)} migration(s) are not on the path from base to "
            f"{head} and will never run: {sorted(orphaned)}"
        )
