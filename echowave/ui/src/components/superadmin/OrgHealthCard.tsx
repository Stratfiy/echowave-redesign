"use client";

import { AlertTriangle, CircleCheck } from "lucide-react";
import type { ReactNode } from "react";

import { StatTile } from "@/components/charts/primitives";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTimeIST } from "@/lib/billing/format";

import {
    channelsSummary,
    COPY,
    kycLabel,
    type OrgHealth,
    type RecentFailure,
    trialSummary,
    trialTone,
} from "./orgHealth";

/**
 * "Is this account healthy?" in one card at the top of the drill-down
 * (ADMIN-2, A2). Presentational: the page fetches, this renders. `actions`
 * is where the trial controls and the impersonate button sit, so the answer
 * and what to do about it are on the same card.
 */
export function OrgHealthCard({
    health,
    failures,
    actions,
}: {
    health: OrgHealth;
    failures: RecentFailure[];
    actions?: ReactNode;
}) {
    const trial = health.trial;
    const trialSub = trial.on_trial && trial.ends_at
        ? `${trial.stage === "ended" ? "Ended" : "Ends"} ${formatDateTimeIST(trial.ends_at)}${trial.override ? " · set by staff" : ""}`
        : health.plan_is_paid
          ? "Paid plan"
          : undefined;

    return (
        <Card>
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">{COPY.healthTitle}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                    <StatTile
                        label={COPY.plan}
                        value={trialSummary(health)}
                        sub={trialSub}
                        tone={trialTone(trial)}
                    />
                    <StatTile
                        label={COPY.agents}
                        value={String(health.agents_count)}
                        sub={`${health.live_agents_count} live`}
                    />
                    <StatTile
                        label={COPY.channels}
                        value={String(health.channels_linked)}
                        sub={channelsSummary(health.channels)}
                    />
                    <StatTile label={COPY.kyc} value={kycLabel(health.kyc_status)} />
                    <StatTile
                        label={COPY.ownKeys}
                        value={health.byok_keys_present ? "Yes" : "No"}
                        sub={
                            health.byok_keys_present
                                ? health.byok_providers.join(", ")
                                : COPY.ownKeysNone
                        }
                    />
                </div>

                {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}

                <section aria-label={COPY.recentFailures} className="space-y-2">
                    <h3 className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-[0.07em] text-muted-foreground">
                        <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                        {COPY.recentFailures}
                    </h3>
                    {failures.length === 0 ? (
                        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
                            <CircleCheck className="h-4 w-4" aria-hidden />
                            {COPY.noFailures}
                        </p>
                    ) : (
                        <ul className="divide-y divide-border rounded-md border border-border text-sm">
                            {failures.map((failure) => (
                                <li key={failure.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 py-2">
                                    <span className="whitespace-nowrap text-xs text-muted-foreground tabular-nums">
                                        {failure.at ? formatDateTimeIST(failure.at) : "—"}
                                    </span>
                                    {failure.workflow_name && (
                                        <span className="font-medium">{failure.workflow_name}</span>
                                    )}
                                    <span className="min-w-0 flex-1 break-words">{failure.summary}</span>
                                </li>
                            ))}
                        </ul>
                    )}
                </section>
            </CardContent>
        </Card>
    );
}

