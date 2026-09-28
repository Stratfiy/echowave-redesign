"""The approval matrix (KAN-160, E-1): who must approve what.

Today any member confirms any card, answers any question and an agent
may move a purchase order to issued on its own. A business that buys on
purchase orders has a rule about that -- above a lakh the owner signs --
and a system that cannot hold the rule is not one they can put their
spend through.

A rule names a **subject** (a register kind such as purchase_order or
tax_invoice, ``card`` for a proposed action, ``decision`` for a bot's
question, or ``*``), an optional **amount band** in paise, and **who**: a
role (admin or owner; owner outranks admin) or a named member. Rules are
ordered; the first that matches decides. No matching rule means what it
meant before this existed: any member may.

**Off, nothing changes.** Behind ``APPROVALS_2026_09_ENABLED``; every check
below answers "allowed" while it is off.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select

from api.db import db_client
from api.db.models import ApprovalRuleModel
from api.enums import ORGANIZATION_ROLE_RANK, OrganizationRole
from api.services import features
from api.services.refused import Refused

CARD = "card"
DECISION = "decision"
ANY = "*"
ROLES = (OrganizationRole.ADMIN.value, OrganizationRole.OWNER.value)
MAX_RULES = 50


class ApprovalRequired(Refused):
    """The act needs somebody else. The message names who."""


@dataclass(frozen=True)
class Rule:
    id: int | None
    subject: str
    min_amount_paise: int | None
    max_amount_paise: int | None
    approver_role: str | None
    approver_user_id: int | None
    position: int = 0

    def covers(self, subject: str, amount_paise: int | None) -> bool:
        if self.subject not in (ANY, subject):
            return False
        if self.min_amount_paise is not None and (
            amount_paise is None or amount_paise < self.min_amount_paise
        ):
            return False
        if self.max_amount_paise is not None and (
            amount_paise is not None and amount_paise >= self.max_amount_paise
        ):
            return False
        return True

    def allows(self, *, role: str | None, user_id: int | None) -> bool:
        if self.approver_user_id is not None:
            return user_id is not None and int(user_id) == int(self.approver_user_id)
        if self.approver_role:
            return ORGANIZATION_ROLE_RANK.get(
                str(role or ""), -1
            ) >= ORGANIZATION_ROLE_RANK.get(self.approver_role, 99)
        return True

    def who(self, people: dict[int, str] | None = None) -> str:
        if self.approver_user_id is not None:
            return (people or {}).get(
                int(self.approver_user_id), f"member {self.approver_user_id}"
            )
        return (
            f"an {self.approver_role}"
            if self.approver_role == "admin"
            else f"the {self.approver_role}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "min_amount_paise": self.min_amount_paise,
            "max_amount_paise": self.max_amount_paise,
            "approver_role": self.approver_role,
            "approver_user_id": self.approver_user_id,
            "position": self.position,
        }


def enabled() -> bool:
    return features.is_on("approvals")


def clean(raw: Any, position: int) -> Rule:
    """A rule from a person's input, or a Refused saying what is wrong."""
    if not isinstance(raw, dict):
        raise Refused("Each rule must be an object.")
    subject = str(raw.get("subject") or ANY).strip().lower()
    role = raw.get("approver_role")
    user_id = raw.get("approver_user_id")
    if role is not None:
        role = str(role).strip().lower()
        if role not in ROLES:
            raise Refused(f"approver_role must be one of {', '.join(ROLES)}.")
    if user_id in ("", None):
        user_id = None
    else:
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            raise Refused("approver_user_id must be a member's id.") from None
    if role is None and user_id is None:
        raise Refused("A rule needs an approver: a role or a member.")

    def paise(name: str) -> int | None:
        value = raw.get(name)
        if value in ("", None):
            return None
        try:
            out = int(value)
        except (TypeError, ValueError):
            raise Refused(f"{name} must be a whole number of paise.") from None
        if out < 0:
            raise Refused(f"{name} cannot be negative.")
        return out

    lo, hi = paise("min_amount_paise"), paise("max_amount_paise")
    if lo is not None and hi is not None and hi <= lo:
        raise Refused("max_amount_paise must be more than min_amount_paise.")
    return Rule(
        id=None,
        subject=subject,
        min_amount_paise=lo,
        max_amount_paise=hi,
        approver_role=role,
        approver_user_id=user_id,
        position=position,
    )


def first_match(
    rules: list[Rule], *, subject: str, amount_paise: int | None
) -> Rule | None:
    for rule in sorted(rules, key=lambda r: (r.position, r.id or 0)):
        if rule.covers(subject, amount_paise):
            return rule
    return None


def _rule(row: Any) -> Rule:
    return Rule(
        id=row.id,
        subject=row.subject,
        min_amount_paise=row.min_amount_paise,
        max_amount_paise=row.max_amount_paise,
        approver_role=row.approver_role,
        approver_user_id=row.approver_user_id,
        position=int(row.position or 0),
    )


async def rules_of(organization_id: int) -> list[Rule]:
    async with db_client.async_session() as session:
        result = await session.execute(
            select(ApprovalRuleModel)
            .where(ApprovalRuleModel.organization_id == organization_id)
            .order_by(ApprovalRuleModel.position, ApprovalRuleModel.id)
        )
        return [_rule(r) for r in result.scalars().all()]


async def save_rules(organization_id: int, raw_rules: list[Any]) -> list[Rule]:
    """Replace the workspace's rules with these, in this order."""
    if len(raw_rules) > MAX_RULES:
        raise Refused(f"At most {MAX_RULES} rules.")
    cleaned = [clean(raw, position) for position, raw in enumerate(raw_rules)]
    async with db_client.async_session() as session:
        await session.execute(
            delete(ApprovalRuleModel).where(
                ApprovalRuleModel.organization_id == organization_id
            )
        )
        for rule in cleaned:
            session.add(
                ApprovalRuleModel(
                    organization_id=organization_id,
                    subject=rule.subject,
                    min_amount_paise=rule.min_amount_paise,
                    max_amount_paise=rule.max_amount_paise,
                    approver_role=rule.approver_role,
                    approver_user_id=rule.approver_user_id,
                    position=rule.position,
                )
            )
        await session.commit()
    return await rules_of(organization_id)


async def check(
    organization_id: int,
    *,
    subject: str,
    amount_paise: int | None,
    user_id: int | None,
    role: str | None = None,
) -> Rule | None:
    """May this person approve this? Returns the rule that applied (None when
    none did) or raises ApprovalRequired naming who must."""
    if not enabled():
        return None
    rules = await rules_of(organization_id)
    rule = first_match(rules, subject=subject, amount_paise=amount_paise)
    if rule is None:
        return None
    if role is None and user_id:
        membership = await db_client.get_membership(int(user_id), int(organization_id))
        role = getattr(membership, "role", None) if membership else None
    if rule.allows(role=role, user_id=user_id):
        return rule
    raise ApprovalRequired(f"This needs approval by {rule.who()}.")
