"use client";

/**
 * The campaign aggregate, on the screen the customer already looks at.
 *
 * `GET /campaign/{id}/summary` was built to answer Telangana tender §10 —
 * connection rate, completion rate, retry statistics, language distribution,
 * daily progress — none of which are derivable from the per-call CSV the page
 * already offers. It shipped without a caller, so the only way to read it was
 * the raw API.
 *
 * Two definitions on this card are deliberate and are the reason the numbers
 * here will not match a naive reading of the runs table. Both come from
 * `services/reports/campaign_summary.py` and are surfaced as help text rather
 * than left for someone to rediscover:
 *
 * - **Completion rate is over connected calls, not attempts.** A call nobody
 *   picked up never got the chance to complete a conversation, so counting it
 *   in the denominator reports the agent as failing at something it never
 *   attempted.
 * - **Reach is over the contacts we were given, not the dials we made.** A
 *   retried contact is two attempts and one contact, and a reach target is
 *   written against the list.
 */

import { AlertTriangle, BarChart3 } from "lucide-react";
import { useEffect, useState } from "react";

import { getCampaignSummaryApiV1CampaignCampaignIdSummaryGet } from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { detailFromResult } from "@/lib/apiError";

type Totals = {
    attempted: number;
    connected: number;
    completed: number;
    connection_rate: number | null;
    completion_rate: number | null;
    talk_minutes: number;
    contacts_total: number | null;
    contact_connection_rate: number | null;
    circuit_breaker_trips: number;
};

type Retries = {
    retry_attempts: number;
    contacts_retried: number;
    retries_pending: number;
    by_reason: Record<string, number>;
};

type LanguageRow = { language: string; calls: number; share: number | null };

type DailyRow = {
    date: string;
    attempted: number;
    connected: number;
    completed: number;
    connection_rate: number | null;
    talk_minutes: number;
};

type Summary = {
    totals: Totals;
    retries: Retries;
    languages: LanguageRow[];
    daily: DailyRow[];
};

/**
 * A rate with nothing in its denominator is null, never 0 — the codebase-wide
 * rule (HANDOVER.md §6). A campaign that has not dialled has measured
 * nothing, and "0%" reads as total failure.
 */
function formatRate(rate: number | null | undefined): string {
    if (rate === null || rate === undefined) return "—";
    return `${(rate * 100).toFixed(1)}%`;
}

function formatNumber(value: number | null | undefined): string {
    if (value === null || value === undefined) return "—";
    return value.toLocaleString("en-IN");
}

function Figure({
    label,
    value,
    sub,
    help,
}: {
    label: string;
    value: string;
    sub?: string;
    help?: string;
}) {
    const labelNode = help ? (
        <Tooltip>
            <TooltipTrigger asChild>
                <span className="cursor-help underline decoration-dotted underline-offset-2">
                    {label}
                </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">{help}</TooltipContent>
        </Tooltip>
    ) : (
        label
    );

    return (
        <div className="rounded-lg border p-3">
            <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                {labelNode}
            </p>
            <p className="mt-1.5 text-xl font-semibold tabular-nums">{value}</p>
            {sub && <p className="mt-1 text-xs text-muted-foreground">{sub}</p>}
        </div>
    );
}

export function CampaignSummaryCard({ campaignId }: { campaignId: number }) {
    const [summary, setSummary] = useState<Summary | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await getCampaignSummaryApiV1CampaignCampaignIdSummaryGet({
                path: { campaign_id: campaignId },
            });
            if (cancelled) return;
            if (result.error) {
                setError(
                    detailFromResult(result, "Failed to load campaign summary"),
                );
            } else {
                setSummary(result.data as unknown as Summary);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [campaignId]);

    const totals = summary?.totals;
    const retries = summary?.retries;

    return (
        <Card className="mb-6">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <BarChart3 className="h-4 w-4" />
                    Performance
                </CardTitle>
                <CardDescription>
                    Connection and completion rates, retries, and language mix for this
                    campaign.
                </CardDescription>
            </CardHeader>
            <CardContent>
                {loading && (
                    <div className="h-24 animate-pulse rounded-lg bg-foreground/[0.04]" />
                )}

                {!loading && error && (
                    <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                        <AlertTriangle className="h-4 w-4 shrink-0" />
                        {error}
                    </div>
                )}

                {!loading && !error && totals && (
                    <div className="space-y-5">
                        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                            <Figure
                                label="Connection rate"
                                value={formatRate(totals.connection_rate)}
                                sub={`${formatNumber(totals.connected)} connected of ${formatNumber(totals.attempted)} dials`}
                                help="Connected calls as a share of dials attempted. A retried contact counts as two attempts."
                            />
                            <Figure
                                label="Completion rate"
                                value={formatRate(totals.completion_rate)}
                                sub={`${formatNumber(totals.completed)} completed`}
                                help="Over connected calls, not attempts. A call nobody answered never got the chance to complete a conversation, so counting it here would report the agent as failing at something it never attempted."
                            />
                            <Figure
                                label="Reach"
                                value={formatRate(totals.contact_connection_rate)}
                                sub={`of ${formatNumber(totals.contacts_total)} contacts supplied`}
                                help="Measured against the contact list you supplied, not the dials we made — a retried contact is two attempts but one contact. This is the figure a reach target is written in."
                            />
                            <Figure
                                label="Talk time"
                                value={`${formatNumber(totals.talk_minutes)} min`}
                                sub={
                                    totals.circuit_breaker_trips > 0
                                        ? `${formatNumber(totals.circuit_breaker_trips)} circuit-breaker pause(s)`
                                        : undefined
                                }
                            />
                        </div>

                        {retries && retries.retry_attempts > 0 && (
                            <div className="rounded-lg border p-3">
                                <p className="text-sm font-medium">Retries</p>
                                <p className="mt-1 text-sm text-muted-foreground">
                                    {formatNumber(retries.retry_attempts)} retry
                                    attempt(s) across{" "}
                                    {formatNumber(retries.contacts_retried)} contact(s)
                                    {retries.retries_pending > 0
                                        ? `, ${formatNumber(retries.retries_pending)} still queued`
                                        : ""}
                                    .
                                </p>
                                {Object.keys(retries.by_reason).length > 0 && (
                                    <ul className="mt-2 flex flex-wrap gap-2">
                                        {Object.entries(retries.by_reason).map(
                                            ([reason, count]) => (
                                                <li key={reason}>
                                                    <Badge variant="secondary">
                                                        {reason}: {formatNumber(count)}
                                                    </Badge>
                                                </li>
                                            ),
                                        )}
                                    </ul>
                                )}
                            </div>
                        )}

                        {(summary?.languages.length ?? 0) > 0 && (
                            <div>
                                <p className="mb-2 text-sm font-medium">Languages</p>
                                <ul className="flex flex-wrap gap-2">
                                    {summary!.languages.map((row) => (
                                        <li
                                            key={row.language}
                                            className="rounded-md border px-3 py-1.5 text-xs"
                                        >
                                            <span className="font-medium capitalize">
                                                {row.language}
                                            </span>
                                            <span className="ml-2 text-muted-foreground">
                                                {formatNumber(row.calls)} calls ·{" "}
                                                {formatRate(row.share)}
                                            </span>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        {(summary?.daily.length ?? 0) > 0 && (
                            <div>
                                <p className="mb-2 text-sm font-medium">
                                    Daily progress
                                </p>
                                <div className="overflow-x-auto">
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Date</TableHead>
                                                <TableHead className="text-right">
                                                    Attempted
                                                </TableHead>
                                                <TableHead className="text-right">
                                                    Connected
                                                </TableHead>
                                                <TableHead className="text-right">
                                                    Completed
                                                </TableHead>
                                                <TableHead className="text-right">
                                                    Connection rate
                                                </TableHead>
                                                <TableHead className="text-right">
                                                    Talk min
                                                </TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {summary!.daily.map((row) => (
                                                <TableRow key={row.date}>
                                                    <TableCell className="whitespace-nowrap">
                                                        {row.date}
                                                    </TableCell>
                                                    <TableCell className="text-right tabular-nums">
                                                        {formatNumber(row.attempted)}
                                                    </TableCell>
                                                    <TableCell className="text-right tabular-nums">
                                                        {formatNumber(row.connected)}
                                                    </TableCell>
                                                    <TableCell className="text-right tabular-nums">
                                                        {formatNumber(row.completed)}
                                                    </TableCell>
                                                    <TableCell className="text-right tabular-nums">
                                                        {formatRate(row.connection_rate)}
                                                    </TableCell>
                                                    <TableCell className="text-right tabular-nums">
                                                        {formatNumber(row.talk_minutes)}
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>
                                {/* Bucketed by IST calendar day server-side. In UTC the
                                    boundary sits 5h30m off an operator's own day, so a
                                    call at 01:00 IST would be filed under yesterday. */}
                                <p className="mt-2 text-xs text-muted-foreground">
                                    Days are IST calendar days.
                                </p>
                            </div>
                        )}
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
