"""Database access for telephony configurations.

Each row represents one provider account that an organization has connected
(e.g. "Twilio US prod", "Vobiz IN sandbox"). Replaces the single-row-per-org
``OrganizationConfiguration(TELEPHONY_CONFIGURATION)`` storage.
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import CampaignModel, TelephonyConfigurationModel


class TelephonyConfigurationInUseError(Exception):
    """Raised when deleting a config that is still referenced by a campaign."""


class TelephonyConfigurationClient(BaseDBClient):
    async def list_telephony_configurations(
        self, organization_id: int
    ) -> List[TelephonyConfigurationModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel)
                .where(TelephonyConfigurationModel.organization_id == organization_id)
                .order_by(TelephonyConfigurationModel.created_at)
            )
            return list(result.scalars().all())

    async def get_telephony_configuration(
        self, config_id: int
    ) -> Optional[TelephonyConfigurationModel]:
        async with self.async_session() as session:
            return await session.get(TelephonyConfigurationModel, config_id)

    async def get_telephony_configuration_for_org(
        self, config_id: int, organization_id: int
    ) -> Optional[TelephonyConfigurationModel]:
        """Lookup scoped to an org — used to authorize per-org access."""
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel).where(
                    TelephonyConfigurationModel.id == config_id,
                    TelephonyConfigurationModel.organization_id == organization_id,
                )
            )
            return result.scalars().first()

    async def get_default_telephony_configuration(
        self, organization_id: int
    ) -> Optional[TelephonyConfigurationModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel).where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                    TelephonyConfigurationModel.is_default_outbound.is_(True),
                )
            )
            return result.scalars().first()

    async def list_telephony_configurations_by_provider(
        self, organization_id: int, provider: str
    ) -> List[TelephonyConfigurationModel]:
        """Used by inbound matching to enumerate candidates of a given provider."""
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel).where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                    TelephonyConfigurationModel.provider == provider,
                )
            )
            return list(result.scalars().all())

    async def count_telnyx_configs_missing_webhook_public_key(
        self, organization_id: int
    ) -> int:
        """Count Telnyx configs in this org with no webhook_public_key in credentials.

        Used by the org-warnings endpoint to surface a UI nudge until customers
        paste their portal-issued public key.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(func.count(TelephonyConfigurationModel.id)).where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                    TelephonyConfigurationModel.provider == "telnyx",
                    (
                        TelephonyConfigurationModel.credentials.op("->>")(
                            "webhook_public_key"
                        ).is_(None)
                    )
                    | (
                        TelephonyConfigurationModel.credentials.op("->>")(
                            "webhook_public_key"
                        )
                        == ""
                    ),
                )
            )
            return int(result.scalar() or 0)

    async def count_vonage_configs_missing_signature_secret(
        self, organization_id: int
    ) -> int:
        """Count Vonage configs in this org with no signature_secret."""
        async with self.async_session() as session:
            result = await session.execute(
                select(func.count(TelephonyConfigurationModel.id)).where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                    TelephonyConfigurationModel.provider == "vonage",
                    (
                        TelephonyConfigurationModel.credentials.op("->>")(
                            "signature_secret"
                        ).is_(None)
                    )
                    | (
                        TelephonyConfigurationModel.credentials.op("->>")(
                            "signature_secret"
                        )
                        == ""
                    ),
                )
            )
            return int(result.scalar() or 0)

    async def list_all_telephony_configurations_by_provider(
        self, provider: str
    ) -> List[TelephonyConfigurationModel]:
        """List configs of a given provider across every organization.

        Used by background workers like the ARI manager that maintain
        long-lived connections per config row, independent of any one org.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel).where(
                    TelephonyConfigurationModel.provider == provider,
                )
            )
            return list(result.scalars().all())

    async def create_telephony_configuration(
        self,
        organization_id: int,
        name: str,
        provider: str,
        credentials: Dict[str, Any],
        is_default_outbound: bool = False,
    ) -> TelephonyConfigurationModel:
        """Create a new config row. Duplicate-account guarding is the caller's
        responsibility; this method does not enforce it."""
        # Imported here, not at module scope: `credential_encryption` reaches
        # the telephony registry, which reaches the provider packages, which
        # import `api.db.db_client` — a cycle that leaves whichever module lost
        # the race half-initialised, and shows up as an unrelated attribute
        # going missing depending on import order.
        from api.services.telephony import credential_encryption

        async with self.async_session() as session:
            existing_count = await session.scalar(
                select(func.count(TelephonyConfigurationModel.id)).where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                )
            )
            if existing_count == 0:
                is_default_outbound = True
            elif is_default_outbound:
                await self._clear_default_outbound(session, organization_id)

            row = TelephonyConfigurationModel(
                organization_id=organization_id,
                name=name,
                provider=provider,
                # Encrypted here rather than in the route, so every writer gets
                # it — including any future one that forgets. The account id is
                # deliberately left readable; inbound dispatch matches it in
                # SQL. See services/telephony/credential_encryption.
                credentials=credential_encryption.encrypt(provider, credentials),
                is_default_outbound=is_default_outbound,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise e
            await session.refresh(row)
            return row

    async def update_telephony_configuration(
        self,
        config_id: int,
        organization_id: int,
        name: Optional[str] = None,
        credentials: Optional[Dict[str, Any]] = None,
    ) -> Optional[TelephonyConfigurationModel]:
        from api.services.telephony import credential_encryption

        async with self.async_session() as session:
            row = await session.get(TelephonyConfigurationModel, config_id)
            if not row or row.organization_id != organization_id:
                return None

            if name is not None:
                row.name = name
            if credentials is not None:
                row.credentials = credential_encryption.encrypt(
                    row.provider, credentials
                )

            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise e
            await session.refresh(row)
            return row

    async def set_default_telephony_configuration(
        self, config_id: int, organization_id: int
    ) -> Optional[TelephonyConfigurationModel]:
        """Mark this config as the org's default outbound, clearing any other default."""
        async with self.async_session() as session:
            row = await session.get(TelephonyConfigurationModel, config_id)
            if not row or row.organization_id != organization_id:
                return None
            await self._clear_default_outbound(session, organization_id)
            row.is_default_outbound = True
            await session.commit()
            await session.refresh(row)
            return row

    async def list_platform_managed_configurations(
        self,
    ) -> List[TelephonyConfigurationModel]:
        """Every configuration running on Decibyl's own carrier account.

        Deliberately not organization-scoped: this is the staff view of what we
        are administering and paying rent for, across all customers. The route
        that exposes it is behind the superuser gate.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel)
                .where(TelephonyConfigurationModel.is_platform_managed.is_(True))
                .order_by(TelephonyConfigurationModel.organization_id)
            )
            return list(result.scalars().all())

    async def set_platform_managed(
        self, config_id: int, *, managed: bool = True
    ) -> Optional[TelephonyConfigurationModel]:
        """Mark a configuration as platform-managed (Decibyl's own carrier account).

        Admin-only operation — no organization_id check. This gates the
        telephony KYC flow: only platform-managed configurations are subject to
        ``assert_may_place_calls``.
        """
        async with self.async_session() as session:
            row = await session.get(TelephonyConfigurationModel, config_id)
            if row is None:
                return None
            row.is_platform_managed = managed
            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise e
            await session.refresh(row)
            return row

    async def delete_telephony_configuration(
        self, config_id: int, organization_id: int
    ) -> bool:
        async with self.async_session() as session:
            row = await session.get(TelephonyConfigurationModel, config_id)
            if not row or row.organization_id != organization_id:
                return False

            campaign_ref = await session.execute(
                select(CampaignModel.id)
                .where(CampaignModel.telephony_configuration_id == config_id)
                .limit(1)
            )
            if campaign_ref.first():
                raise TelephonyConfigurationInUseError(
                    f"Telephony configuration {config_id} is referenced by one or "
                    f"more campaigns and cannot be deleted."
                )

            await session.delete(row)
            await session.commit()
            return True

    @staticmethod
    async def _clear_default_outbound(session, organization_id: int) -> None:
        await session.execute(
            update(TelephonyConfigurationModel)
            .where(
                TelephonyConfigurationModel.organization_id == organization_id,
                TelephonyConfigurationModel.is_default_outbound.is_(True),
            )
            .values(is_default_outbound=False)
        )
