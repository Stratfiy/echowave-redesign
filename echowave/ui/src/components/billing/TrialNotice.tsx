"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { useAuth } from "@/lib/auth";

/**
 * Where the account stands on its trial (PLAN-1, KAN-255): the end date
 * while it runs, and what happened once it has ended. Renders nothing for an
 * account that is not on the trial (a paid plan, or the trial feature off),
 * so it can sit on any screen.
 */

export type TrialState = {
  on_trial: boolean;
  active: boolean;
  starts_at: string | null;
  ends_at: string | null;
  days_left: number | null;
  days: number;
};

function formatDay(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "long" });
}

export function TrialLine({ trial }: { trial: TrialState | null | undefined }) {
  if (!trial || !trial.on_trial) return null;
  if (!trial.active) {
    return (
      <div
        className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm"
        role="status"
        data-testid="trial-ended"
      >
        <p className="font-medium">Your trial ended on {formatDay(trial.ends_at)}.</p>
        <p className="mt-1 text-muted-foreground">
          Your agents, threads and reports are all still here. Choose a plan to
          switch them back on.{" "}
          <Link href="/billing" className="font-medium underline underline-offset-4">
            See plans
          </Link>
        </p>
      </div>
    );
  }
  const days = trial.days_left ?? 0;
  return (
    <div className="rounded-lg border bg-muted/40 p-3 text-sm" role="status" data-testid="trial-active">
      <span className="font-medium">Trial</span>
      <span className="text-muted-foreground">
        {" "}· ends {formatDay(trial.ends_at)} · {days === 1 ? "1 day" : `${days} days`} left.
        Everything is on, including a phone number.{" "}
      </span>
      <Link href="/billing" className="font-medium underline underline-offset-4">
        Plans
      </Link>
    </div>
  );
}

export function TrialNotice() {
  const { user, loading } = useAuth();
  const [trial, setTrial] = useState<TrialState | null>(null);

  useEffect(() => {
    if (loading || !user) return;
    let cancelled = false;
    void (async () => {
      const result = await client.get({ url: "/api/v1/billing/plan" });
      if (cancelled || result.error) return;
      setTrial(((result.data as { trial?: TrialState } | undefined)?.trial) ?? null);
    })();
    return () => {
      cancelled = true;
    };
  }, [loading, user]);

  return <TrialLine trial={trial} />;
}
