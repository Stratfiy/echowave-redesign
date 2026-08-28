"use client";

/**
 * Unit economics — the screen behind the pricing decision.
 *
 * Every other billing page answers "how much did we bill". This one answers
 * "does a minute make money, and where does it go when it doesn't". It is laid
 * out to be read top to bottom as an argument: what a minute earns, what the
 * pulse costs us to offer, where the cost sits, and how much of the whole
 * picture we know is incomplete.
 *
 * The last section matters most. Unpriced usage makes every margin above it
 * optimistic, so it is on the same screen rather than filed somewhere quieter.
 */

import {
    AlertTriangle,
    Coins,
    Database,
    Gauge,
    Info,
    TrendingDown,
} from "lucide-react";
import { useEffect, useState } from "react";

import { getUnitEconomicsApiV1AdminBillingUnitEconomicsGet } from "@/client/sdk.gen";
import { ChartCard, PanelMessage, StatTile, useAuthReady } from "@/components/charts/primitives";
import { Badge } from "@/components/ui/badge";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { detailFromError } from "@/lib/apiError";
import {
    formatDateIST,
    formatMicrosUsd,
    formatNumber,
    formatPaise,
    formatPaisePerUsd,
    formatPercent,
    formatSecondsCompact,
} from "@/lib/billing/format";
import { cn } from "@/lib/utils";

type ComponentRow = {
    component: string;
    cost_paise: number;
    calls: number;
    paise_per_minute: number;
    share: number | null;
};

type ModelRow = {
    component: string;
    provider: string | null;
    model: string | null;
    cost_paise: number;
    billable_seconds: number;
    calls: number;
    paise_per_minute: number;
};

type AccountRow = {
    organization_id: number;
    name: string;
    calls: number;
    billable_seconds: number;
    revenue_paise: number;
    provider_cost_paise: number;
    margin_paise: number;
    margin_paise_per_minute: number;
};

type Report = {
    calls: number;
    billable_seconds: number;
    billed_seconds: number;
    billable_minutes: number;
    revenue_paise: number;
    revenue_paise_per_minute: number;
    provider_cost_paise: number;
    provider_cost_paise_per_minute: number;
    margin_paise: number;
    margin_paise_per_minute: number;
    margin_pct: number | null;
    revenue_micros_usd_per_minute: number | null;
    margin_micros_usd_per_minute: number | null;
    fx: {
        paise_per_usd: number;
        effective_from: string;
        recorded_at: string;
        source: string | null;
        age_days: number;
        is_stale: boolean;
    } | null;
    components: ComponentRow[];
    models: ModelRow[];
    pulse: {
        pulse_seconds: number;
        competitor_pulse_seconds: number;
        calls: number;
        unaffected_calls: number;
        billed_seconds: number;
        competitor_seconds: number;
        seconds_saved_for_customers: number;
        foregone_paise: number;
        foregone_pct_of_revenue: number | null;
        seconds_saved_per_call: number;
    };
    uncosted: {
        costed_calls: number;
        affected_calls: number;
        unknown_calls: number;
        by_usage: Array<{ usage: string; calls: number }>;
    };
    accounts: AccountRow[];
    // Document-level, not per-minute -- see billing_kpi_client.embedding_
    // ingestion_totals for why this is its own block rather than folded into
    // revenue_paise/provider_cost_paise above. Staff-only, same as the rest
    // of this page: never rendered to a customer as its own line.
    embedding_ingestion: {
        documents: number;
        tokens: number;
        vendor_cost_paise: number;
        charged_paise: number;
        margin_paise: number;
    };
};

const COMPONENT_LABELS: Record<string, string> = {
    stt: "Speech to text",
    llm: "Language model",
    tts: "Text to speech",
    telephony: "Telephony",
    platform: "Platform fee",
    // Priced features. Decibyl revenue like the platform fee, so it carries
    // no provider cost and shows as pure margin.
    addon: "Feature",
};

export default function UnitEconomicsPage() {
    const [report, setReport] = useState<Report | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const authReady = useAuthReady();

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        (async () => {
            setLoading(true);
            const result = await getUnitEconomicsApiV1AdminBillingUnitEconomicsGet({});
            if (cancelled) return;
            if (result.error) {
                setError(detailFromError(result.error, "Failed to load unit economics"));
            } else {
                setReport(result.data as unknown as Report);
                setError(null);
            }
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady]);

    if (error) {
        return (
            <section className="glass-panel flex items-center gap-3 px-5 py-4 text-sm text-destructive">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                {error}
            </section>
        );
    }

    const empty = !loading && (report?.calls ?? 0) === 0;

    return (
        <div className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <StatTile
                    label="Revenue / minute"
                    value={loading ? "…" : formatPaise(report?.revenue_paise_per_minute)}
                    sub={
                        report?.revenue_micros_usd_per_minute
                            ? `${formatMicrosUsd(report.revenue_micros_usd_per_minute)} at today's rate`
                            : undefined
                    }
                />
                <StatTile
                    label="Provider cost / minute"
                    value={
                        loading
                            ? "…"
                            : formatPaise(report?.provider_cost_paise_per_minute)
                    }
                    sub="Passed through at cost"
                />
                <StatTile
                    label="Margin / minute"
                    value={loading ? "…" : formatPaise(report?.margin_paise_per_minute)}
                    sub={
                        report?.margin_micros_usd_per_minute
                            ? formatMicrosUsd(report.margin_micros_usd_per_minute)
                            : undefined
                    }
                    tone={
                        (report?.margin_paise_per_minute ?? 0) < 0 ? "critical" : undefined
                    }
                />
                <StatTile
                    label="Gross margin"
                    value={loading ? "…" : formatPercent(report?.margin_pct ?? null)}
                    sub={
                        report
                            ? `${formatNumber(report.calls)} calls · ${formatNumber(report.billable_minutes)} min`
                            : undefined
                    }
                />
            </div>

            {report?.fx && (
                <section
                    className={cn(
                        "glass-panel flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3.5 text-sm",
                        report.fx.is_stale &&
                            "ring-1 ring-[color:var(--glass-amber-edge)]",
                    )}
                >
                    <span className="flex items-center gap-2 text-muted-foreground">
                        <Coins className="h-4 w-4" />
                        Billing at{" "}
                        <span className="font-medium text-foreground">
                            {formatPaisePerUsd(report.fx.paise_per_usd)} / $1
                        </span>
                    </span>
                    <span className="text-xs text-muted-foreground">
                        Set {formatDateIST(report.fx.recorded_at)}
                        {report.fx.source ? ` · ${report.fx.source}` : ""}
                    </span>
                    {report.fx.is_stale && (
                        <span className="text-xs font-medium text-amber-600 dark:text-amber-400">
                            {report.fx.age_days} days old — the list price is fixed in
                            dollars, so a stale rate quietly costs margin
                        </span>
                    )}
                </section>
            )}

            <PulsePanel report={report} loading={loading} />

            <div className="grid gap-5 lg:grid-cols-2">
                <ChartCard
                    title="Where the money goes"
                    description="Per connected minute, split by component"
                    loading={loading}
                    isEmpty={empty}
                    height={200}
                >
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Component</TableHead>
                                <TableHead className="text-right">Per minute</TableHead>
                                <TableHead className="text-right">Total</TableHead>
                                <TableHead className="text-right">Share of cost</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {(report?.components ?? []).map((row) => (
                                <TableRow key={row.component}>
                                    <TableCell>
                                        {COMPONENT_LABELS[row.component] ?? row.component}
                                        {row.component === "platform" && (
                                            <Badge variant="secondary" className="ml-2">
                                                our fee
                                            </Badge>
                                        )}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {formatPaise(row.paise_per_minute)}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums text-muted-foreground">
                                        {formatPaise(row.cost_paise)}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums text-muted-foreground">
                                        {formatPercent(row.share)}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </ChartCard>

                <ChartCard
                    title="Cost intensity by model"
                    description="What a minute costs on each model we actually ran"
                    loading={loading}
                    isEmpty={empty || (report?.models.length ?? 0) === 0}
                    emptyMessage="No costed provider usage in this period."
                    height={200}
                >
                    <div className="overflow-x-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Model</TableHead>
                                    <TableHead className="text-right">Per minute</TableHead>
                                    <TableHead className="text-right">Total</TableHead>
                                    <TableHead className="text-right">Calls</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {(report?.models ?? []).map((row) => (
                                    <TableRow
                                        key={`${row.component}-${row.provider}-${row.model}`}
                                    >
                                        <TableCell>
                                            <span className="font-medium">
                                                {row.model || row.provider}
                                            </span>
                                            <span className="ml-2 text-xs uppercase tracking-[0.06em] text-muted-foreground">
                                                {row.component}
                                            </span>
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {formatPaise(row.paise_per_minute)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums text-muted-foreground">
                                            {formatPaise(row.cost_paise)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums text-muted-foreground">
                                            {formatNumber(row.calls)}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </div>
                    <p className="mt-3 flex items-start gap-1.5 text-xs text-muted-foreground">
                        <Info className="mt-0.5 h-3 w-3 shrink-0" />
                        A call using two models counts its minutes toward both, so these
                        are cost intensities for comparison, not shares of the invoice.
                    </p>
                </ChartCard>
            </div>

            <IngestionEmbeddingPanel report={report} loading={loading} />

            <ChartCard
                title="Thinnest margins by account"
                description="Worst first — the account losing money on every minute is rarely the biggest"
                loading={loading}
                isEmpty={empty}
                height={200}
            >
                <div className="overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Account</TableHead>
                                <TableHead className="text-right">Minutes</TableHead>
                                <TableHead className="text-right">Revenue</TableHead>
                                <TableHead className="text-right">Provider cost</TableHead>
                                <TableHead className="text-right">Margin</TableHead>
                                <TableHead className="text-right">Margin / min</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {(report?.accounts ?? []).map((row) => (
                                <TableRow key={row.organization_id}>
                                    <TableCell className="font-medium">{row.name}</TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {formatNumber(Math.round(row.billable_seconds / 60))}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {formatPaise(row.revenue_paise)}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums text-muted-foreground">
                                        {formatPaise(row.provider_cost_paise)}
                                    </TableCell>
                                    <TableCell
                                        className={cn(
                                            "text-right tabular-nums",
                                            row.margin_paise < 0 &&
                                                "font-medium text-destructive",
                                        )}
                                    >
                                        {formatPaise(row.margin_paise)}
                                    </TableCell>
                                    <TableCell
                                        className={cn(
                                            "text-right tabular-nums",
                                            row.margin_paise_per_minute < 0 &&
                                                "font-medium text-destructive",
                                        )}
                                    >
                                        {formatPaise(row.margin_paise_per_minute)}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            </ChartCard>

            <UncostedPanel report={report} loading={loading} />
        </div>
    );
}

/**
 * What the 15-second pulse is worth, in both directions.
 *
 * Shown as a cost to us and a saving to customers on the same panel on
 * purpose: the decision this screen supports is whether the differentiator is
 * still affordable, and only one of those two numbers answers that.
 */
function PulsePanel({ report, loading }: { report: Report | null; loading: boolean }) {
    const pulse = report?.pulse;

    return (
        <section className="glass-panel px-5 pb-5 pt-4">
            <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="flex items-center gap-2 text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                        <Gauge className="h-4 w-4 text-[color:var(--brand-amber)]" />
                        The {pulse?.pulse_seconds ?? 15}-second pulse
                    </h2>
                    <p className="mt-0.5 text-xs tracking-[-0.01em] text-muted-foreground">
                        What we give away by not rounding every call up to a whole
                        minute, priced at each account&apos;s own rate.
                    </p>
                </div>
            </div>

            {loading ? (
                <div className="h-24 animate-pulse rounded-xl bg-foreground/[0.04]" />
            ) : !pulse || pulse.calls === 0 ? (
                <PanelMessage height={120}>No costed calls in this period.</PanelMessage>
            ) : (
                <>
                    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                        <Figure
                            label="Revenue foregone"
                            value={formatPaise(pulse.foregone_paise)}
                            sub={
                                pulse.foregone_pct_of_revenue !== null
                                    ? `${formatPercent(pulse.foregone_pct_of_revenue)} of what a whole-minute platform would have billed`
                                    : undefined
                            }
                            tone="amber"
                        />
                        <Figure
                            label="Saved per call"
                            value={formatSecondsCompact(pulse.seconds_saved_per_call)}
                            sub="Averaged across every call, including the ones it did not touch"
                        />
                        <Figure
                            label="Calls it changed nothing for"
                            value={`${formatNumber(pulse.unaffected_calls)} of ${formatNumber(pulse.calls)}`}
                            sub="Durations that already landed on a whole minute"
                        />
                        <Figure
                            label="Time billed"
                            value={`${formatNumber(Math.round(pulse.billed_seconds / 60))} min`}
                            sub={`A 60s-pulse competitor would have billed ${formatNumber(Math.round(pulse.competitor_seconds / 60))}`}
                        />
                    </div>

                    <p className="mt-4 border-t border-foreground/[0.07] pt-3 text-xs leading-relaxed text-muted-foreground">
                        The saving lands almost entirely on short calls. On a 45-second
                        call the pulse is worth a quarter of the bill; on a four-minute
                        one it is a few per cent. That is the traffic the pitch belongs
                        on.
                    </p>
                </>
            )}
        </section>
    );
}

/**
 * What document upload actually costs us, and what it's charged for.
 *
 * Never shown to a customer as a line of its own -- see PRICING-DECISIONS.md.
 * This is the internal view that exists so a future pricing decision here has
 * real numbers behind it instead of a guess: currently absorbed into what the
 * account is billed elsewhere, and this is the margin that absorption costs.
 */
function IngestionEmbeddingPanel({
    report,
    loading,
}: {
    report: Report | null;
    loading: boolean;
}) {
    const ingestion = report?.embedding_ingestion;

    return (
        <section className="glass-panel px-5 pb-5 pt-4">
            <h2 className="flex items-center gap-2 text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                <Database className="h-4 w-4" />
                Knowledge-base ingestion
            </h2>
            <p className="mt-0.5 text-xs tracking-[-0.01em] text-muted-foreground">
                Embedding cost for documents uploaded in this period. Absorbed today,
                not billed as its own line to any customer.
            </p>

            {loading ? (
                <div className="mt-4 h-16 animate-pulse rounded-xl bg-foreground/[0.04]" />
            ) : !ingestion || ingestion.documents === 0 ? (
                <p className="mt-4 text-sm text-muted-foreground">
                    No document ingestion in this period.
                </p>
            ) : (
                <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
                    <Figure
                        label="Documents"
                        value={formatNumber(ingestion.documents)}
                    />
                    <Figure label="Tokens embedded" value={formatNumber(ingestion.tokens)} />
                    <Figure
                        label="Vendor cost"
                        value={formatPaise(ingestion.vendor_cost_paise)}
                        sub="What we paid, before markup"
                    />
                    <Figure
                        label="Charged"
                        value={formatPaise(ingestion.charged_paise)}
                        sub="Debited to the ledger, absorbed elsewhere"
                    />
                    <Figure
                        label="Margin"
                        value={formatPaise(ingestion.margin_paise)}
                        tone={ingestion.margin_paise < 0 ? "amber" : undefined}
                    />
                </div>
            )}
        </section>
    );
}

/**
 * How much of the book we know is incomplete.
 *
 * Deliberately the last thing on the page and deliberately not hidden: unpriced
 * usage is provider cost we paid and did not record, so every margin above is
 * better than the truth by exactly this much.
 */
function UncostedPanel({
    report,
    loading,
}: {
    report: Report | null;
    loading: boolean;
}) {
    const uncosted = report?.uncosted;
    const clean =
        !loading &&
        uncosted &&
        uncosted.affected_calls === 0 &&
        uncosted.unknown_calls === 0;

    return (
        <section
            className={cn(
                "glass-panel px-5 pb-5 pt-4",
                (uncosted?.affected_calls ?? 0) > 0 && "ring-1 ring-destructive/30",
            )}
        >
            <h2 className="flex items-center gap-2 text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                <TrendingDown className="h-4 w-4" />
                Usage we hold no rate for
            </h2>
            <p className="mt-0.5 text-xs tracking-[-0.01em] text-muted-foreground">
                Provider cost we incurred and could not price. Every margin figure above
                is better than the truth by this much.
            </p>

            {loading ? (
                <div className="mt-4 h-16 animate-pulse rounded-xl bg-foreground/[0.04]" />
            ) : clean ? (
                <p className="mt-4 text-sm text-muted-foreground">
                    Every costed call in this period was fully priced.
                </p>
            ) : (
                <div className="mt-4 space-y-3">
                    <div className="flex flex-wrap gap-6 text-sm">
                        <span>
                            <span className="font-medium text-foreground">
                                {formatNumber(uncosted?.affected_calls)}
                            </span>{" "}
                            <span className="text-muted-foreground">
                                of {formatNumber(uncosted?.costed_calls)} calls affected
                            </span>
                        </span>
                        {(uncosted?.unknown_calls ?? 0) > 0 && (
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <span className="text-muted-foreground underline decoration-dotted underline-offset-2">
                                        {formatNumber(uncosted?.unknown_calls)} unknown
                                    </span>
                                </TooltipTrigger>
                                <TooltipContent>
                                    Costed before we started recording this. Not the same
                                    as &ldquo;nothing was missing&rdquo;.
                                </TooltipContent>
                            </Tooltip>
                        )}
                    </div>

                    {(uncosted?.by_usage.length ?? 0) > 0 && (
                        <ul className="flex flex-wrap gap-2">
                            {uncosted!.by_usage.map((row) => (
                                <li
                                    key={row.usage}
                                    className="glass-tile px-3 py-1.5 text-xs"
                                >
                                    <span className="font-mono">{row.usage}</span>
                                    <span className="ml-2 text-muted-foreground">
                                        {formatNumber(row.calls)} calls
                                    </span>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}
        </section>
    );
}

function Figure({
    label,
    value,
    sub,
    tone,
}: {
    label: string;
    value: string;
    sub?: string;
    tone?: "amber";
}) {
    return (
        <div className="glass-tile px-4 py-3">
            <p className="text-[0.6875rem] font-medium uppercase tracking-[0.07em] text-muted-foreground">
                {label}
            </p>
            <p
                className={cn(
                    "mt-1.5 text-[1.375rem] font-semibold leading-[1.1] tracking-[-0.03em] text-foreground",
                    tone === "amber" && "text-[color:var(--brand-amber)]",
                )}
            >
                {value}
            </p>
            {sub && (
                <p className="mt-1 text-xs leading-snug text-muted-foreground">{sub}</p>
            )}
        </div>
    );
}
