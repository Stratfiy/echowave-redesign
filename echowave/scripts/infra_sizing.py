"""What the Telangana campaign needs to run on, and what that costs.

``INFRASTRUCTURE.md`` describes the architecture; this decides its size. The
two questions it answers are the ones a fleet plan usually asserts without
showing its work:

1. **How many machines?** Derived from the same volume model the quote uses
   (``tender_cost_model.volume``), so a change to call length or connect rate
   moves the fleet as well as the price. A sizing document that drifts from the
   cost model is how you end up provisioning for a campaign nobody is selling.

2. **Does it fit the money?** The bid carries ``infra_rupees_campaign`` —
   ₹95,000 — for infrastructure. That number was set before anyone drew the
   architecture, so it is a constraint to check, not a total to reverse into.
   The script fails loudly when a plan exceeds it rather than quietly rounding.

Run it::

    set -a && source api/.env && set +a && python -m scripts.infra_sizing

**On the prices below.** They are ap-south-1 on-demand list, entered by hand
and *not* fetched from the AWS price list API, which needs credentials. Treat
them the way this codebase treats every other vendor price: as a figure with a
date on it that somebody has to re-verify. Check them against
https://calculator.aws before quoting, and change them here — every derived
number moves with them.

**On concurrency per vCPU.** ``CONCURRENT_CALLS_PER_VCPU`` is the one input
here that is a guess rather than a measurement, and it sets the whole fleet
size. STT, LLM and TTS are all remote, so a media pipeline is mostly waiting —
but VAD inference and 8 kHz resampling are real local CPU, and they run per
stream for the whole call. The default is deliberately conservative. It stays
a guess until the soak test in ``INFRASTRUCTURE.md`` produces a number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from scripts.tender_cost_model import Assumptions, Volume, volume

#: Rupees per US dollar. The same reference the cost model quotes against, so
#: the two documents cannot disagree about what a dollar is.
USD_INR = 96.0

#: Traffic is not flat across the calling window. Mid-morning and early evening
#: carry far more answered calls than the 13:00 lull, and the fleet has to hold
#: the busiest hour rather than the mean. 2.0 is the working figure until the
#: pilot produces a real hourly profile — which it will, and this should then be
#: replaced by the measured ratio.
PEAK_TO_AVERAGE = 2.0

#: Concurrent conversations one vCPU can carry. **A guess. See the module
#: docstring.** The capability assessment quotes 5-8; this takes the low end.
CONCURRENT_CALLS_PER_VCPU = 5.0

#: Fraction of an instance's vCPU left unused at peak. Voice is latency-bound:
#: a box at 90% CPU adds jitter to every stream on it at once, which is heard
#: as the agent stumbling rather than seen as a graph.
HEADROOM = 0.40


@dataclass(frozen=True)
class Instance:
    name: str
    vcpu: int
    memory_gb: int
    #: ap-south-1 on-demand Linux, USD/hour. Verify before quoting.
    usd_per_hour: float


#: Compute. c7i is the current-generation Intel compute-optimised family;
#: media pipelines want clock and memory bandwidth more than they want cores.
COMPUTE = {
    "c7i.large": Instance("c7i.large", 2, 4, 0.1071),
    "c7i.xlarge": Instance("c7i.xlarge", 4, 8, 0.2142),
    "c7i.2xlarge": Instance("c7i.2xlarge", 8, 16, 0.4284),
    "c7i.4xlarge": Instance("c7i.4xlarge", 16, 32, 0.8568),
    "m7i.large": Instance("m7i.large", 2, 8, 0.1176),
    "m7i.xlarge": Instance("m7i.xlarge", 4, 16, 0.2352),
    "m7i.2xlarge": Instance("m7i.2xlarge", 8, 32, 0.4704),
}

#: RDS PostgreSQL, single-AZ USD/hour. Multi-AZ is charged at double.
RDS = {
    "db.t4g.medium": Instance("db.t4g.medium", 2, 4, 0.0930),
    "db.m6g.large": Instance("db.m6g.large", 2, 8, 0.1930),
    "db.r6g.large": Instance("db.r6g.large", 2, 16, 0.2570),
    "db.r6g.xlarge": Instance("db.r6g.xlarge", 4, 32, 0.5140),
}

#: ElastiCache Redis, single-node USD/hour. Multi-AZ needs a replica, so double.
ELASTICACHE = {
    "cache.t4g.medium": Instance("cache.t4g.medium", 2, 3, 0.0850),
    "cache.m6g.large": Instance("cache.m6g.large", 2, 6, 0.1560),
}


@dataclass(frozen=True)
class Schedule:
    """How long each tier is actually switched on.

    The single most expensive mistake available in this budget is billing a
    calendar month for a seven-day campaign. The data tier genuinely has to
    stay up across setup, pilot, campaign and reporting; **the media fleet does
    not** — it only serves calls inside the 09:00-19:00 window, and outside it
    the instances can be stopped. That is the difference between ~120 hours and
    730, and it is worth roughly a lakh.
    """

    #: Setup, pilot, campaign and reporting. The data tier runs throughout.
    campaign_weeks: float = 4.0
    #: Calling window, plus the pilot's own window and a load-test allowance.
    media_hours: float = 120.0
    #: The control node — orchestrator, ARI manager, ARQ workers, the API that
    #: serves the dashboard — cannot be stopped overnight: retries, the nightly
    #: backup and the daily rollup all run outside the calling window.
    control_hours: float = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "control_hours", self.campaign_weeks * 7 * 24)


@dataclass(frozen=True)
class Fleet:
    peak_concurrent: float
    media_vcpu_needed: float
    media_instance: Instance
    media_count: int
    media_capacity: float
    control_instance: Instance
    db_instance: Instance
    db_multi_az: bool
    cache_instance: Instance
    cache_multi_az: bool


def peak_concurrency(v: Volume, peak_factor: float = PEAK_TO_AVERAGE) -> float:
    """Concurrent conversations at the busiest hour.

    ``avg_concurrent_calls`` is conversation-seconds divided by window-seconds.
    Sizing to it would leave the fleet saturated for several hours a day.
    """
    return v.avg_concurrent_calls * peak_factor


def size_media(
    peak: float,
    instance: Instance,
    *,
    per_vcpu: float = CONCURRENT_CALLS_PER_VCPU,
    headroom: float = HEADROOM,
) -> tuple[int, float, float]:
    """``(instance count, vCPU needed, capacity)`` for the media tier.

    Minimum of two regardless of the arithmetic. One media instance is a single
    point of failure for every live call on it, and a campaign against a hard
    connection SLA has no afternoon to spare — an instance replacement takes
    minutes and every call on it drops.
    """
    vcpu_needed = peak / per_vcpu / (1 - headroom)
    per_instance = instance.vcpu * per_vcpu * (1 - headroom)
    count = max(2, math.ceil(peak / per_instance))
    return count, vcpu_needed, count * per_instance


def plan(
    a: Assumptions,
    *,
    media: str = "c7i.2xlarge",
    control: str = "m7i.large",
    db: str = "db.r6g.large",
    cache: str = "cache.t4g.medium",
    db_multi_az: bool = True,
    cache_multi_az: bool = False,
) -> Fleet:
    v = volume(a)
    peak = peak_concurrency(v)
    instance = COMPUTE[media]
    count, vcpu_needed, capacity = size_media(peak, instance)
    return Fleet(
        peak_concurrent=peak,
        media_vcpu_needed=vcpu_needed,
        media_instance=instance,
        media_count=count,
        media_capacity=capacity,
        control_instance=COMPUTE[control],
        db_instance=RDS[db],
        db_multi_az=db_multi_az,
        cache_instance=ELASTICACHE[cache],
        cache_multi_az=cache_multi_az,
    )


def cost(f: Fleet, s: Schedule, v: Volume) -> dict[str, float]:
    """Rupees per line item for the whole campaign.

    Storage and transfer are estimated rather than modelled: recordings are the
    only large object, at roughly 8 kB/s of 8 kHz mono Opus for the length of
    each conversation.
    """
    media = f.media_count * f.media_instance.usd_per_hour * s.media_hours
    control = f.control_instance.usd_per_hour * s.control_hours
    db = f.db_instance.usd_per_hour * (2 if f.db_multi_az else 1) * s.control_hours
    cache = (
        f.cache_instance.usd_per_hour * (2 if f.cache_multi_az else 1) * s.control_hours
    )

    # 100 GB gp3 for the DB, plus the control node's root volume.
    storage = (100 * 0.0912 + 100 * 0.0912) * (s.campaign_weeks / 4.345)

    recording_gb = v.conversation_minutes * 60 * 8_000 / 1e9
    s3 = recording_gb * 0.025 * (s.campaign_weeks / 4.345)

    # NAT gateway and the ALB are the two line items people forget; both are
    # billed per hour whether or not anything is flowing through them.
    nat = 0.056 * s.control_hours + recording_gb * 0.045
    alb = 0.0225 * s.control_hours

    lines = {
        "media fleet": media,
        "control node": control,
        "RDS PostgreSQL": db,
        "ElastiCache Redis": cache,
        "EBS storage": storage,
        "S3 recordings": s3,
        "NAT gateway": nat,
        "load balancer": alb,
    }
    return {k: usd * USD_INR for k, usd in lines.items()}


def report(a: Assumptions | None = None) -> None:
    a = a or Assumptions()
    v = volume(a)
    s = Schedule()

    print("CAPACITY")
    print(f"  conversation minutes            {v.conversation_minutes:>10,.0f}")
    print(f"  average concurrent calls        {v.avg_concurrent_calls:>10.1f}")
    print(
        f"  peak concurrent (x{PEAK_TO_AVERAGE:.1f})           {peak_concurrency(v):>10.1f}"
    )
    print(f"  average SIP channels            {v.avg_sip_channels:>10.1f}")
    print(
        f"  peak SIP channels               "
        f"{v.avg_sip_channels * PEAK_TO_AVERAGE:>10.1f}   <- order this many from the carrier"
    )

    print(
        f"\n  sized at {CONCURRENT_CALLS_PER_VCPU:.0f} concurrent calls per vCPU, {HEADROOM:.0%} headroom"
    )
    print("  ** that ratio is an estimate, not a measurement — see the soak test **")

    for name in ("c7i.xlarge", "c7i.2xlarge", "c7i.4xlarge"):
        f = plan(a, media=name)
        print(
            f"    {name:<14} {f.media_count} x {f.media_instance.vcpu:>2} vCPU "
            f"= capacity {f.media_capacity:>5.0f} concurrent "
            f"(need {f.peak_concurrent:.0f})"
        )

    f = plan(a)
    lines = cost(f, s, v)
    total = sum(lines.values())

    print(
        f"\nFLEET  (media {s.media_hours:.0f} h in-window, data tier {s.control_hours:.0f} h)"
    )
    print(f"  media           {f.media_count} x {f.media_instance.name}")
    print(f"  control         1 x {f.control_instance.name}")
    print(
        f"  database        {f.db_instance.name}"
        f"{' Multi-AZ' if f.db_multi_az else ' single-AZ'}"
    )
    print(
        f"  cache           {f.cache_instance.name}"
        f"{' Multi-AZ' if f.cache_multi_az else ' single-AZ'}"
    )

    print("\nCOST — ap-south-1 on-demand list, whole campaign")
    for name, rupees in sorted(lines.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<20} {rupees:>10,.0f}   {rupees / total:>5.1%}")
    print(f"  {'TOTAL':<20} {total:>10,.0f}")

    budget = a.infra_rupees_campaign
    print(f"\n  budget carried in the bid  {budget:>10,.0f}")
    delta = total - budget
    verdict = "OVER" if delta > 0 else "under"
    print(f"  {verdict:<25} {abs(delta):>10,.0f}")

    if delta > 0:
        print("\n  Ways to close it, cheapest concession first:")
        for label, alt in (
            ("single-AZ database", plan(a, db_multi_az=False)),
            ("db.m6g.large instead of r6g", plan(a, db="db.m6g.large")),
            (
                "both",
                plan(a, db="db.m6g.large", db_multi_az=False),
            ),
        ):
            saved = total - sum(cost(alt, s, v).values())
            print(f"    {label:<30} saves {saved:>8,.0f}")

    print(
        "\n  Reserved instances and Savings Plans are deliberately NOT modelled:"
        "\n  a one-off seven-day campaign cannot commit to a year. They become"
        "\n  the right answer the moment this repeats."
    )


if __name__ == "__main__":
    report()
