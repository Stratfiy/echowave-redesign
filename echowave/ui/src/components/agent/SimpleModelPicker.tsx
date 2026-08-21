/**
 * The model choice for someone who does not know what a model is.
 *
 * A clinic owner, a dealership, a coaching centre. They are choosing how their
 * agent should sound and think, and the only number they should have to read
 * is what a minute costs.
 *
 * Every bundle carries variants even when there is one, so this renders a
 * single shape rather than branching on architecture — and a fourth bundle
 * added from the superadmin screen appears here with no change.
 */

"use client";

import { Check, Loader2, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { client } from "@/client/client.gen";
import { cn } from "@/lib/utils";

import { type VoiceOption,VoicePicker } from "./VoicePicker";

type Variant = {
    tier: string;
    label: string;
    blurb: string;
    paise_per_minute: number;
    india_only: boolean;
};

type Bundle = {
    slug: string;
    label: string;
    blurb: string;
    architecture: string;
    picks_voice: boolean;
    variants: Variant[];
};

type Options = { voices: VoiceOption[]; bundles: Bundle[] };

function rupees(paise: number): string {
    return `₹${(paise / 100).toFixed(2)}`;
}

/**
 * Shown only where it is true, and computed on the server from the tiers the
 * bundle resolves to right now. Never a stored flag: move a tier abroad and
 * this disappears on the next request rather than lying until somebody looks.
 */
function IndiaBadge() {
    return (
        <span
            className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[0.7rem] font-medium text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
            title="Speech and language for this choice are processed in India."
        >
            <ShieldCheck className="h-3 w-3" />
            Stays in India
        </span>
    );
}

export function SimpleModelPicker({
    onChange,
}: {
    /** Fires whenever the choice changes, so a parent can save it. */
    onChange?: (choice: {
        bundle: string;
        tier: string;
        voice: string;
        paisePerMinute: number;
    }) => void;
}) {
    const [options, setOptions] = useState<Options | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [bundleSlug, setBundleSlug] = useState<string>("");
    const [tier, setTier] = useState<string>("");
    const [voice, setVoice] = useState<string>("");

    useEffect(() => {
        let cancelled = false;
        void (async () => {
            // Not in the generated SDK yet; the route is newer than the last
            // client generation.
            const response = await client.get({ url: "/api/v1/agent-options" });
            if (cancelled) return;
            if (response.error) {
                setError("Could not load the options. Refresh to try again.");
                return;
            }
            const data = response.data as Options | undefined;
            if (!data?.bundles?.length) {
                setError("No bundles are available on this account.");
                return;
            }
            setOptions(data);
            const first = data.bundles[0];
            setBundleSlug((current) => current || first.slug);
            setTier((current) => current || (first.variants[0]?.tier ?? ""));
            setVoice((current) => current || (data.voices[0]?.voice_id ?? ""));
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    const bundle = useMemo(
        () => options?.bundles.find((b) => b.slug === bundleSlug) ?? null,
        [options, bundleSlug],
    );
    const variant = useMemo(
        () =>
            bundle?.variants.find((v) => v.tier === tier) ?? bundle?.variants[0] ?? null,
        [bundle, tier],
    );

    useEffect(() => {
        if (!bundle || !variant) return;
        onChange?.({
            bundle: bundle.slug,
            tier: variant.tier,
            voice: bundle.picks_voice ? voice : "",
            paisePerMinute: variant.paise_per_minute,
        });
        // `onChange` is a prop that callers commonly define inline, so
        // including it here would fire this on every parent render.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [bundle, variant, voice]);

    if (error) {
        return <p className="text-sm text-destructive">{error}</p>;
    }
    if (!options || !bundle) {
        return (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading options…
            </div>
        );
    }

    return (
        <div className="space-y-6">
            {/* One card per bundle. The cheapest variant is shown on the card so
                the three can be compared before any of them is opened — a price
                that only appears after selection makes the reader click all
                three to find out. */}
            <div className="grid gap-3 sm:grid-cols-3">
                {options.bundles.map((option) => {
                    const cheapest = option.variants.reduce(
                        (low, v) => (v.paise_per_minute < low.paise_per_minute ? v : low),
                        option.variants[0],
                    );
                    const isSelected = option.slug === bundle.slug;
                    return (
                        <button
                            key={option.slug}
                            type="button"
                            aria-pressed={isSelected}
                            onClick={() => {
                                setBundleSlug(option.slug);
                                setTier(option.variants[0]?.tier ?? "");
                            }}
                            className={cn(
                                "flex flex-col gap-1.5 rounded-lg border p-4 text-left transition-colors",
                                isSelected
                                    ? "border-primary ring-1 ring-primary"
                                    : "border-border hover:border-foreground/30",
                            )}
                        >
                            <span className="flex items-center gap-2 font-medium">
                                {option.label}
                                {isSelected && <Check className="h-4 w-4" />}
                            </span>
                            <span className="text-sm text-muted-foreground">
                                {option.blurb}
                            </span>
                            <span className="mt-1 text-sm">
                                <strong>
                                    {option.variants.length > 1 ? "from " : ""}
                                    {rupees(cheapest.paise_per_minute)}
                                </strong>
                                <span className="text-muted-foreground"> a minute</span>
                            </span>
                            {cheapest.india_only && <IndiaBadge />}
                        </button>
                    );
                })}
            </div>

            {/* Only where there is a choice to make. A single-variant bundle
                showing a one-option radio group is furniture. */}
            {bundle.variants.length > 1 && (
                <section className="space-y-2">
                    <h3 className="text-sm font-medium">How clever should it be?</h3>
                    <div className="flex flex-col gap-2">
                        {bundle.variants.map((v) => (
                            <button
                                key={v.tier}
                                type="button"
                                aria-pressed={v.tier === variant?.tier}
                                onClick={() => setTier(v.tier)}
                                className={cn(
                                    "flex items-center justify-between gap-4 rounded-lg border px-4 py-3 text-left transition-colors",
                                    v.tier === variant?.tier
                                        ? "border-primary ring-1 ring-primary"
                                        : "border-border hover:border-foreground/30",
                                )}
                            >
                                <span className="min-w-0">
                                    <span className="flex flex-wrap items-center gap-2 font-medium">
                                        {v.label}
                                        {v.india_only && <IndiaBadge />}
                                    </span>
                                    <span className="block text-sm text-muted-foreground">
                                        {v.blurb}
                                    </span>
                                </span>
                                <span className="shrink-0 tabular-nums text-sm">
                                    {rupees(v.paise_per_minute)}
                                    <span className="text-muted-foreground">/min</span>
                                </span>
                            </button>
                        ))}
                    </div>
                </section>
            )}

            {bundle.picks_voice ? (
                <section className="space-y-2">
                    <h3 className="text-sm font-medium">What should it sound like?</h3>
                    <VoicePicker
                        voices={options.voices}
                        selected={voice}
                        onSelect={(id) => setVoice(id)}
                    />
                </section>
            ) : (
                <p className="text-sm text-muted-foreground">
                    This one speaks for itself — the model that hears the caller is the
                    same one that answers, so there is no separate voice to choose.
                </p>
            )}

            {variant && (
                <p className="rounded-lg border border-border bg-muted/20 px-4 py-3 text-sm">
                    <strong>{rupees(variant.paise_per_minute)} a minute</strong>
                    <span className="mt-1 block text-xs text-muted-foreground">
                        An estimate. A call that says more costs more, and the figure
                        moves if provider prices do.
                    </span>
                </p>
            )}
        </div>
    );
}
