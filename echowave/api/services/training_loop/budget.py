"""What one agent's weekly learning loop may spend.

The founder's rule (final): the weekly loop budget per agent is the smaller of
**2% of that agent's model spend over the last four weeks** and a **fixed
ceiling in rupees**. The ceiling is a constant, not a setting.

Nothing runs a loop yet. This is the number a loop will be held to, and the
function that computes it, so the rule exists in one place before anything
depends on it. All money is in paise (1 rupee = 100 paise), as everywhere in
billing.

"Model spend" is what the language models behind the agent cost *us*: the
vendor's price on the model lines of its call receipts
(``call_cost_items.provider_cost_paise``), not what the workspace was charged.
An agent with no spend has a budget of nothing, so a new agent cannot start a
loop on the strength of a ceiling alone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.db import db_client

#: The share of the last four weeks' model spend a week's loop may use.
SPEND_SHARE_PERCENT = 2

#: How far back "recent model spend" looks.
SPEND_WINDOW_DAYS = 28

#: The most any one agent's weekly loop may spend, whatever its spend, in
#: rupees. A constant by the founder's decision.
#:
#: PLACEHOLDER VALUE: the founder has not given the number yet. Change this
#: one line when they do; nothing else reads a different figure.
WEEKLY_LOOP_CEILING_RUPEES = 500

PAISE_PER_RUPEE = 100
WEEKLY_LOOP_CEILING_PAISE = WEEKLY_LOOP_CEILING_RUPEES * PAISE_PER_RUPEE


def weekly_cap_paise(model_spend_last_4_weeks_paise: int | None) -> int:
    """The weekly loop budget for an agent that spent this much on models over
    the last four weeks: the smaller of 2% of it and the ceiling. Never
    negative; rounds down, so the cap is never more than 2%."""
    spend = max(int(model_spend_last_4_weeks_paise or 0), 0)
    share = spend * SPEND_SHARE_PERCENT // 100
    return min(share, WEEKLY_LOOP_CEILING_PAISE)


async def weekly_cap_for_agent(
    *, organization_id: int, workflow_id: int, now: datetime | None = None
) -> int:
    """``weekly_cap_paise`` for a real agent, from its own receipts. The agent
    is read through its workspace: another workspace's agent has no spend here
    and gets nothing."""
    since = (now or datetime.now(UTC)) - timedelta(days=SPEND_WINDOW_DAYS)
    spend = await db_client.agent_model_spend_paise(
        organization_id=organization_id, workflow_id=workflow_id, since=since
    )
    return weekly_cap_paise(spend)
