"""Top-up packs: what a customer can buy outright, and what it grants.

KAN-55. Four packs, and the credits-per-rupee ratio rises with the pack the
way it rises with the plan: ₹500 and ₹1,000 buy credits at face (2.0 a rupee,
fifty paise a credit), ₹5,000 buys 10,500 (2.1) and ₹20,000 buys 44,000
(2.2). The extra is a *bonus* the ledger has to carry as paise, because the
ledger is paise: a ₹5,000 pack credits ₹5,250 of balance against a ₹5,000
invoice, and the difference is recorded on the payment so a reconciliation
can explain it.

Top-up credits never expire and are spent after plan credits — see
``plans._consumed_since`` for the ordering, which is what makes a pack a pool
of its own without a second ledger.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.services.billing.credits import PAISE_PER_CREDIT


@dataclass(frozen=True)
class Pack:
    code: str
    #: What the customer pays, net of GST.
    price_paise: int
    #: What lands on the balance, in credits.
    credits: int

    @property
    def credit_paise(self) -> int:
        return self.credits * PAISE_PER_CREDIT

    @property
    def bonus_paise(self) -> int:
        """Balance granted beyond the price paid. Zero on the small packs."""
        return self.credit_paise - self.price_paise

    @property
    def bonus_credits(self) -> int:
        return self.bonus_paise // PAISE_PER_CREDIT

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "price_paise": self.price_paise,
            "credits": self.credits,
            "bonus_credits": self.bonus_credits,
        }


PACKS: tuple[Pack, ...] = (
    Pack("p500", 50_000, 1_000),
    Pack("p1000", 100_000, 2_000),
    Pack("p5000", 500_000, 10_500),
    Pack("p20000", 2_000_000, 44_000),
)

PACKS_BY_CODE: dict[str, Pack] = {pack.code: pack for pack in PACKS}


def pack_for(code: str) -> Pack | None:
    return PACKS_BY_CODE.get((code or "").strip().lower())


def packs_as_dicts() -> list[dict]:
    return [pack.as_dict() for pack in PACKS]
