"""Care's three consent cards, as the controls action cards run them.

``services/workflow/actions.py`` owns the card: proposal, the payload
version a Confirm approves, the undo window, run-once, audit and events.
This module only says what each care card shows (``resolve``), what
confirming it does (``execute``), how Undo puts it back (``reverse``) and
what a No leaves behind (``declined``).

Every care card carries ``only_user_id``: the older person answers it, not
whoever else can read the workspace's thread.
"""

from __future__ import annotations

from typing import Any

from api.services.care import circle, medicines


def _private(args: dict[str, Any], audit_subject: str) -> dict[str, Any]:
    """Every care card is the older person's alone: ``private_to`` keeps the
    card and the lines under it (which carry an invitation code, a medicine)
    off every colleague's timeline, whatever the private-threads switch
    says, and the workspace audit log records the press under a neutral
    subject rather than the medicine or the family member's name."""
    return {"private_to": args["person_user_id"], "audit_subject": audit_subject}


def _actions():
    from api.services.workflow import actions

    return actions


async def resolve(
    action: str, *, organization_id: int, arguments: dict[str, Any], why: str
) -> dict[str, Any]:
    actions = _actions()
    try:
        if action == actions.CARE_FAMILY_INVITE:
            args = await circle.card_args(organization_id, arguments)
            return {
                "action": action,
                "args": args,
                "label": f"Share with {args['name']} ({circle.mask_email(args['email'])})",
                "why": why or "You asked to add them to your family circle.",
                "effect": (
                    f"{args['name']} will see only: "
                    f"{circle.describe_shares(args['shares'])}. They join with a "
                    "code for their email. You can stop sharing at any time."
                ),
                "reversible": True,
                "state": actions.PROPOSED,
                "only_user_id": args["person_user_id"],
                **_private(args, "Care: a family member added"),
            }
        if action == actions.CARE_FAMILY_SHARE:
            args = await circle.card_args(organization_id, arguments)
            async with circle.db_client.async_session() as session:
                row = await circle._member(
                    session, organization_id, args["person_user_id"], args["member_id"]
                )
                args["previous_shares"] = list(row.shares or [])
            return {
                "action": action,
                "args": args,
                "label": f"Let {args['name']} see more",
                "why": why or "You asked to share more with them.",
                "effect": (
                    f"{args['name']} will see: {circle.describe_shares(args['shares'])}."
                ),
                "reversible": True,
                "state": actions.PROPOSED,
                "only_user_id": args["person_user_id"],
                **_private(args, "Care: what family can see"),
            }
        if action == actions.CARE_MEDICINE_CALLS:
            args = await medicines.card_args(organization_id, arguments)
            if args.get("channel") == medicines.APP:
                return _app_reminder_card(action, organization_id, args, why)
            language = medicines.LANGUAGE_NAMES.get(args["language"], args["language"])
            told = (
                f"If a call is not answered or the medicine is not taken, "
                f"{', '.join(args['alert_names'])} will be told."
                if args["alert_names"]
                else "Nobody else is told."
            )
            return {
                "action": action,
                "args": args,
                "label": (
                    f"Reminder calls for {args['label']}: every day at "
                    f"{medicines.say_times(args['times'])}, in {language}"
                ),
                "why": why or "You asked to be reminded by phone.",
                "effect": (
                    f"Decibyl will ring {medicines.mask_phone(args['phone'])} at "
                    f"these times ({args['timezone']}) and say it is Decibyl "
                    "calling with a medicine reminder. It only reminds; it never "
                    f"gives advice about doses. {told}"
                ),
                "reversible": True,
                "state": actions.PROPOSED,
                "only_user_id": args["person_user_id"],
                **_private(args, "Care: medicine reminders"),
            }
    except circle.CareError as exc:
        raise actions.ActionError(str(exc)) from exc
    raise actions.ActionError("That is not something that can be done.")


def _app_reminder_card(
    action: str, organization_id: int, args: dict[str, Any], why: str
) -> dict[str, Any]:
    """The card for a reminder in Decibyl: no phone, nothing rings."""
    from api import constants
    from api.services import features

    actions = _actions()
    also = (
        " and send it to the phones and browsers where you allowed Decibyl's "
        "notifications"
        if features.is_on("identity_notifications", organization_id)
        else ""
    )
    told = (
        f"If you do not tap I took it within {constants.CARE_CALL_ANSWER_MINUTES} "
        f"minutes, {', '.join(args['alert_names'])} will be told."
        if args["alert_names"]
        else "Nobody else is told."
    )
    return {
        "action": action,
        "args": args,
        "label": (
            f"Reminders for {args['label']}: every day at "
            f"{medicines.say_times(args['times'])}, in Decibyl"
        ),
        "why": why or "You asked to be reminded.",
        "effect": (
            f"Decibyl will remind you in Decibyl at these times ({args['timezone']})"
            f"{also}. No phone number is needed. It only reminds; it never gives "
            f"advice about doses. {told}"
        ),
        "reversible": True,
        "state": actions.PROPOSED,
        "only_user_id": args["person_user_id"],
        **_private(args, "Care: medicine reminders"),
    }


async def execute(organization_id: int, payload: dict[str, Any]) -> str:
    actions = _actions()
    action = payload.get("action")
    args = dict(payload.get("args") or {})
    try:
        if action == actions.CARE_FAMILY_INVITE:
            made = await circle.activate_invite(organization_id, args, event_id=None)
            payload.setdefault("result", {})["invite_code"] = made["code"]
            return made["note"]
        if action == actions.CARE_FAMILY_SHARE:
            return await circle.apply_share(organization_id, args)
        if action == actions.CARE_MEDICINE_CALLS:
            return await medicines.activate(
                organization_id, args, (payload.get("confirmed") or {}).get("version")
            )
    except circle.CareError as exc:
        raise actions.ActionError(str(exc)) from exc
    raise actions.ActionError("That is not something that can be done.")


async def reverse(organization_id: int, payload: dict[str, Any]) -> None:
    actions = _actions()
    action = payload.get("action")
    args = dict(payload.get("args") or {})
    try:
        if action == actions.CARE_FAMILY_INVITE:
            await circle.unshare(organization_id, {**args, "previous_shares": None})
            return
        if action == actions.CARE_FAMILY_SHARE:
            await circle.unshare(organization_id, args)
            return
        if action == actions.CARE_MEDICINE_CALLS:
            await medicines.deactivate(organization_id, args)
            return
    except circle.CareError as exc:
        raise actions.ActionError(str(exc)) from exc
    raise actions.ActionError("This cannot be put back.")


async def declined(organization_id: int, payload: dict[str, Any]) -> None:
    """No, or Undo inside the window: nothing shared, nothing rings."""
    actions = _actions()
    action = payload.get("action")
    args = dict(payload.get("args") or {})
    try:
        if action in (actions.CARE_FAMILY_INVITE, actions.CARE_FAMILY_SHARE):
            await circle.consent_declined(organization_id, args)
        elif action == actions.CARE_MEDICINE_CALLS:
            await medicines.not_started(organization_id, args)
    except circle.CareError:
        return
