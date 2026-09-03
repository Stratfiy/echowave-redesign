"use client";

/**
 * Provider costs, grouped the way an operator reads them.
 *
 * The flat table this replaces answered "what is row forty-one", which is not
 * a question anybody has. The question is **"is this vendor set up, and what
 * does it cost us"** — and answering it from a flat list meant scanning for
 * every row that happened to share a provider name, while the rows that do not
 * exist (models we have never priced) stayed invisible by construction.
 *
 * Three things this shape can show that the list could not:
 *
 * 1. **Models we have never priced.** Listed greyed out, because an unpriced
 *    model is billable usage that will be reported as uncosted.
 * 2. **Where a price comes from.** A model on the provider-wide rate says
 *    "from provider rate"; a model with its own row says so too. This is the
 *    whole reason an operator edits a provider rate and watches nothing
 *    change — the resolver takes the most specific row, and until now nothing
 *    said which rows those were.
 * 3. **Whether a platform key exists.** A priced model with no key is a
 *    managed call that fails at dial time, and the price screen is where that
 *    gets noticed, not the incident.
 *
 * Editing keeps both shapes vendors actually quote in: one flat rate for
 * everything they serve, or a rate per model, or both at once — the flat rate
 * as the floor and named models overriding it.
 */

import {
    AlertTriangle,
    Check,
    ChevronRight,
    KeyRound,
    Loader2,
    Search,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
    listProvidersApiV1AdminBillingProvidersGet,
    setProviderRatesApiV1AdminBillingProvidersRatesPut,
} from "@/client/sdk.gen";
import { useAuthReady } from "@/components/charts/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromResult } from "@/lib/apiError";
import { cn } from "@/lib/utils";

type ModelEntry = {
    model: string;
    rate_mpaise: number | null;
    rate_micros_usd: number | null;
    unit: string | null;
    from_provider_rate: boolean;
};

type ComponentEntry = {
    component: string;
    flat_rate_mpaise: number | null;
    flat_rate_micros_usd: number | null;
    flat_unit: string | null;
    has_platform_key: boolean;
    models: ModelEntry[];
};

type ProviderEntry = {
    provider: string;
    is_priced: boolean;
    components: ComponentEntry[];
};

const COMPONENT_LABEL: Record<string, string> = {
    stt: "Speech to text",
    llm: "Language model",
    tts: "Text to speech",
    embeddings: "Embeddings",
    telephony: "Telephony",
};

const UNITS = [
    { value: "minute", label: "per minute" },
    { value: "1k_tokens", label: "per 1k tokens" },
    { value: "1k_chars", label: "per 1k characters" },
];

/** What a component is normally metered in, so the form pre-selects sensibly. */
const DEFAULT_UNIT: Record<string, string> = {
    stt: "minute",
    telephony: "minute",
    llm: "1k_tokens",
    tts: "1k_chars",
    embeddings: "1k_tokens",
};

/** Millipaise on the wire, rupees in the form. One conversion, one place. */
function toMpaise(rupees: string): number | null {
    const value = Number(rupees);
    if (rupees.trim() === "" || !Number.isFinite(value) || value < 0) return null;
    return Math.round(value * 100 * 1000);
}

function toRupees(mpaise: number | null | undefined): string {
    if (mpaise === null || mpaise === undefined) return "";
    return String(mpaise / 100_000);
}

/**
 * The same pair for dollars.
 *
 * Micro-dollars rather than cents because vendor unit prices are small enough
 * that cents would round most of them to zero — Sarvam's LLM works out at four
 * ten-thousandths of a dollar per 1k tokens.
 */
function toMicrosUsd(dollars: string): number | null {
    const value = Number(dollars);
    if (dollars.trim() === "" || !Number.isFinite(value) || value < 0) return null;
    return Math.round(value * 1_000_000);
}

function toDollars(micros: number | null | undefined): string {
    if (micros === null || micros === undefined) return "";
    return String(micros / 1_000_000);
}

type Currency = "inr" | "usd";

/** What is already stored for a row, shown in whichever currency it was quoted in. */
function storedValue(
    currency: Currency,
    mpaise: number | null | undefined,
    micros: number | null | undefined,
): string {
    return currency === "usd" ? toDollars(micros) : toRupees(mpaise);
}

export function ProviderCatalogue() {
    const [providers, setProviders] = useState<ProviderEntry[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [query, setQuery] = useState("");
    const [open, setOpen] = useState<string | null>(null);
    const authReady = useAuthReady();

    const load = useCallback(async () => {
        const result = await listProvidersApiV1AdminBillingProvidersGet({});
        if (result.error) {
            setError(detailFromResult(result, "Failed to load providers"));
        } else {
            setProviders(
                (result.data as unknown as { providers: ProviderEntry[] }).providers,
            );
            setError(null);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (!authReady) return;
        void load();
    }, [authReady, load]);

    const filtered = useMemo(() => {
        if (!providers) return [];
        const needle = query.trim().toLowerCase();
        if (!needle) return providers;
        return providers.filter(
            (p) =>
                p.provider.includes(needle) ||
                p.components.some((c) =>
                    c.models.some((m) => m.model.toLowerCase().includes(needle)),
                ),
        );
    }, [providers, query]);

    if (error) {
        return (
            <section className="glass-panel flex items-center gap-3 px-5 py-4 text-sm text-destructive">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                {error}
            </section>
        );
    }

    if (loading) return <Skeleton className="h-[420px] w-full rounded-2xl" />;

    const unpriced = (providers ?? []).filter((p) => !p.is_priced).length;

    return (
        <section className="glass-panel px-6 pb-6 pt-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                        Provider costs
                    </h2>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                        What each vendor charges us. Not margin — this is the bill we pay,
                        and the managed markup above is what sits on top of it.
                    </p>
                </div>
                <div className="relative">
                    <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                    <Input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="Find a vendor or model"
                        className="w-56 pl-8"
                        aria-label="Filter providers"
                    />
                </div>
            </div>

            {unpriced > 0 && (
                <p className="mt-3 flex items-start gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-xs leading-relaxed text-amber-700 dark:text-amber-400">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    {unpriced} {unpriced === 1 ? "vendor has" : "vendors have"} no price
                    at all. Usage on them is reported as uncosted rather than priced at
                    zero — a zero would read as free and overstate margin.
                </p>
            )}

            <div className="mt-4 space-y-1.5">
                {filtered.map((provider) => (
                    <ProviderRow
                        key={provider.provider}
                        provider={provider}
                        expanded={open === provider.provider}
                        onToggle={() =>
                            setOpen((v) =>
                                v === provider.provider ? null : provider.provider,
                            )
                        }
                        onSaved={load}
                    />
                ))}
                {filtered.length === 0 && (
                    <p className="py-6 text-center text-sm text-muted-foreground">
                        No vendor or model matches “{query}”.
                    </p>
                )}
            </div>
        </section>
    );
}

function ProviderRow({
    provider,
    expanded,
    onToggle,
    onSaved,
}: {
    provider: ProviderEntry;
    expanded: boolean;
    onToggle: () => void;
    onSaved: () => Promise<void>;
}) {
    const priced = provider.components.filter(
        (c) => c.flat_rate_mpaise !== null || c.models.some((m) => m.rate_mpaise),
    ).length;

    return (
        <div
            className={cn(
                "rounded-xl border border-border/60 transition-colors",
                expanded && "border-border bg-muted/20",
            )}
        >
            <button
                type="button"
                onClick={onToggle}
                aria-expanded={expanded}
                className="flex w-full items-center gap-3 px-4 py-3 text-left"
            >
                <ChevronRight
                    className={cn(
                        "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
                        expanded && "rotate-90",
                    )}
                />
                <span className="font-medium text-foreground">{provider.provider}</span>
                <span className="flex flex-wrap gap-1.5">
                    {provider.components.map((c) => (
                        <Badge
                            key={c.component}
                            variant="outline"
                            className="text-[0.6875rem] font-normal"
                        >
                            {COMPONENT_LABEL[c.component] ?? c.component}
                            {c.has_platform_key && (
                                <KeyRound className="ml-1 h-2.5 w-2.5 opacity-60" />
                            )}
                        </Badge>
                    ))}
                </span>
                <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                    {provider.is_priced
                        ? `${priced} of ${provider.components.length} priced`
                        : "no price set"}
                </span>
            </button>

            {expanded && (
                <div className="space-y-4 border-t border-border/60 px-4 py-4">
                    {provider.components.map((component) => (
                        <ComponentEditor
                            key={component.component}
                            provider={provider.provider}
                            component={component}
                            onSaved={onSaved}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}

/**
 * One vendor, one component: the flat rate and every model under it.
 *
 * Only fields the operator actually touched are sent. Sending everything back
 * would open a fresh effective-dated row for every model on every save, which
 * would fill the price history with changes nobody made and make the real ones
 * impossible to find.
 */
function ComponentEditor({
    provider,
    component,
    onSaved,
}: {
    provider: string;
    component: ComponentEntry;
    onSaved: () => Promise<void>;
}) {
    // Opens in the currency this vendor is already priced in, so a dollar
    // vendor never presents a rupee field with a dollar number in it. New
    // vendors default to rupees: that is what we invoice in, and it is the
    // safer wrong guess of the two, being off by the exchange rate rather than
    // by a factor of it.
    const [currency, setCurrency] = useState<Currency>(
        component.flat_rate_micros_usd !== null ? "usd" : "inr",
    );
    const [flat, setFlat] = useState(() =>
        storedValue(
            component.flat_rate_micros_usd !== null ? "usd" : "inr",
            component.flat_rate_mpaise,
            component.flat_rate_micros_usd,
        ),
    );
    const [models, setModels] = useState<Record<string, string>>({});
    const [unit, setUnit] = useState(
        component.flat_unit ?? DEFAULT_UNIT[component.component] ?? "minute",
    );
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [saved, setSaved] = useState(false);

    const flatChanged =
        flat !==
        storedValue(currency, component.flat_rate_mpaise, component.flat_rate_micros_usd);
    const dirty = flatChanged || Object.keys(models).length > 0;

    const submit = async () => {
        const usd = currency === "usd";
        const convert = usd ? toMicrosUsd : toMpaise;
        const flatValue = flatChanged ? convert(flat) : null;

        const modelRates: Record<string, number> = {};
        for (const [model, value] of Object.entries(models)) {
            const amount = convert(value);
            if (amount !== null) modelRates[model] = amount;
        }
        if (flatValue === null && Object.keys(modelRates).length === 0) return;

        setSaving(true);
        setSaveError(null);
        const result = await setProviderRatesApiV1AdminBillingProvidersRatesPut({
            body: {
                provider,
                component: component.component,
                unit,
                // The currency picked here decides which pair of fields carries
                // the price. Sending both would be rejected: a rate is quoted in
                // one currency or the other, never both.
                ...(usd
                    ? {
                          flat_rate_micros_usd: flatValue,
                          model_rates_micros_usd: modelRates,
                      }
                    : { flat_rate_mpaise: flatValue, model_rates: modelRates }),
            },
        });
        if (result.error) {
            setSaveError(detailFromResult(result, "Could not save the rate"));
        } else {
            setModels({});
            await onSaved();
            setSaved(true);
            setTimeout(() => setSaved(false), 2500);
        }
        setSaving(false);
    };

    const unpriced = component.models.filter((m) => m.rate_mpaise === null).length;

    return (
        <div className="rounded-lg border border-border/50 bg-background/40 px-4 py-3">
            <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                    <h3 className="text-sm font-medium text-foreground">
                        {COMPONENT_LABEL[component.component] ?? component.component}
                    </h3>
                    {!component.has_platform_key && (
                        <p className="mt-0.5 flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400">
                            <AlertTriangle className="h-3 w-3" />
                            No platform key stored. A priced model with no key is a
                            managed call that fails when it dials.
                        </p>
                    )}
                </div>
                <div className="flex flex-wrap items-end gap-2">
                    <div className="space-y-1.5">
                        <Label htmlFor={`${provider}-${component.component}-currency`}>
                            Quoted in
                        </Label>
                        <Select
                            value={currency}
                            onValueChange={(v) => {
                                const next = v as Currency;
                                setCurrency(next);
                                // Re-read what is stored in the new currency
                                // rather than carrying the typed number across.
                                // "50" meaning rupees and "50" meaning dollars
                                // are not the same price, and silently keeping
                                // the digits would write the wrong one.
                                setFlat(
                                    storedValue(
                                        next,
                                        component.flat_rate_mpaise,
                                        component.flat_rate_micros_usd,
                                    ),
                                );
                                setModels({});
                            }}
                        >
                            <SelectTrigger
                                id={`${provider}-${component.component}-currency`}
                                className="w-28"
                            >
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="inr">Rupees</SelectItem>
                                <SelectItem value="usd">Dollars</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor={`${provider}-${component.component}-flat`}>
                            All models ({currency === "usd" ? "$" : "₹"})
                        </Label>
                        <Input
                            id={`${provider}-${component.component}-flat`}
                            value={flat}
                            inputMode="decimal"
                            onChange={(e) => setFlat(e.target.value)}
                            placeholder="not set"
                            className="w-28"
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor={`${provider}-${component.component}-unit`}>
                            Unit
                        </Label>
                        <Select value={unit} onValueChange={setUnit}>
                            <SelectTrigger
                                id={`${provider}-${component.component}-unit`}
                                className="w-40"
                            >
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {UNITS.map((u) => (
                                    <SelectItem key={u.value} value={u.value}>
                                        {u.label}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    <Button disabled={!dirty || saving} onClick={submit} size="sm">
                        {saving ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : saved ? (
                            <Check className="h-4 w-4" />
                        ) : (
                            "Apply from now"
                        )}
                    </Button>
                </div>
            </div>

            {component.models.length > 0 && (
                <div className="mt-3 grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
                    {component.models.map((model) => {
                        const pending = models[model.model];
                        const shown =
                            pending !== undefined
                                ? pending
                                : model.from_provider_rate
                                  ? ""
                                  : storedValue(
                                        currency,
                                        model.rate_mpaise,
                                        model.rate_micros_usd,
                                    );
                        return (
                            <div
                                key={model.model}
                                className="flex items-center gap-2 py-0.5"
                            >
                                <span
                                    className={cn(
                                        "flex-1 truncate text-xs",
                                        model.rate_mpaise === null
                                            ? "text-muted-foreground"
                                            : "text-foreground",
                                    )}
                                    title={model.model}
                                >
                                    {model.model}
                                </span>
                                {/* Says where the number comes from. A model
                                    inheriting the flat rate looks identical to one
                                    priced at the same figure, and only one of them
                                    moves when the flat rate does. */}
                                <span className="shrink-0 text-[0.6875rem] text-muted-foreground">
                                    {model.rate_mpaise === null
                                        ? "unpriced"
                                        : model.from_provider_rate
                                          ? "from provider rate"
                                          : "own rate"}
                                </span>
                                <Input
                                    value={shown}
                                    inputMode="decimal"
                                    aria-label={`Rate for ${model.model}`}
                                    placeholder={
                                        model.from_provider_rate
                                            ? storedValue(
                                                  currency,
                                                  model.rate_mpaise,
                                                  model.rate_micros_usd,
                                              )
                                            : "—"
                                    }
                                    onChange={(e) =>
                                        setModels((prev) => ({
                                            ...prev,
                                            [model.model]: e.target.value,
                                        }))
                                    }
                                    className="h-7 w-24 text-xs"
                                />
                            </div>
                        );
                    })}
                </div>
            )}

            {unpriced > 0 && component.flat_rate_mpaise === null && (
                <p className="mt-2 text-xs text-muted-foreground">
                    {unpriced} {unpriced === 1 ? "model has" : "models have"} no price.
                    Set the flat rate to cover all of them at once, or price them
                    individually.
                </p>
            )}

            {saveError && (
                <p className="mt-2 flex items-start gap-2 text-xs text-destructive">
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                    {saveError}
                </p>
            )}
        </div>
    );
}
