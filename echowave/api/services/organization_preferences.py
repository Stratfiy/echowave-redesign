from inspect import isawaitable

from loguru import logger
from pydantic import ValidationError

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.schemas.organization_preferences import OrganizationPreferences


async def get_organization_preferences(
    organization_id: int | None,
    db=None,
) -> OrganizationPreferences:
    if organization_id is None:
        return OrganizationPreferences()

    db = db or db_client
    row = await _get_configuration(
        db,
        organization_id,
        OrganizationConfigurationKey.ORGANIZATION_PREFERENCES.value,
    )
    if row is None:
        row = await _get_configuration(
            db,
            organization_id,
            OrganizationConfigurationKey.MODEL_CONFIGURATION_PREFERENCES.value,
        )
    return _parse_preferences(row.value if row is not None else None, organization_id)


#: Fields only staff may change. A customer's save carries whatever the
#: screen last read, so these are taken from what is stored, never from the
#: request — otherwise a stale form would quietly switch an entitlement off,
#: or a crafted one switch it on.
STAFF_ONLY_FIELDS = ("own_keys_allowed",)


def with_staff_fields(
    incoming: OrganizationPreferences, existing: OrganizationPreferences
) -> OrganizationPreferences:
    """The stored preferences with whatever this request actually asked to change.

    A PUT here is a whole object, so every field the caller leaves out arrives
    carrying its schema default -- and the save then wrote that default over
    what was stored. The Settings form sends two fields, so saving a timezone
    from that screen silently switched ``byok_fallback_to_managed`` off, which
    decides whether a call with no usable customer key runs on Decibyl's key or
    is refused outright. Nothing on the screen mentions the field, and nothing
    told the operator it had changed.

    ``model_fields_set`` is the difference between "sent as false" and "not
    sent", which the parsed model alone cannot express. Only the fields a
    request names are applied; everything else keeps its stored value, and the
    staff-only fields keep theirs whatever the request says -- otherwise a
    stale form would quietly switch an entitlement off, or a crafted one switch
    it on.
    """
    named = incoming.model_fields_set - set(STAFF_ONLY_FIELDS)
    return existing.model_copy(update={name: getattr(incoming, name) for name in named})


async def upsert_organization_preferences(
    organization_id: int,
    preferences: OrganizationPreferences,
) -> OrganizationPreferences:
    await db_client.upsert_configuration(
        organization_id,
        OrganizationConfigurationKey.ORGANIZATION_PREFERENCES.value,
        preferences.model_dump(mode="json", exclude_none=True),
    )
    return preferences


async def _get_configuration(db, organization_id: int, key: str):
    row = db.get_configuration(organization_id, key)
    if isawaitable(row):
        row = await row
    return row


def _parse_preferences(value, organization_id: int) -> OrganizationPreferences:
    if not value or not isinstance(value, dict):
        return OrganizationPreferences()
    try:
        return OrganizationPreferences.model_validate(value)
    except ValidationError as exc:
        logger.warning(
            "Invalid organization preferences for organization "
            f"{organization_id}: {exc}. Returning defaults."
        )
        return OrganizationPreferences()
