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
 *
 * The choice is **saved explicitly**. It used to notify a parent through
 * `onChange` and nothing else, so on the Models screen — where there is no
 * parent listening — picking a bundle did nothing at all: the screen looked
 * like a settings page, behaved like a preview, and the account went on
 * dialling whatever it dialled before. The summary column now shows what is
 * stored, what is selected, and the difference between them.
 */

"use client";

import { Check, Loader2, ShieldCheck } from "lucide-react";
import { useCallback,useEffect, useMemo, useRef,useState } from "react";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

import { type VoiceOption,VoicePicker } from "./VoicePicker";

/** One component's share of a minute, with the vendor deliberately absent. */
type BreakdownLine = {
    component: string;
    label: string;
    paise_per_minute: number;
};

/**
 * What makes up a price, as the server itemised it.
 *
 * The same figures the Advanced tab's bar shows and none of the provider or
 * model names, because this is the screen whose entire purpose is not naming
 * them. Comes from the estimate the headline price came from, so a segment can
 * never disagree with the number above it.
 */
type Breakdown = {
    agent_paise_per_minute: number;
    telephony_paise_per_minute: number;
    platform_paise_per_minute: number;
    addon_paise_per_minute: number;
    pulse_seconds: number;
    lines: BreakdownLine[];
};

type Variant = {
    tier: string;
    label: string;
    blurb: string;
    /**
     * Null when some component of the stack has no rate on file. Never a
     * number we could not stand behind: a speech-to-speech card once showed
     * ₹2.76 a minute for a model billing ₹25.79, because the model resolved to
     * no rate and the server served what was left of the sum.
     */
    paise_per_minute: number | null;
    breakdown: Breakdown | null;
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

/** What the prices below do and do not contain, as the server works it out. */
type Carriage = {
    provider: string | null;
    reason: string;
    explanation: string;
    included: boolean;
};

/** The choice currently stored against the account, or null if there is none. */
type Selection = {
    bundle: string;
    tier: string;
    voice: string;
};

type Options = {
    voices: VoiceOption[];
    bundles: Bundle[];
    selected?: Selection | null;
    telephony?: Carriage;
    /** What this account can spend today. Turns a price into a decision. */
    balance_paise?: number;
};

function rupees(paise: number): string {
    return `₹${(paise / 100).toFixed(2)}`;
}

/** What a card shows where a price would go when there is no price. */
const NO_PRICE = "Price unavailable";

/**
 * The groups the summary bar is split into: fixed order, fixed colour, so the
 * segment for a group never moves or changes hue as the choice changes and the
 * bar can be read at a glance across bundles. Deliberately the same four
 * colours `CostPerMinuteBar` uses on the Advanced tab — one account, one price,
 * and it should look like one price on both screens.
 */
const GROUPS = [
    { key: "agent", label: "Agent", colour: "#1baf7a" },
    { key: "telephony", label: "Telephony", colour: "#eb6834" },
    { key: "platform", label: "Platform", colour: "#2a78d6" },
    { key: "addon", label: "Features", colour: "#8b5cf6" },
] as const;

/**
 * Roughly how long a balance lasts on this price.
 *
 * A price a minute is not a decision anybody can make. ₹25.79 reads as "a bit
 * more than ₹8.30" right up until it is stated as 97 minutes against 301 —
 * which is the difference between a premium option and a month that ends in
 * four days, and it is the question a first-time buyer is actually asking.
 *
 * Null wherever the price is null: quoting minutes at a price missing its
 * largest line multiplies the error instead of surfacing it. Mirrors
 * `agent_options.approximate_minutes` on the server, floor and all.
 */
function approximateMinutes(
    balancePaise: number | undefined,
    paisePerMinute: number | null,
): number | null {
    if (!balancePaise || balancePaise <= 0) return null;
    if (paisePerMinute === null || paisePerMinute <= 0) return null;
    return Math.floor(balancePaise / paisePerMinute);
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

/**
 * The price, in the weight it deserves.
 *
 * A per-minute figure set in body text beside three other paragraphs is the
 * one number on this screen anybody is actually comparing, and it read like a
 * footnote. Large, tabular, with the composition underneath it.
 */
function PriceSummary({
    paisePerMinute,
    breakdown,
    balancePaise,
}: {
    paisePerMinute: number | null;
    breakdown: Breakdown | null;
    balancePaise: number | undefined;
}) {
    const minutes = approximateMinutes(balancePaise, paisePerMinute);
    const total = paisePerMinute ?? 0;
    const values: Record<string, number> = breakdown
        ? {
              agent: breakdown.agent_paise_per_minute,
              telephony: breakdown.telephony_paise_per_minute,
              platform: breakdown.platform_paise_per_minute,
              addon: breakdown.addon_paise_per_minute,
          }
        : {};

    return (
        <div>
            <div className="flex items-baseline gap-1.5">
                <span className="text-3xl font-semibold tabular-nums tracking-tight">
                    {paisePerMinute === null ? NO_PRICE : rupees(paisePerMinute)}
                </span>
                {paisePerMinute !== null && (
                    <span className="text-sm text-muted-foreground">a minute</span>
                )}
            </div>

            {minutes !== null && (
                <p className="mt-0.5 text-xs text-muted-foreground">
                    about{" "}
                    <span className="font-medium text-foreground">
                        {minutes.toLocaleString("en-IN")} minutes
                    </span>{" "}
                    on your balance
                </p>
            )}

            {breakdown && total > 0 && (
                <>
                    {/* Proportional split. The same four groups, in the same
                        order, whatever the bundle — so two cards can be
                        compared by the shape of the bar and not only by the
                        number above it. */}
                    <div className="mt-3 flex h-2.5 w-full overflow-hidden rounded-full bg-muted">
                        {GROUPS.map((group) => {
                            const value = values[group.key] ?? 0;
                            if (value <= 0) return null;
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

                    <dl className="mt-3 space-y-1">
                        {breakdown.lines.map((line) => (
                            <div
                                key={line.component}
                                className="flex items-baseline justify-between gap-3 text-xs"
                            >
                                <dt className="text-muted-foreground">{line.label}</dt>
                                <dd className="shrink-0 tabular-nums">
                                    {rupees(line.paise_per_minute)}
                                </dd>
                            </div>
                        ))}
                    </dl>

                    {/* The differentiator, stated where the price is. A minute
                        is the unit everyone quotes; it is not the unit we
                        charge. */}
                    <span className="mt-3 inline-block rounded-full bg-[color:var(--brand-amber)]/12 px-2 py-0.5 text-[11px] font-medium text-[color:var(--brand-amber)]">
                        billed in {breakdown.pulse_seconds}s pulses
                    </span>
                </>
            )}
        </div>
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
        /**
         * Null when the stack cannot be priced. Passed on rather than
         * substituted with a zero or a partial total: a caller storing this
         * against a quote has to decide what to do about a missing price, and
         * a number that looks fine takes that decision away from them.
         */
        paisePerMinute: number | null;
    }) => void;
}) {
    const [options, setOptions] = useState<Options | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [bundleSlug, setBundleSlug] = useState<string>("");
    const [tier, setTier] = useState<string>("");
    const [voice, setVoice] = useState<string>("");
    /**
     * What is stored, as distinct from what is selected. Keeping both is what
     * lets the Save button say whether pressing it would change anything —
     * a button that is always live cannot tell you that you have unsaved work.
     */
    const [saved, setSaved] = useState<Selection | null>(null);
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);

    // The auth interceptor that attaches the bearer token is registered only
    // once auth has finished loading. Fetching before that sends the request
    // unauthenticated, and it fails silently — the screen sits on "Loading
    // options…" with nothing in the console to say why.
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
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

            // Open on what the account is actually on. A saved bundle that has
            // since been withdrawn falls through to the first card rather than
            // selecting nothing, so the screen is never blank — but it is not
            // reported as saved, because it is not what would be saved.
            const stored = data.selected ?? null;
            const storedBundle = stored
                ? data.bundles.find((b) => b.slug === stored.bundle)
                : undefined;
            const opening = storedBundle ?? data.bundles[0];
            const openingTier =
                storedBundle &&
                stored &&
                opening.variants.some((v) => v.tier === stored.tier)
                    ? stored.tier
                    : (opening.variants[0]?.tier ?? "");

            setSaved(storedBundle ? stored : null);
            setBundleSlug((current) => current || opening.slug);
            setTier((current) => current || openingTier);
            setVoice(
                (current) =>
                    current ||
                    stored?.voice ||
                    (data.voices[0]?.voice_id ?? ""),
            );
        })();
        return () => {
            cancelled = true;
        };
    }, [authLoading, user]);

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

    const save = useCallback(async () => {
        if (!bundle || !variant) return;
        setSaving(true);
        setSaveError(null);
        const body = {
            bundle: bundle.slug,
            tier: variant.tier,
            voice: bundle.picks_voice ? voice : "",
        };
        const result = await client.put({
            url: "/api/v1/agent-options/selection",
            body,
        });
        setSaving(false);
        if (result.error) {
            setSaveError(detailFromResult(result, "Could not save this choice"));
            return;
        }
        // The server's answer, not the request: it resolves the voice a
        // realtime bundle actually speaks in, and echoing back what we sent
        // would show a saved state that is not what was stored.
        setSaved((result.data as Selection) ?? body);
    }, [bundle, variant, voice]);

    /**
     * Whether pressing Save would change anything. The voice is only part of
     * the answer on a bundle that has one — comparing it on a speech-to-speech
     * bundle would report unsaved changes for a field the screen does not show.
     */
    const dirty =
        !saved ||
        saved.bundle !== bundle?.slug ||
        saved.tier !== variant?.tier ||
        (bundle?.picks_voice === true && saved.voice !== voice);

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

    const savedBundle = saved
        ? options.bundles.find((b) => b.slug === saved.bundle)
        : undefined;
    const savedVariant = savedBundle?.variants.find((v) => v.tier === saved?.tier);
    const savedVoice = options.voices.find((v) => v.voice_id === saved?.voice);
    const selectedVoice = options.voices.find((v) => v.voice_id === voice);

    return (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem] lg:items-start">
            <div className="space-y-6">
                {/* One card per bundle. The cheapest variant is shown on the card so
                    the three can be compared before any of them is opened — a price
                    that only appears after selection makes the reader click all
                    three to find out. */}
                <div className="grid gap-3 sm:grid-cols-3">
                    {options.bundles.map((option) => {
                        // Only priced variants can be "cheapest". A null compares
                        // as less than every number in JS once coerced, so the old
                        // reduce would have picked the unpriced one and shown its
                        // absent price as the headline.
                        const priced = option.variants.filter(
                            (v) => v.paise_per_minute !== null,
                        );
                        const cheapest =
                            priced.length > 0
                                ? priced.reduce((low, v) =>
                                      (v.paise_per_minute as number) <
                                      (low.paise_per_minute as number)
                                          ? v
                                          : low,
                                  )
                                : option.variants[0];
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
                                    {cheapest.paise_per_minute === null ? (
                                        <span className="text-muted-foreground">
                                            {NO_PRICE}
                                        </span>
                                    ) : (
                                        <>
                                            <strong>
                                                {priced.length > 1 ? "from " : ""}
                                                {rupees(cheapest.paise_per_minute)}
                                            </strong>
                                            <span className="text-muted-foreground">
                                                {" "}
                                                a minute
                                            </span>
                                        </>
                                    )}
                                </span>
                                {(() => {
                                    const minutes = approximateMinutes(
                                        options.balance_paise,
                                        cheapest.paise_per_minute,
                                    );
                                    if (minutes === null) return null;
                                    return (
                                        <span className="text-xs text-muted-foreground">
                                            about {minutes.toLocaleString("en-IN")} min on
                                            your balance
                                        </span>
                                    );
                                })()}
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
                                        {v.paise_per_minute === null ? (
                                            <span className="text-muted-foreground">
                                                {NO_PRICE}
                                            </span>
                                        ) : (
                                            <>
                                                {rupees(v.paise_per_minute)}
                                                <span className="text-muted-foreground">
                                                    /min
                                                </span>
                                                {(() => {
                                                    const minutes = approximateMinutes(
                                                        options.balance_paise,
                                                        v.paise_per_minute,
                                                    );
                                                    if (minutes === null) return null;
                                                    return (
                                                        <span className="block text-xs text-muted-foreground">
                                                            ~{minutes.toLocaleString("en-IN")} min
                                                        </span>
                                                    );
                                                })()}
                                            </>
                                        )}
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
            </div>

            {/* Everything chosen, in one column, with the button that commits it.
                Sticky because the choice is made by scrolling through cards and
                variants, and a summary you have to scroll back to is a summary
                you check once. */}
            <aside className="lg:sticky lg:top-6">
                <div className="space-y-4 rounded-xl border bg-card p-4 shadow-sm">
                    <div className="flex items-center justify-between gap-2">
                        <h3 className="text-sm font-semibold">Current selection</h3>
                        {!dirty && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
                                <Check className="h-3 w-3" />
                                Saved
                            </span>
                        )}
                    </div>

                    <dl className="space-y-2 text-sm">
                        <div className="flex items-baseline justify-between gap-3">
                            <dt className="text-muted-foreground">Bundle</dt>
                            <dd className="font-medium">{bundle.label}</dd>
                        </div>
                        {bundle.variants.length > 1 && variant && (
                            <div className="flex items-baseline justify-between gap-3">
                                <dt className="text-muted-foreground">Brain</dt>
                                <dd className="font-medium">{variant.label}</dd>
                            </div>
                        )}
                        {bundle.picks_voice && (
                            <div className="flex items-baseline justify-between gap-3">
                                <dt className="text-muted-foreground">Voice</dt>
                                <dd className="font-medium">
                                    {selectedVoice?.name ?? "—"}
                                </dd>
                            </div>
                        )}
                    </dl>

                    {variant?.india_only && <IndiaBadge />}

                    <div className="border-t pt-4">
                        <PriceSummary
                            paisePerMinute={variant?.paise_per_minute ?? null}
                            breakdown={variant?.breakdown ?? null}
                            balancePaise={options.balance_paise}
                        />
                    </div>

                    {variant?.paise_per_minute === null && (
                        <p className="text-xs text-muted-foreground">
                            We hold no rate for one of the models behind this choice, so we
                            will not quote a number for it. You can still build the agent —
                            ask us before you run a campaign on it.
                        </p>
                    )}

                    <p className="text-xs text-muted-foreground">
                        An estimate. A call that says more costs more, and the figure moves
                        if provider prices do.
                        {/* Whether the carrier's minutes are in that number.
                            They were not, and the omission was never stated —
                            roughly a tenth of the price of a call, missing, on
                            the one screen a first-time buyer reads before
                            paying. */}
                        {options.telephony?.explanation
                            ? ` ${options.telephony.explanation}`
                            : null}
                    </p>

                    {saveError && (
                        <p className="text-xs text-destructive">{saveError}</p>
                    )}

                    <Button
                        type="button"
                        className="w-full"
                        disabled={saving || !dirty}
                        onClick={() => void save()}
                    >
                        {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                        {dirty ? "Save as my default" : "Saved"}
                    </Button>

                    {/* What is in force, when it is not what is on screen. A
                        Save button alone tells you there is something unsaved;
                        it does not tell you what your agents are running on in
                        the meantime, which is the thing you came to check. */}
                    {dirty && savedBundle && (
                        <p className="text-xs text-muted-foreground">
                            Your agents currently run on{" "}
                            <span className="font-medium text-foreground">
                                {savedBundle.label}
                                {savedVariant && savedBundle.variants.length > 1
                                    ? ` · ${savedVariant.label}`
                                    : ""}
                                {savedBundle.picks_voice && savedVoice
                                    ? ` · ${savedVoice.name}`
                                    : ""}
                            </span>
                            .
                        </p>
                    )}
                    {dirty && !saved && (
                        <p className="text-xs text-muted-foreground">
                            Nothing is saved yet, so your agents run on the account
                            default.
                        </p>
                    )}
                </div>
            </aside>
        </div>
    );
}
