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

from api.constants import UI_APP_URL
from api.db import db_client
from api.db.models import OrganizationModel, UserModel
from api.enums import OrganizationConfigurationKey, OrganizationRole
from api.services.auth import welcome_email
from api.services.auth.depends import create_user_configuration_with_mps_key
from api.services.configuration.ai_model_configuration import (
    convert_legacy_ai_model_configuration_to_v2,
    default_managed_configuration,
    upsert_organization_ai_model_configuration_v2,
)
from api.services.messaging import announce
from api.services.partners import referrals


async def provision_new_account(
    user: UserModel, *, referral_code: str | None = None
) -> OrganizationModel:
    """Give ``user`` an organization, select it, and seed a default config.

    ``referral_code`` attributes the new account to the partner it came
    through. This is the only moment attribution happens — see
    ``OrganizationModel.referred_by_organization_id`` — so both doors pass it,
    and a code that does not resolve is ignored rather than refused. Somebody
    signing up did not choose the code and cannot fix a bad one; costing them
    the account over it would be the wrong trade every time.
    """
    org_provider_id = f"org_{user.provider_id}"
    organization, _ = await db_client.get_or_create_organization_by_provider_id(
        org_provider_id=org_provider_id, user_id=user.id
    )

    # OWNER, not the MEMBER default.
    #
    # The organization is derived from this user's own provider id, so the
    # person being provisioned is the only person who can ever have founded
    # it. Leaving them on the default made a new account's first and only
    # member a MEMBER of a company they had just created: every route behind
    # `require_organization_role(ADMIN)` answered them with a 403 — provider
    # keys, the billing profile, removing a number from the do-not-call list,
    # minting a tool credential — and the one route that could have fixed it,
    # promoting a member, is itself OWNER-gated. There was no way out from
    # inside the product.
    #
    # `add_user_to_organization` is conflict-do-nothing, so a re-provision of a
    # half-finished signup cannot use this to escalate an existing membership.
    await db_client.add_user_to_organization(
        user.id, organization.id, role=OrganizationRole.OWNER.value
    )
    await db_client.update_user_selected_organization(user.id, organization.id)

    # Attribution, before the best-effort block below and inside its own, so a
    # referral is never lost to a model-configuration failure and a referral
    # failure never costs somebody their account. Both are true only if it sits
    # here rather than in either neighbour.
    if referral_code:
        try:
            async with db_client.async_session() as session:
                await referrals.attribute(
                    session,
                    organization=await session.merge(organization),
                    code=referral_code,
                )
                await session.commit()
        except Exception:
            logger.warning(
                f"Could not attribute new organization {organization.id} to a partner",
                exc_info=True,
            )

    # A default model configuration, in two steps: ask the model gateway for a
    # service key, and fall back to plain managed tiers if that does not
    # happen.
    #
    # The fallback is not a nicety. Without it an account provisioned while MPS
    # was unreachable got *no* configuration at all, and every AI surface it
    # touched afterwards refused with "requires an LLM configuration" — the
    # widget, the knowledge base, the first test call. Nothing retried, and
    # nothing said why; the account simply did not work, and the only fix was
    # for its owner to find the model screen and choose a stack.
    #
    # Managed tiers resolve to real vendors on the keys staff installed at
    # /superadmin/provider-keys, so this default needs nothing from MPS to run.
    # The service key it does without only attributes managed usage back to the
    # account.
    seeded = False
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
            seeded = True
    except Exception:
        logger.warning(
            "Could not mint a model gateway key for new account; falling back "
            "to managed tiers on the platform keys",
            exc_info=True,
        )

    if not seeded:
        # Best-effort like everything around it: a new account is worth more
        # than a default stack, and its owner can still pick one by hand.
        try:
            await upsert_organization_ai_model_configuration_v2(
                organization.id, default_managed_configuration()
            )
        except Exception:
            logger.warning(
                "Failed to create default configuration for new account",
                exc_info=True,
            )

    # The welcome, last, and best-effort like everything above it.
    #
    # Addressed to the person who just signed up rather than to the
    # organization's members: at this moment those are the same one address,
    # and `announce` would otherwise mail anybody a later re-provision found
    # attached. Deduplicated per account for ever, so re-provisioning a
    # half-finished signup — which both front doors can do — welcomes nobody
    # twice.
    try:
        address = (user.email or "").strip()
        if address:
            await announce.announce(
                organization_id=organization.id,
                kind=welcome_email.KIND,
                notice=welcome_email.compose(
                    # No name to greet them by: the signup form takes one and
                    # `create_user_with_email` does not store it, and the
                    # Google door has none either. The compose reads correctly
                    # without one rather than falling back to "Hi ,".
                    account_name=None,
                    app_url=UI_APP_URL,
                ),
                to=[address],
            )
    except Exception:
        # `announce` does not raise; this catches the compose, which reads
        # attributes off a user row somebody may have changed under us.
        logger.warning("Could not welcome new account", exc_info=True)

    return organization
