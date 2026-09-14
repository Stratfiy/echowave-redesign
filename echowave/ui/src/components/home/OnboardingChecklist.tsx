"use client";

/**
 * Six steps, each paying part of the Free 1,000 credits (KAN-132).
 *
 * Lives on Home until every step is done, then leaves. Loading it settles
 * any step completed elsewhere, so the card is also the moment the credit
 * lands — which is why the balance chip is told when it does.
 */

import { CheckCircle2, Circle, Coins } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { getOnboardingCreditsApiV1OnboardingCreditsGet } from "@/client/sdk.gen";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";
import { announceBalanceChanged } from "@/lib/billing/balanceEvents";
import { cn } from "@/lib/utils";

type Step = {
  key: string;
  label: string;
  hint: string;
  href: string;
  credits: number;
  done: boolean;
  paid: boolean;
  granted_credits: number;
  enabled: boolean;
};

type Checklist = {
  enabled: boolean;
  free_credits: number;
  granted_credits: number;
  remaining_credits: number;
  complete: boolean;
  steps: Step[];
  just_granted: string[];
};

export function OnboardingChecklist() {
  const { user, loading: authLoading } = useAuth();
  const [list, setList] = useState<Checklist | null>(null);

  useEffect(() => {
    if (authLoading || !user) return;
    let cancelled = false;
    (async () => {
      const response = await getOnboardingCreditsApiV1OnboardingCreditsGet({});
      if (cancelled || response.error || !response.data) return;
      const data = response.data as unknown as Checklist;
      setList(data);
      if (data.just_granted.length > 0) announceBalanceChanged();
    })();
    return () => {
      cancelled = true;
    };
  }, [authLoading, user]);

  if (!list || !list.enabled || list.complete) return null;

  const steps = list.steps.filter((step) => step.enabled);
  const doneCount = steps.filter((step) => step.done).length;

  return (
    <Card data-testid="onboarding-checklist">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <Coins className="h-4 w-4 text-primary" />
              Earn your {list.free_credits.toLocaleString("en-IN")} free credits
            </CardTitle>
            <CardDescription className="mt-1">
              Each step pays as you do it. {doneCount} of {steps.length} done,{" "}
              {list.granted_credits.toLocaleString("en-IN")} credits in so far.
            </CardDescription>
          </div>
          <span className="rounded-full border border-border bg-muted/40 px-2.5 py-1 text-xs tabular-nums text-muted-foreground">
            {list.remaining_credits.toLocaleString("en-IN")} to go
          </span>
        </div>
      </CardHeader>
      <CardContent>
        <ol className="space-y-2">
          {steps.map((step) => (
            <li key={step.key} className="flex items-start gap-3">
              {step.done ? (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
              ) : (
                <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                  {step.done ? (
                    <span className="text-sm text-muted-foreground line-through">{step.label}</span>
                  ) : (
                    <Link href={step.href} className="text-sm font-medium hover:underline">
                      {step.label}
                    </Link>
                  )}
                  <span
                    className={cn(
                      "text-xs tabular-nums",
                      step.done ? "text-emerald-700" : "text-muted-foreground",
                    )}
                  >
                    {step.done ? `+${step.granted_credits || step.credits}` : `+${step.credits}`} credits
                  </span>
                </div>
                {!step.done ? (
                  <p className="text-xs text-muted-foreground">{step.hint}</p>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}
