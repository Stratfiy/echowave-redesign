"""Database access for telephony phone numbers.

Phone numbers are first-class entities (PSTN, SIP URI, or SIP extension)
owned by a telephony configuration. They power both outbound caller-ID
selection and inbound call routing.
"""

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import (
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    WorkflowModel,
)
from api.utils.telephony_address import normalize_telephony_address


class TelephonyPhoneNumberClient(BaseDBClient):
    async def list_phone_numbers_for_config(
        self, telephony_configuration_id: int
    ) -> List[TelephonyPhoneNumberModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel)
                .where(
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == telephony_configuration_id
                )
                .order_by(TelephonyPhoneNumberModel.created_at)
            )
            return list(result.scalars().all())

    async def list_phone_numbers_with_workflow_name_for_config(
        self, telephony_configuration_id: int
    ) -> List[Tuple[TelephonyPhoneNumberModel, Optional[str]]]:
        """Same as :meth:`list_phone_numbers_for_config` but also returns the
        inbound workflow's display name (or None) for each row, fetched via a
        single LEFT JOIN so we don't load entire workflow rows."""
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel, WorkflowModel.name)
                .join(
                    WorkflowModel,
                    WorkflowModel.id == TelephonyPhoneNumberModel.inbound_workflow_id,
                    isouter=True,
                )
                .where(
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == telephony_configuration_id
                )
                .order_by(TelephonyPhoneNumberModel.created_at)
            )
            return [(row, name) for row, name in result.all()]

    async def list_active_normalized_addresses_for_config(
        self, telephony_configuration_id: int
    ) -> List[str]:
        """Active phone numbers as canonical address strings (E.164 for PSTN,
        normalized SIP otherwise) — the shape providers want in their
        ``from_numbers`` list for caller-ID and rate-limit pool keys."""
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel.address_normalized)
                .where(
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == telephony_configuration_id,
                    TelephonyPhoneNumberModel.is_active.is_(True),
                )
                .order_by(TelephonyPhoneNumberModel.created_at)
            )
            return [row[0] for row in result.all()]

    async def get_phone_number(
        self, phone_number_id: int
    ) -> Optional[TelephonyPhoneNumberModel]:
        async with self.async_session() as session:
            return await session.get(TelephonyPhoneNumberModel, phone_number_id)

    async def get_phone_number_for_config(
        self, phone_number_id: int, telephony_configuration_id: int
    ) -> Optional[TelephonyPhoneNumberModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel).where(
                    TelephonyPhoneNumberModel.id == phone_number_id,
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == telephony_configuration_id,
                )
            )
            return result.scalars().first()

    async def get_phone_number_for_org(
        self, phone_number_id: int, organization_id: int
    ) -> Optional[TelephonyPhoneNumberModel]:
        """One number, scoped to its owning organization.

        The release path needs this rather than the config-scoped lookup: a
        caller asking to release a number knows the number's id but not
        necessarily which configuration holds it, and resolving through the
        config first would mean trusting an id pair the caller supplied.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel).where(
                    TelephonyPhoneNumberModel.id == phone_number_id,
                    TelephonyPhoneNumberModel.organization_id == organization_id,
                )
            )
            return result.scalars().first()

    async def list_normalized_addresses_for_organization(
        self, organization_id: int
    ) -> list[str]:
        """Every number this account holds, plus our shared outbound pool.

        The loop guard for missed-call callback. Both halves are needed and for
        different reasons: an account's own agent dialling its own callback
        number is the likely accident (it is the obvious way to try the feature
        out), and the shared pool is the number a trial account dials *from*,
        so it would otherwise be the one number guaranteed to reach a callback
        line and start a conversation between two of our own agents.

        Returns the normalised form, because that is what the inbound lookup
        matches on — a set built from the display column would miss a number
        stored as "+91 98765 43210" and compared as "919876543210".
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel.address_normalized).where(
                    or_(
                        TelephonyPhoneNumberModel.organization_id == organization_id,
                        TelephonyPhoneNumberModel.is_shared_outbound.is_(True),
                    )
                )
            )
            return [row for row in result.scalars().all() if row]

    async def find_active_phone_number_for_inbound(
        self,
        organization_id: int,
        address: str,
        provider: str,
        country_hint: Optional[str] = None,
    ) -> Optional[TelephonyPhoneNumberModel]:
        """Inbound routing primary lookup: normalize the called address and find
        the matching active row whose config is for the detected provider."""
        normalized = normalize_telephony_address(address, country_hint=country_hint)

        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel)
                .join(
                    TelephonyConfigurationModel,
                    TelephonyConfigurationModel.id
                    == TelephonyPhoneNumberModel.telephony_configuration_id,
                )
                .where(
                    TelephonyPhoneNumberModel.organization_id == organization_id,
                    TelephonyPhoneNumberModel.address_normalized
                    == normalized.canonical,
                    TelephonyPhoneNumberModel.is_active.is_(True),
                    TelephonyConfigurationModel.provider == provider,
                )
            )
            return result.scalars().first()

    async def find_inbound_route_by_account(
        self,
        provider: str,
        account_id_field: str,
        account_id: str,
        to_number: str,
        country_hint: Optional[str] = None,
        organization_id: Optional[int] = None,
    ) -> Optional[Tuple[TelephonyConfigurationModel, TelephonyPhoneNumberModel]]:
        """Combined primary-path lookup for inbound dispatch.

        One SQL roundtrip that joins ``telephony_configurations`` and
        ``telephony_phone_numbers`` and matches all of:
        provider, ``credentials[account_id_field] == account_id``,
        ``phone.address_normalized == canonical(to_number)``, and
        ``phone.is_active``. Replaces the previous pattern of resolving the
        config and the phone number in two separate queries with a Python-side
        loop over candidate configs.

        Returns ``(config, phone_number)`` or None when the primary path
        misses (e.g. legacy non-E.164 stored addresses); the caller should
        fall back to the fuzzy ``numbers_match`` path in that case.
        """
        if not (provider and account_id_field and account_id and to_number):
            return None

        normalized = normalize_telephony_address(to_number, country_hint=country_hint)

        async with self.async_session() as session:
            stmt = (
                select(TelephonyConfigurationModel, TelephonyPhoneNumberModel)
                .join(
                    TelephonyPhoneNumberModel,
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == TelephonyConfigurationModel.id,
                )
                .where(
                    TelephonyConfigurationModel.provider == provider,
                    TelephonyConfigurationModel.credentials.op("->>")(account_id_field)
                    == account_id,
                    TelephonyPhoneNumberModel.address_normalized
                    == normalized.canonical,
                    TelephonyPhoneNumberModel.is_active.is_(True),
                    # Decibyl's own shared caller IDs are outbound only. They
                    # sit on one row borrowed by every account, so matching one
                    # here would answer an inbound call as whichever tenant the
                    # database happened to return.
                    TelephonyPhoneNumberModel.is_shared_outbound.is_(False),
                )
            )
            if organization_id is not None:
                stmt = stmt.where(
                    TelephonyConfigurationModel.organization_id == organization_id
                )
            result = await session.execute(stmt)
            row = result.first()
            if not row:
                return None
            return row[0], row[1]

    async def set_shared_outbound(self, phone_number_id: int, *, shared: bool = True):
        """Lend one of our numbers to every account, or take it back.

        Staff-only at the route. No organization scope on purpose: the number is
        Decibyl's, and which customer's configuration it happens to hang off is
        an implementation detail of where we parked it.
        """
        async with self.async_session() as session:
            row = await session.get(TelephonyPhoneNumberModel, phone_number_id)
            if row is None:
                return None
            row.is_shared_outbound = shared
            await session.commit()
            await session.refresh(row)
            return row

    async def list_shared_outbound_numbers(self):
        """Decibyl's own caller IDs, in service, across every configuration.

        Deliberately not organization-scoped: these are ours and are lent to
        every account, which is the whole point. The rows they return are never
        matched for inbound — see `is_shared_outbound` on the model.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel)
                .where(
                    TelephonyPhoneNumberModel.is_shared_outbound.is_(True),
                    TelephonyPhoneNumberModel.is_active.is_(True),
                )
                .order_by(TelephonyPhoneNumberModel.id)
            )
            return list(result.scalars().all())

    async def find_inbound_route_by_number(
        self,
        provider: str,
        to_number: str,
        country_hint: Optional[str] = None,
    ) -> Optional[Tuple[TelephonyConfigurationModel, TelephonyPhoneNumberModel]]:
        """Fallback dispatch when the webhook carries no usable account id.

        ``find_inbound_route_by_account`` is the primary path and needs the
        carrier's account id. Plivo does not always send one — this repo's own
        Plivo provider says so in as many words ("AuthID is not always present
        in Plivo webhooks (undocumented field)") — and a number held on a
        *subaccount* sends the subaccount's id, which never equals the parent
        credential we stored. In both cases the primary lookup misses and the
        caller hears "this number is not configured" on a number that is
        configured correctly.

        Matches on provider plus the called number alone, and **refuses to
        guess**: if more than one active row matches, this returns None rather
        than picking whichever Postgres happened to return first. That
        ambiguity is exactly what the account id disambiguates, so resolving it
        arbitrarily would route one customer's caller into another customer's
        agent — a worse outcome than a refused call.
        """
        if not (provider and to_number):
            return None

        normalized = normalize_telephony_address(to_number, country_hint=country_hint)

        async with self.async_session() as session:
            stmt = (
                select(TelephonyConfigurationModel, TelephonyPhoneNumberModel)
                .join(
                    TelephonyPhoneNumberModel,
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == TelephonyConfigurationModel.id,
                )
                .where(
                    TelephonyConfigurationModel.provider == provider,
                    TelephonyPhoneNumberModel.address_normalized
                    == normalized.canonical,
                    TelephonyPhoneNumberModel.is_active.is_(True),
                    TelephonyPhoneNumberModel.is_shared_outbound.is_(False),
                )
                # Two is enough to know it is ambiguous; there is no reason to
                # read every row on the platform to find that out.
                .limit(2)
            )
            result = await session.execute(stmt)
            rows = result.all()

        if len(rows) != 1:
            if len(rows) > 1:
                logger.warning(
                    "Inbound fallback for {} {} matched {} active rows; "
                    "refusing to guess which organization the call belongs to.",
                    provider,
                    normalized.canonical,
                    len(rows),
                )
            return None
        return rows[0][0], rows[0][1]

    async def find_inbound_routing_conflict(
        self,
        provider: str,
        account_id_field: str,
        account_id: str,
        address: str,
        country_hint: Optional[str] = None,
    ) -> Optional[Tuple[TelephonyConfigurationModel, TelephonyPhoneNumberModel]]:
        """Inbound dispatch keys on (provider, credentials[account_id_field],
        address_normalized) — see ``find_inbound_route_by_account``. That tuple
        must be globally unique or two orgs would race for the same call.

        Returns the conflicting (config, phone_number) — possibly in another
        org — when inserting a row with this combination would break that
        invariant, or None when the row is safe to insert. Returns None for
        providers that don't carry an account_id (e.g. ARI), which use a
        different inbound path.
        """
        if not (provider and account_id_field and account_id):
            return None

        normalized = normalize_telephony_address(address, country_hint=country_hint)

        async with self.async_session() as session:
            stmt = (
                select(TelephonyConfigurationModel, TelephonyPhoneNumberModel)
                .join(
                    TelephonyPhoneNumberModel,
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == TelephonyConfigurationModel.id,
                )
                .where(
                    TelephonyConfigurationModel.provider == provider,
                    TelephonyConfigurationModel.credentials.op("->>")(account_id_field)
                    == account_id,
                    TelephonyPhoneNumberModel.address_normalized
                    == normalized.canonical,
                    # A shared outbound caller ID is not reachable inbound, so
                    # it cannot make anything ambiguous — and excluding it is
                    # what lets the same number be lent to every account
                    # without tripping the global uniqueness this enforces.
                    TelephonyPhoneNumberModel.is_shared_outbound.is_(False),
                )
            )
            result = await session.execute(stmt)
            row = result.first()
            return (row[0], row[1]) if row else None

    async def create_phone_number(
        self,
        organization_id: int,
        telephony_configuration_id: int,
        address: str,
        country_code: Optional[str] = None,
        label: Optional[str] = None,
        inbound_workflow_id: Optional[int] = None,
        callback_workflow_id: Optional[int] = None,
        is_active: bool = True,
        is_default_caller_id: bool = False,
        extra_metadata: Optional[Dict[str, Any]] = None,
        status: str = "active",
        carrier_number_id: Optional[str] = None,
    ) -> TelephonyPhoneNumberModel:
        normalized = normalize_telephony_address(address, country_hint=country_code)

        async with self.async_session() as session:
            if is_default_caller_id:
                await self._clear_default_caller_id(session, telephony_configuration_id)

            row = TelephonyPhoneNumberModel(
                organization_id=organization_id,
                telephony_configuration_id=telephony_configuration_id,
                address=address,
                address_normalized=normalized.canonical,
                address_type=normalized.address_type,
                country_code=country_code or normalized.country_code,
                label=label,
                inbound_workflow_id=inbound_workflow_id,
                callback_workflow_id=callback_workflow_id,
                is_active=is_active,
                is_default_caller_id=is_default_caller_id,
                extra_metadata=extra_metadata or {},
                status=status,
                carrier_number_id=carrier_number_id,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise e
            await session.refresh(row)
            return row

    async def update_phone_number(
        self,
        phone_number_id: int,
        telephony_configuration_id: int,
        label: Optional[str] = None,
        inbound_workflow_id: Optional[int] = None,
        is_active: Optional[bool] = None,
        country_code: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
        clear_inbound_workflow: bool = False,
        status: Optional[str] = None,
        carrier_number_id: Optional[str] = None,
        provisioned_at=None,
        inbound_contact_list_id: Optional[int] = None,
        clear_inbound_contact_list: bool = False,
        inbound_require_known_caller: Optional[bool] = None,
        inbound_max_calls_per_caller: Optional[int] = None,
        inbound_call_window_hours: Optional[int] = None,
        inbound_allow_list: Optional[list] = None,
        callback_workflow_id: Optional[int] = None,
        clear_callback_workflow: bool = False,
    ) -> Optional[TelephonyPhoneNumberModel]:
        """Partial update. ``address`` is intentionally immutable — create a new
        row instead. Set ``clear_inbound_workflow=True`` to null out the FK."""
        async with self.async_session() as session:
            row = await session.get(TelephonyPhoneNumberModel, phone_number_id)
            if not row or row.telephony_configuration_id != telephony_configuration_id:
                return None

            if label is not None:
                row.label = label
            if inbound_workflow_id is not None:
                row.inbound_workflow_id = inbound_workflow_id
            elif clear_inbound_workflow:
                row.inbound_workflow_id = None
            if callback_workflow_id is not None:
                row.callback_workflow_id = callback_workflow_id
            elif clear_callback_workflow:
                row.callback_workflow_id = None
            if is_active is not None:
                row.is_active = is_active
            if country_code is not None:
                row.country_code = country_code
            if extra_metadata is not None:
                row.extra_metadata = extra_metadata
            if status is not None:
                row.status = status
            if carrier_number_id is not None:
                row.carrier_number_id = carrier_number_id
            if provisioned_at is not None:
                row.provisioned_at = provisioned_at

            if inbound_contact_list_id is not None:
                row.inbound_contact_list_id = inbound_contact_list_id
            elif clear_inbound_contact_list:
                row.inbound_contact_list_id = None
            if inbound_require_known_caller is not None:
                row.inbound_require_known_caller = inbound_require_known_caller
            if inbound_max_calls_per_caller is not None:
                # <= 0 is the form's way of saying unlimited, and NULL is how
                # the column says it. Storing 0 or -1 verbatim would make the
                # guard's "no limit" test depend on which of them was typed.
                row.inbound_max_calls_per_caller = (
                    inbound_max_calls_per_caller
                    if inbound_max_calls_per_caller > 0
                    else None
                )
            if inbound_call_window_hours is not None:
                row.inbound_call_window_hours = inbound_call_window_hours
            if inbound_allow_list is not None:
                row.inbound_allow_list = inbound_allow_list

            await session.commit()
            await session.refresh(row)
            return row

    async def set_default_caller_id(
        self, phone_number_id: int, telephony_configuration_id: int
    ) -> Optional[TelephonyPhoneNumberModel]:
        async with self.async_session() as session:
            row = await session.get(TelephonyPhoneNumberModel, phone_number_id)
            if not row or row.telephony_configuration_id != telephony_configuration_id:
                return None
            await self._clear_default_caller_id(session, telephony_configuration_id)
            row.is_default_caller_id = True
            await session.commit()
            await session.refresh(row)
            return row

    async def get_default_caller_id(
        self, telephony_configuration_id: int
    ) -> Optional[TelephonyPhoneNumberModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel).where(
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == telephony_configuration_id,
                    TelephonyPhoneNumberModel.is_default_caller_id.is_(True),
                )
            )
            return result.scalars().first()

    async def delete_phone_number(
        self, phone_number_id: int, telephony_configuration_id: int
    ) -> bool:
        async with self.async_session() as session:
            row = await session.get(TelephonyPhoneNumberModel, phone_number_id)
            if not row or row.telephony_configuration_id != telephony_configuration_id:
                return False
            await session.delete(row)
            await session.commit()
            return True

    @staticmethod
    async def _clear_default_caller_id(
        session, telephony_configuration_id: int
    ) -> None:
        await session.execute(
            update(TelephonyPhoneNumberModel)
            .where(
                TelephonyPhoneNumberModel.telephony_configuration_id
                == telephony_configuration_id,
                TelephonyPhoneNumberModel.is_default_caller_id.is_(True),
            )
            .values(is_default_caller_id=False)
        )
