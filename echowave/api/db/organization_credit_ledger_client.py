from typing import Optional

from sqlalchemy import func, select, text

from api.db.base_client import BaseDBClient
from api.db.models import OrganizationCreditLedgerModel


class OrganizationCreditLedgerClient(BaseDBClient):
    """Client for the prepaid platform-fee credit ledger.

    An organization's balance is always the SUM of its ledger entries — there
    is no separate balance column to drift out of sync. record_entry() takes a
    Postgres transaction-scoped advisory lock keyed by organization_id so
    concurrent writers for the same org can't race on the balance snapshot.
    """

    async def get_balance(
        self, organization_id: int, session=None
    ) -> float:
        if session is None:
            async with self.async_session() as session:
                return await self._get_balance_impl(organization_id, session)
        return await self._get_balance_impl(organization_id, session)

    async def _get_balance_impl(self, organization_id: int, session) -> float:
        result = await session.execute(
            select(
                func.coalesce(func.sum(OrganizationCreditLedgerModel.amount_usd), 0.0)
            ).where(OrganizationCreditLedgerModel.organization_id == organization_id)
        )
        return float(result.scalar() or 0.0)

    async def record_entry(
        self,
        organization_id: int,
        entry_type: str,
        amount_usd: float,
        *,
        workflow_run_id: Optional[int] = None,
        description: Optional[str] = None,
        reference: Optional[str] = None,
        session=None,
    ) -> OrganizationCreditLedgerModel:
        """Append a ledger entry and return it with balance_after_usd populated.

        amount_usd is signed: positive for purchase/trial_grant/upward
        adjustments, negative for charge/refund-reversal/downward adjustments.
        """
        if session is None:
            async with self.async_session() as session:
                return await self._record_entry_impl(
                    organization_id,
                    entry_type,
                    amount_usd,
                    workflow_run_id,
                    description,
                    reference,
                    session,
                    commit=True,
                )
        return await self._record_entry_impl(
            organization_id,
            entry_type,
            amount_usd,
            workflow_run_id,
            description,
            reference,
            session,
            commit=False,
        )

    async def _record_entry_impl(
        self,
        organization_id: int,
        entry_type: str,
        amount_usd: float,
        workflow_run_id: Optional[int],
        description: Optional[str],
        reference: Optional[str],
        session,
        commit: bool,
    ) -> OrganizationCreditLedgerModel:
        # Serializes concurrent writers for this org for the rest of this
        # transaction; released automatically on commit/rollback.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:org_id)"),
            {"org_id": organization_id},
        )
        current_balance = await self._get_balance_impl(organization_id, session)
        entry = OrganizationCreditLedgerModel(
            organization_id=organization_id,
            entry_type=entry_type,
            amount_usd=amount_usd,
            balance_after_usd=current_balance + amount_usd,
            workflow_run_id=workflow_run_id,
            description=description,
            reference=reference,
        )
        session.add(entry)
        if commit:
            await session.commit()
            await session.refresh(entry)
        else:
            await session.flush()
        return entry
