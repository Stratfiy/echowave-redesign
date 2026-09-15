"""One lock for every write to an organisation's credit ledger (KAN-44).

The ledger is append-only and every row carries ``balance_after_paise``, the
running balance at the moment it was written, so a statement can be rendered
without replaying the table. That column is only true if the read of the
balance and the write of the row happen with nothing in between -- and until
this module, only the call-reservation path made that so. Every other writer
(a usage debit, a top-up, a plan grant, a bonus, a manual adjustment) read
the balance as a plain SUM and appended a row. Two of them in the same
instant for the same organisation both read the same sum and both wrote a
stale running balance; the unique indexes stopped the *duplicate* rows, and
nothing stopped the wrong balances.

``lock_organization_ledger`` is ``SELECT ... FOR NO KEY UPDATE`` on the
organisation row, held until the transaction ends. It is the pattern
``reservations.reserve`` already used, lifted out so every writer takes the
same lock on the same row:

- **Per organisation.** It only ever contends with another ledger write on
  the *same* account at the same moment, which is exactly the set of
  operations that must not interleave. Two organisations never wait on
  each other.
- **Re-entrant within a transaction.** A path that reserves, then charges,
  in one session locks once and proceeds; a second ``FOR UPDATE`` on a row
  the transaction already holds is a no-op.
- **Taken as late as possible.** Callers take it right before the balance
  read, after their cheap early-outs (not metered, internal account,
  already written), so the row is held for the read-and-append only.
- **NO KEY UPDATE, not UPDATE.** Several writers do more inside the locked
  window than append the row: the onboarding grant posts an inbox
  notification through its *own* session, and a notification row carries a
  foreign key to the organisation. An insert with a foreign key takes
  ``FOR KEY SHARE`` on the referenced row, which ``FOR UPDATE`` blocks and
  ``FOR NO KEY UPDATE`` does not. With the stronger lock the writer's own
  second connection waited on its first, forever -- Postgres cannot see a
  deadlock across two connections of one process, and the request hung.
  NO KEY UPDATE still excludes every other ledger writer (they all take the
  same lock), which is the whole requirement; it only stops excluding the
  foreign-key checks that were never the race.

A writer that forgets it is caught by ``tests/test_ledger_writes_are_serialised``,
which scans every module that constructs a ``CreditLedgerModel``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import OrganizationModel


async def lock_organization_ledger(
    session: AsyncSession, *, organization_id: int
) -> None:
    """Serialise this transaction against every other ledger write for one
    organisation. Call it immediately before reading the balance a new
    ledger row's ``balance_after_paise`` will be computed from."""
    await session.execute(
        select(OrganizationModel.id)
        .where(OrganizationModel.id == organization_id)
        # key_share=True renders FOR NO KEY UPDATE on Postgres; see the
        # module docstring for why the weaker strength is the right one.
        .with_for_update(key_share=True)
    )


__all__ = ["lock_organization_ledger"]
