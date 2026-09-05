/**
 * What this agent runs on, at the top of its own screen.
 *
 * Vapi and Bolna both open the agent editor with the stack on show — vendor,
 * model, cost a minute — and ours opened with a name field and buried the
 * models six cards down. This is that row.
 *
 * The latency column is the part neither of them has. Theirs is the vendor's
 * published figure, identical for every customer. Ours is the median of *this
 * agent's own turns* from `call_turn_metrics`, measured over Indian telephony,
 * which is why it is labelled "measured" and dated by sample size. An agent
 * without enough turns says so rather than borrowing a datasheet number.
 *
 * The cost bar is the whole minute — agent, telephony, platform — not the sum
 * of the three cards, because telephony and the platform fee are in a
 * connected minute and in none of the slots. A total added up from the cards
 * would be quietly low, and low is the one direction a price shown to a
 * customer must never be wrong in.
 */

"use client";

import { Bot, Ear, Radio, Volume2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { client } from "@/client/client.gen";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatPaise } from "@/lib/billing/format";
import { cn } from "@/lib/utils";

type Slot = {
    component: "stt" | "llm" | "tts" | "realtime";
    title: string;
    provider: string;
    model: string;
    paise_per_minute: number | null;
    approximate: boolean;
    latency_ms: number | null;
};

type Latency = {
    turns: number;
    window_days: number;
    transcribe_ms: number | null;
    think_ms: number | null;
    speak_ms: number | null;
    total_ms: number | null;
};

type Cost = {
    total_paise_per_minute: number;
    agent_paise_per_minute: number;
    telephony_paise_per_minute: number;
    platform_paise_per_minute: number;
    unpriced: string[];
    /** False when this account's numbers are not ours, so no carriage is priced. */
    includes_telephony?: boolean;
};

type ModelRowData = {
    is_realtime: boolean;
    slots: Slot[];
    cost: Cost;
    latency: Latency | null;
};

const ICONS = {
    stt: Ear,
    llm: Bot,
    tts: Volume2,
    realtime: Radio,
} as const;

/** Vendor ids are lowercase machine strings; nobody wants to read `openai_realtime`. */
const VENDOR_NAMES: Record<string, string> = {
    openai: "OpenAI",
    openai_realtime: "OpenAI",
    google: "Google",
    google_realtime: "Google",
    google_vertex: "Google Vertex",
    sarvam: "Sarvam",
    deepgram: "Deepgram",
    elevenlabs: "ElevenLabs",
    anthropic: "Anthropic",
    cartesia: "Cartesia",
    azure: "Azure",
    groq: "Groq",
};

function vendorName(provider: string): string {
    return VENDOR_NAMES[provider] ?? provider;
}

function ms(value: number | null): string {
    return value === null ? "—" : `${Math.round(value)}ms`;
}

export function ModelRow({ workflowId }: { workflowId: number }) {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);

    const [data, setData] = useState<ModelRowData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        // Not in the generated SDK yet; the route is newer than the last
        // client generation.
        //
        // Wrapped because a throw here would leave `loading` true forever: the
        // row would sit as a skeleton above a form that works, which reads as a
        // broken page rather than as a summary that could not be drawn.
        try {
            const result = await client.get({
                url: `/api/v1/workflow/${workflowId}/model-row`,
            });
            if (result.error) {
                setError(
                    detailFromResult(result, "Could not load what this agent runs on"),
                );
                return;
            }
            setData((result.data as ModelRowData | undefined) ?? null);
        } catch {
            setError("Could not load what this agent runs on");
        } finally {
            setLoading(false);
        }
    }, [workflowId]);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        load();
    }, [authLoading, user, load]);

    if (loading) {
        return (
            <div className="space-y-3">
                <Skeleton className="h-10 w-full max-w-md" />
                <div className="grid gap-3 sm:grid-cols-3">
                    <Skeleton className="h-28 w-full" />
                    <Skeleton className="h-28 w-full" />
                    <Skeleton className="h-28 w-full" />
                </div>
            </div>
        );
    }

    // A row that cannot be drawn is left out rather than replaced with an error
    // block: the settings below it are still editable, and this is a summary of
    // them, not a prerequisite.
    if (error || !data || data.slots.length === 0) return null;

    const { cost, latency, slots } = data;
    const segments = [
        { label: "Agent", paise: cost.agent_paise_per_minute, className: "bg-emerald-600" },
        { label: "Telephony", paise: cost.telephony_paise_per_minute, className: "bg-orange-500" },
        { label: "Platform", paise: cost.platform_paise_per_minute, className: "bg-blue-600" },
    ].filter((s) => s.paise > 0);
    const barTotal = segments.reduce((sum, s) => sum + s.paise, 0);

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-start gap-x-10 gap-y-4">
                <div className="min-w-0">
                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Cost
                    </p>
                    <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">
                        {formatPaise(cost.total_paise_per_minute)}
                        <span className="ml-1 text-sm font-normal text-muted-foreground">
                            /min
                        </span>
                    </p>
                    {barTotal > 0 && (
                        <>
                            <div className="mt-2 flex h-1.5 w-full min-w-[180px] max-w-xs gap-0.5 overflow-hidden rounded-full">
                                {segments.map((s) => (
                                    <div
                                        key={s.label}
                                        className={s.className}
                                        style={{ flex: s.paise }}
                                        title={`${s.label} ${formatPaise(s.paise)}/min`}
                                    />
                                ))}
                            </div>
                            <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                                {segments.map((s) => (
                                    <span key={s.label} className="inline-flex items-center gap-1">
                                        <span
                                            className={cn("h-2 w-2 rounded-full", s.className)}
                                        />
                                        {s.label}
                                    </span>
                                ))}
                            </div>
                        </>
                    )}
                </div>

                <div className="min-w-0">
                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Reply time
                    </p>
                    <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">
                        {latency?.total_ms ? ms(latency.total_ms) : "—"}
                    </p>
                    <p className="mt-2 max-w-xs text-[11px] text-muted-foreground">
                        {latency
                            ? `Median across ${latency.turns.toLocaleString("en-IN")} turns on real calls, last ${latency.window_days} days. Excludes the opening turn.`
                            : "Not enough calls yet to measure. This fills in once the agent has run."}
                    </p>
                </div>
            </div>

            <div
                className={cn(
                    "grid gap-3",
                    // One card on the speech-to-speech path, where a single
                    // model does the whole turn.
                    slots.length === 1
                        ? "sm:grid-cols-1"
                        : slots.length === 2
                          ? "sm:grid-cols-2"
                          : "sm:grid-cols-3",
                )}
            >
                {slots.map((slot) => {
                    const Icon = ICONS[slot.component];
                    return (
                        <Card key={slot.component} className="overflow-hidden">
                            <CardContent className="p-4">
                                <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                                    <Icon className="h-3.5 w-3.5" />
                                    {slot.title}
                                </p>
                                {/* Wraps rather than truncates: the model id is
                                    what the card is *for*, and vendor ids here
                                    are long enough that clipping loses the part
                                    that distinguishes two of them
                                    (sarvam-105b vs sarvam-105b-conversations). */}
                                <p
                                    className="mt-2 break-words text-[15px] font-medium leading-tight"
                                    title={slot.model}
                                >
                                    {slot.model || "—"}
                                </p>
                                <p className="text-sm text-muted-foreground">
                                    {vendorName(slot.provider)}
                                </p>
                                <div className="mt-3 flex gap-6 border-t pt-3">
                                    <div>
                                        <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
                                            Cost
                                        </p>
                                        <p className="text-sm tabular-nums">
                                            {slot.paise_per_minute === null
                                                ? "—"
                                                : `${formatPaise(slot.paise_per_minute)}/min`}
                                            {slot.approximate && (
                                                <span
                                                    className="ml-1 text-muted-foreground"
                                                    title="Priced against this provider's default rather than this model"
                                                >
                                                    ≈
                                                </span>
                                            )}
                                        </p>
                                    </div>
                                    <div>
                                        <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
                                            Measured
                                        </p>
                                        <p className="text-sm tabular-nums">
                                            {ms(slot.latency_ms)}
                                        </p>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    );
                })}
            </div>

            {cost.includes_telephony === false && (
                <p className="text-xs text-muted-foreground">
                    Call minutes are not included above — this account&apos;s numbers
                    are billed by your own carrier.
                </p>
            )}

            {cost.unpriced.length > 0 && (
                <p className="text-xs text-muted-foreground">
                    No rate on file for {cost.unpriced.join(", ")}, so the total above is
                    lower than the invoice will be.
                </p>
            )}
        </div>
    );
}
