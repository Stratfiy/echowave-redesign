"""Everything a brand-new account needs before it can do anything.

An account is not usable the moment its user row exists: it needs an
organization, a membership, that organization selected, and a default model
configuration. Password signup did all of this inline, which was fine while it
was the only way in. It stopped being fine the moment a second door — Google —
had to produce an identically-provisioned account, because two copies of this
sequence drift, and the way they drift is that one door starts handing out
accounts subtly less complete than the other.

Kept deliberately dumb: it takes a user that already exists and returns the
organization it attached. Deciding *whether* to create the user is the caller's
job, and the two callers decide it very differently.
"""

from __future__ import annotations

from loguru import logger

from api.db import db_client
from api.db.models import OrganizationModel, UserModel
from api.enums import OrganizationConfigurationKey, OrganizationRole
from api.services.auth.depends import create_user_configuration_with_mps_key
from api.services.configuration.ai_model_configuration import (
    convert_legacy_ai_model_configuration_to_v2,
)


async def provision_new_account(user: UserModel) -> OrganizationModel:
    """Give ``user`` an organization, select it, and seed a default config.

    The founder is the **owner** of the organization they just created. That
    reads as obvious and was not the behaviour: ``add_user_to_organization``
    defaults to MEMBER, so everybody who signed up ended up a member of their
    own account — unable to change a role, remove anybody, or invite anybody,
    on an organization nobody else was in. The role machinery was enforced
    correctly all along; the founder simply never got the role.

    Only when the organization is new. Somebody joining an existing one gets
    whatever role they were given, and re-provisioning must never quietly
    promote them — which ``add_user_to_organization`` also guarantees, since it
    leaves an existing membership's role untouched.
    """
    org_provider_id = f"org_{user.provider_id}"
    (
        organization,
        was_created,
    ) = await db_client.get_or_create_organization_by_provider_id(
        org_provider_id=org_provider_id, user_id=user.id
    )

    await db_client.add_user_to_organization(
        user.id,
        organization.id,
        role=(
            OrganizationRole.OWNER.value
            if was_created
            else OrganizationRole.MEMBER.value
        ),
    )
    await db_client.update_user_selected_organization(user.id, organization.id)

    # Best-effort, exactly as it was in signup. A default model configuration
    # is a convenience; failing to build one must not cost somebody the account
    # they just created, and they can pick their own stack afterwards.
    try:
        mps_config = await create_user_configuration_with_mps_key(
            user.id, organization.id, user.provider_id
        )
        if mps_config:
            await db_client.update_user_configuration(user.id, mps_config)
            model_config_v2 = convert_legacy_ai_model_configuration_to_v2(mps_config)
            await db_client.upsert_configuration(
                organization.id,
                OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
                model_config_v2.model_dump(mode="json", exclude_none=True),
            )
    except Exception:
        logger.warning(
            "Failed to create default configuration for new account", exc_info=True
        )

    return organization
