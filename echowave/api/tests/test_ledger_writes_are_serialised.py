"""Every credit-ledger write takes the organisation's ledger lock (KAN-44).

Two guards, for the two ways this regresses.

The first is structural, in the style of ``test_silent_absence_guard``: every
module that constructs a ``CreditLedgerModel`` row must take
``lock_organization_ledger`` (or, as the reservation path did first, its own
``FOR UPDATE`` on the organisation row). A new writer that forgets the lock
is caught here, with no database, before it can write a stale
``balance_after_paise`` in production. The rule is a blocklist of
constructors, not an allowlist of files, so a writer added in a new module
is caught too.

The second is behavioural, on the one writer every bot runs through: a
usage debit must lock *before* it reads the balance, not after, and must
still skip the lock entirely on its cheap early-outs, so an already-debited
retry does not queue behind live writes.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy.dialects import postgresql

API_DIR = Path(__file__).resolve().parent.parent
BILLING_DIR = API_DIR / "services" / "billing"
ROUTE_FILES = [API_DIR / "routes" / "billing_dashboard.py"]


def _constructs_ledger_row(source: str) -> bool:
    """Whether the module builds a CreditLedgerModel(...) anywhere."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name == "CreditLedgerModel":
                return True
    return False


def _writer_modules() -> list[Path]:
    candidates = [p for p in BILLING_DIR.glob("*.py") if p.name != "__init__.py"]
    candidates += ROUTE_FILES
    return sorted(p for p in candidates if _constructs_ledger_row(p.read_text()))


class TestEveryWriterTakesTheLock:
    def test_the_scan_finds_the_writers_it_is_meant_to_guard(self):
        # If the scan ever found nothing, the guard would pass vacuously. It
        # must see at least the debit path and the top-up path.
        names = {p.name for p in _writer_modules()}
        assert {"events.py", "payments.py", "costing.py"} <= names

    @pytest.mark.parametrize("module", _writer_modules(), ids=lambda p: p.name)
    def test_a_ledger_writer_locks_the_organisation(self, module: Path):
        source = module.read_text()
        locks = "lock_organization_ledger(" in source or ".with_for_update()" in source
        assert locks, (
            f"{module.name} appends to credit_ledger without taking the "
            "organisation's ledger lock. Call "
            "api.services.billing.ledger_lock.lock_organization_ledger(session, "
            "organization_id=...) immediately before reading the balance that "
            "balance_after_paise is computed from."
        )


def _fake_session(*, existing=None, balance=1_000):
    """A session that records the order of everything the charge did."""
    log: list[tuple[str, object]] = []
    session = SimpleNamespace()

    async def execute(stmt):
        log.append(("execute", stmt))
        return Mock()

    scalars = iter([existing, balance])

    async def scalar(stmt):
        value = next(scalars)
        log.append(("scalar", stmt))
        return value

    session.execute = execute
    session.scalar = scalar
    session.add = lambda row: log.append(("add", row))
    session.flush = AsyncMock()
    return session, log


@pytest.mark.asyncio
class TestTheUsageDebit:
    async def test_it_locks_the_organisation_before_reading_the_balance(self):
        from api.services.billing import events

        session, log = _fake_session()
        with patch(
            "api.services.billing.internal_accounts.is_internal",
            new=AsyncMock(return_value=False),
        ):
            debited = await events.charge(
                session, organization_id=7, event=events.TOOL_CALL, ref_id="c1"
            )

        assert debited > 0
        kinds = [k for k, _ in log]
        # dedupe lookup, then the lock, then the balance read, then the row.
        assert kinds == ["scalar", "execute", "scalar", "add"]
        lock_stmt = log[1][1]
        sql = str(lock_stmt.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE" in sql and "organizations" in sql
        # And the row's running balance came from the locked read.
        row = log[3][1]
        assert row.balance_after_paise == 1_000 - events.paise_for(events.TOOL_CALL)

    async def test_an_already_debited_retry_never_touches_the_lock(self):
        from api.services.billing import events

        session, log = _fake_session(existing=42)
        with patch(
            "api.services.billing.internal_accounts.is_internal",
            new=AsyncMock(return_value=False),
        ):
            debited = await events.charge(
                session, organization_id=7, event=events.TOOL_CALL, ref_id="c1"
            )

        assert debited == 0
        assert [k for k, _ in log] == ["scalar"]

    async def test_an_internal_account_never_touches_the_lock(self):
        from api.services.billing import events

        session, log = _fake_session()
        with patch(
            "api.services.billing.internal_accounts.is_internal",
            new=AsyncMock(return_value=True),
        ):
            debited = await events.charge(
                session, organization_id=7, event=events.TOOL_CALL, ref_id="c1"
            )

        assert debited == 0
        assert log == []
