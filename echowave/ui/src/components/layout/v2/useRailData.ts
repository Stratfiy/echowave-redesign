"use client";

import { useEffect, useState } from "react";

import {
  getBalanceApiV1BillingBalanceGet,
  getPlanApiV1BillingPlanGet,
  teamStatusApiV1TeamStatusGet,
} from "@/client/sdk.gen";
import type { TeamMember } from "@/client/types.gen";
import { useAuth } from "@/lib/auth";

/** The trial window, as /billing/plan reports it once PR #496 is in. */
export type TrialInfo = {
  onTrial: boolean;
  active: boolean;
  daysLeft: number | null;
  days: number | null;
};

export type RailData = {
  colleagues: TeamMember[];
  trial: TrialInfo | null;
  creditsPaise: number | null;
};

const EMPTY: RailData = { colleagues: [], trial: null, creditsPaise: null };

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Read the `trial` block off a /billing/plan answer. The generated client
 * does not know about it yet (it arrives with PR #496), so it is read as
 * untyped data and anything that is not a trial reads as no trial.
 */
export function parseTrial(plan: unknown): TrialInfo | null {
  if (!plan || typeof plan !== "object") return null;
  const trial = (plan as { trial?: unknown }).trial;
  if (!trial || typeof trial !== "object") return null;
  const t = trial as Record<string, unknown>;
  if (t.on_trial !== true) return null;
  return {
    onTrial: true,
    active: t.active === true,
    daysLeft: asNumber(t.days_left),
    days: asNumber(t.days),
  };
}

/**
 * Everything the v2 rail shows beyond links: the roster, the trial and the
 * credits. Three reads the app already makes elsewhere (the old rail's
 * roster, the billing page, the balance chip), fired once auth is ready.
 * Each fails on its own: a missing piece hides its section, never the rail.
 */
export function useRailData(): RailData {
  const { user, loading: authLoading } = useAuth();
  const [data, setData] = useState<RailData>(EMPTY);

  useEffect(() => {
    if (authLoading || !user) return;
    let cancelled = false;
    const update = (patch: Partial<RailData>) => {
      if (!cancelled) setData((prev) => ({ ...prev, ...patch }));
    };

    teamStatusApiV1TeamStatusGet({ query: { hours: 24 } })
      .then((response) => update({ colleagues: response.data?.members ?? [] }))
      .catch(() => undefined);
    getPlanApiV1BillingPlanGet()
      .then((response) => {
        if (!response.error) update({ trial: parseTrial(response.data) });
      })
      .catch(() => undefined);
    getBalanceApiV1BillingBalanceGet()
      .then((response) => {
        if (response.error) return;
        const paise = (response.data as { balance_paise?: unknown } | undefined)?.balance_paise;
        update({ creditsPaise: asNumber(paise) });
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
    };
  }, [authLoading, user]);

  return data;
}
