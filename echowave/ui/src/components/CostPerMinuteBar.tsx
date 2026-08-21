"use client";

import { AlertTriangle, Info } from "lucide-react";
import { useEffect, useState } from "react";

import { getCostPerMinuteApiV1CostEstimatePerMinutePost } from "@/client/sdk.gen";
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "@/components/ui/tooltip";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatMicrosUsd } from "@/lib/billing/format";
import { cn } from "@/lib/utils";

/** The stack being priced. Any part may be unset while still being chosen. */
export interface CostStack {
    stt_provider?: string | null;
    stt_model?: string;
    llm_provider?: string | null;
    llm_model?: string;
    tts_provider?: string | null;
    tts_model?: string;
    telephony_provider?: string | null;
}

interface EstimateLine {
    component: string;
    provider: string | null;
    model: string | null;
    units_per_minute: number;
    unit_rate_mpaise: number;
    paise_per_minute: number;
    basis: string;
    rate_is_provider_fallback: boolean;
}

interface Estimate {
    total_paise_per_minute: number;
    agent_paise_per_minute: number;
    telephony_paise_per_minute: number;
    platform_paise_per_minute: number;
    // Priced features, summed. Its own group rather than folded into one of
    // the others: an add-on is Decibyl revenue like the platform fee but is
    // charged only when the feature runs, and a bar without it would not add
    // up to the total printed above it.
    addon_paise_per_minute: number;
    lines: EstimateLine[];
    unpriced: string[];
    // The same total in dollars, which is the unit every competitor quotes.
    // Null only when this account is on a rupee-denominated contract, where
    // there is no dollar price to show.
    total_micros_usd_per_minute: number | null;
    pulse_seconds: number;
}

/**
 * The four groups the bar is split into. Fixed order, fixed colour — the
 * segment for a group never moves or changes hue as the stack changes, so the
 * bar can be read at a glance across configurations.
 *
 * Add-ons are the fourth and were missing: they belong to none of the other
 * three, so the segments summed to less than the total beside them for any
 * agent using the knowledge base or post-call QA.
 */
const GROUPS = [
    { key: "agent", label: "Agent cost", colour: "#1baf7a" },
    { key: "telephony", label: "Telephony", colour: "#eb6834" },
    { key: "platform", label: "Platform", colour: "#2a78d6" },
    { key: "addon", label: "Features", colour: "#8b5cf6" },
] as const;

const COMPONENT_LABELS: Record<string, string> = {
    stt: "Transcription",
    llm: "LLM",
    tts: "Voice",
    telephony: "Telephony",
    platform: "Platform fee",
    addon: "Feature",
};

/**
 * Add-on lines carry their catalogue key in `provider`, exactly as they do on
 * a receipt, so one label map serves the estimate and the invoice.
 */
const ADDON_LABELS: Record<string, string> = {
    knowledge_base: "Knowledge base",
    call_qa: "Post-call QA",
};

function rupees(paise: number): string {
    return `₹${(paise / 100).toFixed(2)}`;
}

/**
 * Live per-minute cost for the stack currently selected, itemised.
 *
 * Priced from the same effective-dated rates the invoice will use, so the
 * number shown here and the number billed cannot drift apart.
 */
export function CostPerMinuteBar({
    stack,
    className,
    carriageNote,
}: {
    stack: CostStack;
    className?: string;
    /**
     * Why this total carries no telephony, in the caller's own words.
     *
     * There are two reasons and they need different sentences. No carrier
     * configured means the price is genuinely incomplete and will grow. A
     * carrier the customer owns means it is complete *as our invoice* — those
     * minutes are billed to them by their carrier, and adding a line for them
     * here would be the same double charge `carriage.py` refuses on the bill.
     * The server answers which (`GET /agent-options/carriage`); falling back
     * to the first sentence keeps a caller that does not ask behaving as
     * before.
     */
    carriageNote?: string;
}) {
    const { user, loading: authLoading } = useAuth();
    const [estimate, setEstimate] = useState<Estimate | null>(null);
    const [error, setError] = useState<string | null>(null);

    // Serialised so the effect re-runs on a genuine stack change rather than on
    // every re-render that rebuilds the object.
    const key = JSON.stringify(stack);

    useEffect(() => {
        if (authLoading || !user) return;
        let cancelled = false;

        (async () => {
            const result = await getCostPerMinuteApiV1CostEstimatePerMinutePost({
                body: JSON.parse(key) as CostStack,
            });
            if (cancelled) return;
            // The generated client resolves rather than throws on a 4xx/5xx,
            // so the error has to be checked explicitly.
            if (result.error) {
                setError(
                    detailFromError(result.error, "Could not price this configuration"),
                );
                return;
            }
            setEstimate((result.data as unknown as Estimate) ?? null);
            setError(null);
        })();

        return () => {
            cancelled = true;
        };
    }, [key, authLoading, user]);

    // `estimate.lines` as well as `estimate`: the generated client resolves
    // rather than throws, so a 200 carrying an unexpected shape arrives here as
    // a truthy object with nothing in it. Indexing into that crashes the whole
    // Models page through the error boundary, which reads as "this page is
    // broken" rather than "one estimate did not load".
    if (error || !estimate || !Array.isArray(estimate.lines)) return null;

    const total = estimate.total_paise_per_minute;
    const values: Record<string, number> = {
        agent: estimate.agent_paise_per_minute,
        telephony: estimate.telephony_paise_per_minute,
        platform: estimate.platform_paise_per_minute,
        addon: estimate.addon_paise_per_minute ?? 0,
    };

    // Every line whose quantity we assumed rather than measured. Worth saying
    // out loud: it is the difference between a projection and a quote.
    const assumed = estimate.lines.filter((line) => line.basis === "default");

    // Telephony is an exact per-minute rate and routinely a third of what a
    // minute costs. A total with no telephony in it is not slightly low, it is
    // the wrong number — and the old behaviour was to hide the row and show the
    // total anyway, which reads as a complete quote. Say it instead.
    const missingTelephony =
        !stack.telephony_provider ||
        estimate.unpriced.some((entry) => entry.startsWith("telephony:"));

    return (
        <TooltipProvider>
            <div
                className={cn(
                    "rounded-xl border bg-card px-4 py-3 shadow-sm",
                    className,
                )}
            >
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="text-sm text-muted-foreground">Cost per min</span>
                    {/* "from" when some component has no rate on file. The number
                        is then a floor, not a total, and printing it plain reads
                        as a quote — someone picks the stack believing it costs
                        the platform fee alone and finds out on the invoice. */}
                    {estimate.unpriced.length > 0 && (
                        <span className="text-sm text-muted-foreground">from</span>
                    )}
                    <span className="text-xl font-semibold tabular-nums tracking-tight">
                        {rupees(total)}
                    </span>
                    {estimate.unpriced.length > 0 && (
                        <span className="rounded-full bg-amber-500/12 px-2 py-0.5 text-[11px] font-medium text-amber-600 dark:text-amber-400">
                            incomplete — {estimate.unpriced.length} unpriced
                        </span>
                    )}
                    {estimate.total_micros_usd_per_minute !== null && (
                        <span className="text-sm tabular-nums text-muted-foreground">
                            {formatMicrosUsd(estimate.total_micros_usd_per_minute)}
                        </span>
                    )}

                    {/* The differentiator, stated where the price is. A minute is
                        the unit everyone quotes; it is not the unit we charge. */}
                    <span className="rounded-full bg-[color:var(--brand-amber)]/12 px-2 py-0.5 text-[11px] font-medium text-[color:var(--brand-amber)]">
                        billed in {estimate.pulse_seconds}s pulses
                    </span>

                    <Tooltip>
                        <TooltipTrigger asChild>
                            <button
                                type="button"
                                aria-label="How this estimate is calculated"
                                className="text-muted-foreground hover:text-foreground"
                            >
                                <Info className="h-4 w-4" />
                            </button>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs">
                            <p className="text-xs">
                                Priced from the same rates your invoice uses. Transcription
                                and telephony are exact per-minute rates; LLM and voice
                                depend on how much a call actually says, so those use{" "}
                                {assumed.length > 0
                                    ? "a typical-call assumption until you have enough calls on this model to measure it"
                                    : "the median of your own calls on this model"}
                                .
                            </p>
                        </TooltipContent>
                    </Tooltip>

                    {assumed.length > 0 && (
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                            estimated
                        </span>
                    )}
                </div>

                {/* Proportional split. Rendered even at zero width so the
                    legend below never describes a segment that isn't there. */}
                <div className="mt-2.5 flex h-2 w-full overflow-hidden rounded-full bg-muted">
                    {GROUPS.map((group) => {
                        const value = values[group.key] ?? 0;
                        if (total <= 0 || value <= 0) return null;
                        return (
                            <div
                                key={group.key}
                                style={{
                                    width: `${(value / total) * 100}%`,
                                    backgroundColor: group.colour,
                                }}
                                title={`${group.label} ${rupees(value)}/min`}
                            />
                        );
                    })}
                </div>

                {/* Only groups that contribute. A "Telephony ₹0.00" row on a
                    page where no telephony provider is chosen reads as a claim
                    that calls are free, rather than that none is selected —
                    which is what `missingTelephony` says in words below. */}
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                    {GROUPS.filter((group) => (values[group.key] ?? 0) > 0).map(
                        (group) => (
                            <span
                                key={group.key}
                                className="flex items-center gap-1.5 text-xs text-muted-foreground"
                            >
                                <span
                                    aria-hidden
                                    className="inline-block h-2 w-2 shrink-0 rounded-full"
                                    style={{ backgroundColor: group.colour }}
                                />
                                {group.label}
                                <span className="tabular-nums text-foreground">
                                    {rupees(values[group.key] ?? 0)}
                                </span>
                            </span>
                        ),
                    )}
                </div>

                {missingTelephony && (
                    <p className="mt-2 flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                        <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                        <span>
                            {carriageNote ||
                                "Excludes telephony — no carrier is configured for this account yet. Carriage is charged per minute on top of this, and is often a third of the cost of a call."}
                        </span>
                    </p>
                )}

                {/* The itemisation. A headline number nobody can decompose is a
                    number nobody can argue with, which is the wrong property
                    for a bill. */}
                <dl className="mt-3 space-y-1 border-t pt-2">
                    {estimate.lines.map((line) => (
                        <div
                            key={`${line.component}-${line.provider ?? ""}`}
                            className="flex items-baseline justify-between gap-4 text-xs"
                        >
                            <dt className="text-muted-foreground">
                                {COMPONENT_LABELS[line.component] ?? line.component}
                                {line.provider && (
                                    <span className="ml-1.5 text-muted-foreground/70">
                                        {/* An add-on's `provider` is a catalogue
                                            key, not a vendor: rendering it raw
                                            put "knowledge_base" on a price
                                            breakdown a customer reads. */}
                                        {line.component === "addon"
                                            ? (ADDON_LABELS[line.provider] ??
                                              line.provider)
                                            : line.provider}
                                        {line.model ? ` · ${line.model}` : ""}
                                    </span>
                                )}
                                {line.rate_is_provider_fallback && (
                                    <span
                                        className="ml-1.5 text-amber-600 dark:text-amber-400"
                                        title="No rate on file for this exact model — priced at the provider's rate"
                                    >
                                        ≈
                                    </span>
                                )}
                            </dt>
                            <dd className="shrink-0 tabular-nums">
                                {rupees(line.paise_per_minute)}
                            </dd>
                        </div>
                    ))}
                </dl>

                {estimate.unpriced.length > 0 && (
                    <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
                        No rate on file for {estimate.unpriced.join(", ")} — the real
                        cost will be higher than shown.
                    </p>
                )}
            </div>
        </TooltipProvider>
    );
}
