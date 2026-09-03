"use client";

/**
 * The rate card — where prices are set.
 *
 * Every other billing screen reads what prices produced. This one is the only
 * place they are decided, and it replaces two things an operator could not
 * reach: a constant in source and the demo seed script.
 *
 * Two ideas the layout has to carry:
 *
 * 1. **Nothing here is edited in place.** Saving a price closes the current one
 *    and opens a new one, so last month's invoice still recomputes to what was
 *    actually charged. The forms say "from now" rather than "save" for that
 *    reason.
 * 2. **A price still on the built-in fallback is not a price anyone chose.** It
 *    is called out rather than shown as though it were configured.
 */

import { AlertTriangle, Check, Coins, Gauge, Loader2, Plus, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    getRateCardApiV1AdminBillingRateCardGet,
    refreshExchangeRateApiV1AdminBillingRateCardExchangeRateRefreshPost,
    retireVolumeTierApiV1AdminBillingRateCardTiersMinPeriodMinutesDelete,
    setExchangeRateApiV1AdminBillingRateCardExchangeRatePut,
    setGlobalPlatformRateApiV1AdminBillingRateCardPlatformPut,
    setVolumeTierApiV1AdminBillingRateCardTiersPut,
} from "@/client/sdk.gen";
import { ManagedMarkupCard } from "@/components/billing/ManagedMarkupCard";
import { MarkupOverridesCard } from "@/components/billing/MarkupOverridesCard";
import { ProviderCatalogue } from "@/components/billing/ProviderCatalogue";
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
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { detailFromResult } from "@/lib/apiError";
import {
    formatDateIST,
    formatMicrosUsd,
    formatNumber,
    formatPaisePerUsd,
    formatRateMpaise,
} from "@/lib/billing/format";

type Tier = {
    id: number;
    name: string;
    min_period_minutes: number;
    platform_rate_micros_usd: number | null;
    platform_rate_mpaise: number | null;
    pulse_seconds: number;
    pulse_is_default: boolean;
    effective_from: string;
};

type ProviderRate = {
    id: number;
    provider: string;
    model: string | null;
    component: string;
    unit: string;
    rate_mpaise: number;
    effective_from: string;
    note: string | null;
};

type Card = {
    global_tier: Tier | null;
    volume_tiers: Tier[];
    provider_rates: ProviderRate[];
    exchange_rate: {
        paise_per_usd: number;
        source: string | null;
        effective_from: string;
        recorded_at: string;
    } | null;
    using_fallback_platform_rate: boolean;
    fallback: {
        platform_rate_micros_usd: number;
        pulse_seconds: number;
        paise_per_usd: number;
    };
};

function priceOf(tier: Tier): string {
    return tier.platform_rate_micros_usd !== null
        ? `${formatMicrosUsd(tier.platform_rate_micros_usd)}/min`
        : `${formatRateMpaise(tier.platform_rate_mpaise)}/min`;
}

export default function RateCardPage() {
    const [card, setCard] = useState<Card | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const authReady = useAuthReady();

    const load = useCallback(async () => {
        const result = await getRateCardApiV1AdminBillingRateCardGet({});
        if (result.error) {
            setError(detailFromResult(result, "Failed to load the rate card"));
        } else {
            setCard(result.data as unknown as Card);
            setError(null);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (!authReady) return;
        void load();
    }, [authReady, load]);

    if (error) {
        return (
            <section className="glass-panel flex items-center gap-3 px-5 py-4 text-sm text-destructive">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                {error}
            </section>
        );
    }

    if (loading) return <Skeleton className="h-[600px] w-full rounded-2xl" />;

    return (
        <div className="space-y-5">
            <p className="text-sm leading-relaxed text-muted-foreground">
                Saving a price here never edits the old one — it closes it and opens a new
                one from the moment you save. Invoices already issued keep the price they
                were charged at.
            </p>

            <PlatformPricePanel card={card} onSaved={load} />
            {/* Sits above provider costs because it multiplies them: reading
                the rates without knowing the multiple in force gives the wrong
                answer to "what does a customer pay". */}
            <ManagedMarkupCard />
            {/* Narrows the multiple above for one model or one provider,
                without the confirmation-code ceremony the blanket value
                needs — a single line can't move every account's bill the
                way that one can. */}
            <MarkupOverridesCard />
            <VolumeTierPanel card={card} onSaved={load} />
            <ExchangeRatePanel card={card} onSaved={load} />
            {/* Grouped by vendor rather than listed flat. The list could not
                show a model we have never priced — a row that does not exist
                cannot be listed — and that gap is the one that bills as
                uncosted usage. */}
            <ProviderCatalogue />
        </div>
    );
}

/** Shared save-state handling: one in-flight action, one error, reload on success. */
function useSaver(onSaved: () => Promise<void>) {
    const [saving, setSaving] = useState<string | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [saved, setSaved] = useState<string | null>(null);

    const run = async (
        key: string,
        call: () => Promise<{ error?: unknown }>,
        fallback: string,
    ) => {
        setSaving(key);
        setSaveError(null);
        setSaved(null);
        const result = await call();
        if (result.error) {
            setSaveError(detailFromResult(result, fallback));
        } else {
            await onSaved();
            setSaved(key);
            setTimeout(() => setSaved(null), 2500);
        }
        setSaving(null);
    };

    return { saving, saveError, saved, run };
}

function SaveFeedback({
    saveError,
    saved,
}: {
    saveError: string | null;
    saved: string | null;
}) {
    if (saveError) {
        return (
            <p className="mt-3 flex items-start gap-2 text-sm text-destructive">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {saveError}
            </p>
        );
    }
    if (saved) {
        return (
            <p className="mt-3 flex items-center gap-2 text-sm text-emerald-600 dark:text-emerald-400">
                <Check className="h-3.5 w-3.5" />
                Saved. It applies from now.
            </p>
        );
    }
    return null;
}

/**
 * The price every account pays without an override.
 *
 * Offers dollars or rupees because the list price is in dollars but a contract
 * can be written in rupees, and the two are genuinely different products: one
 * moves with the exchange rate and one does not.
 */
function PlatformPricePanel({
    card,
    onSaved,
}: {
    card: Card | null;
    onSaved: () => Promise<void>;
}) {
    const current = card?.global_tier;
    const { saving, saveError, saved, run } = useSaver(onSaved);

    const [currency, setCurrency] = useState<"usd" | "inr">("usd");
    const [price, setPrice] = useState("");
    const [pulse, setPulse] = useState("");

    useEffect(() => {
        if (!card) return;
        const usd = current?.platform_rate_micros_usd;
        const inr = current?.platform_rate_mpaise;
        if (inr !== null && inr !== undefined) {
            setCurrency("inr");
            setPrice((inr / 1000 / 100).toFixed(2));
        } else {
            setCurrency("usd");
            setPrice(
                ((usd ?? card.fallback.platform_rate_micros_usd) / 1_000_000).toFixed(3),
            );
        }
        setPulse(String(current?.pulse_seconds ?? card.fallback.pulse_seconds));
    }, [card, current]);

    const submit = () => {
        const value = Number(price);
        if (!Number.isFinite(value) || value < 0) return;
        void run(
            "platform",
            () =>
                setGlobalPlatformRateApiV1AdminBillingRateCardPlatformPut({
                    body: {
                        ...(currency === "usd"
                            ? { platform_rate_micros_usd: Math.round(value * 1_000_000) }
                            : { platform_rate_mpaise: Math.round(value * 100 * 1000) }),
                        pulse_seconds: Number(pulse) || null,
                    },
                }),
            "Could not save the platform price",
        );
    };

    return (
        <section className="glass-panel px-6 pb-6 pt-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="flex items-center gap-2 text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                        <Coins className="h-4 w-4 text-[color:var(--brand-amber)]" />
                        Platform price
                    </h2>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                        What an account pays per minute with no negotiated override.
                    </p>
                </div>
                {card?.using_fallback_platform_rate ? (
                    <Badge
                        variant="secondary"
                        className="bg-amber-500/12 text-amber-700 dark:text-amber-300"
                    >
                        Using the built-in fallback
                    </Badge>
                ) : (
                    current && (
                        <span className="text-xs text-muted-foreground">
                            In force since {formatDateIST(current.effective_from)}
                        </span>
                    )
                )}
            </div>

            {card?.using_fallback_platform_rate && (
                <p className="mt-3 rounded-xl bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-300">
                    No price has ever been set, so billing is falling back to{" "}
                    {formatMicrosUsd(card.fallback.platform_rate_micros_usd)}/min on a{" "}
                    {card.fallback.pulse_seconds}s pulse — a constant in the code, not a
                    number anyone chose. Save one below to take control of it.
                </p>
            )}

            <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <div className="space-y-1.5">
                    <Label htmlFor="currency">Quoted in</Label>
                    <Select
                        value={currency}
                        onValueChange={(v) => setCurrency(v as "usd" | "inr")}
                    >
                        <SelectTrigger id="currency">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="usd">US dollars</SelectItem>
                            <SelectItem value="inr">Rupees</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <div className="space-y-1.5">
                    <Label htmlFor="platform-price">
                        Price per minute {currency === "usd" ? "($)" : "(₹)"}
                    </Label>
                    <Input
                        id="platform-price"
                        value={price}
                        inputMode="decimal"
                        onChange={(e) => setPrice(e.target.value)}
                        placeholder={currency === "usd" ? "0.020" : "1.92"}
                    />
                </div>
                <div className="space-y-1.5">
                    <Label htmlFor="pulse">Pulse (seconds)</Label>
                    <Input
                        id="pulse"
                        value={pulse}
                        inputMode="numeric"
                        onChange={(e) => setPulse(e.target.value)}
                        placeholder="15"
                    />
                </div>
                <div className="flex items-end">
                    <Button disabled={saving !== null} onClick={submit}>
                        {saving === "platform" && (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        )}
                        Set price from now
                    </Button>
                </div>
            </div>

            <p className="mt-3 flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
                <Gauge className="mt-0.5 h-3 w-3 shrink-0" />
                The pulse is the granularity time is rounded up to. At 60 this is ordinary
                whole-minute billing; at 15 a 62-second call bills 75 seconds instead of
                120.
                {currency === "usd" &&
                    " A dollar price is converted at the exchange rate below, so the rupee amount moves with it."}
            </p>

            <SaveFeedback saveError={saveError} saved={saved} />
        </section>
    );
}

/** Prices that kick in once an account has used enough minutes in the period. */
function VolumeTierPanel({
    card,
    onSaved,
}: {
    card: Card | null;
    onSaved: () => Promise<void>;
}) {
    const { saving, saveError, saved, run } = useSaver(onSaved);
    const [adding, setAdding] = useState(false);
    const [name, setName] = useState("");
    const [minutes, setMinutes] = useState("");
    const [price, setPrice] = useState("");

    const submit = () => {
        const value = Number(price);
        const threshold = Number(minutes);
        if (!Number.isFinite(value) || !Number.isFinite(threshold)) return;
        void run(
            "tier",
            () =>
                setVolumeTierApiV1AdminBillingRateCardTiersPut({
                    body: {
                        name: name.trim() || `From ${threshold} min`,
                        min_period_minutes: Math.round(threshold),
                        platform_rate_micros_usd: Math.round(value * 1_000_000),
                    },
                }),
            "Could not save the tier",
        );
        setAdding(false);
        setName("");
        setMinutes("");
        setPrice("");
    };

    return (
        <section className="glass-panel px-6 pb-6 pt-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                        Volume tiers
                    </h2>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                        A cheaper price once an account passes a monthly minute threshold.
                        The highest threshold it has reached wins.
                    </p>
                </div>
                <Button variant="outline" size="sm" onClick={() => setAdding((v) => !v)}>
                    {adding ? (
                        <X className="mr-2 h-3.5 w-3.5" />
                    ) : (
                        <Plus className="mr-2 h-3.5 w-3.5" />
                    )}
                    {adding ? "Cancel" : "Add tier"}
                </Button>
            </div>

            {adding && (
                <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                    <div className="space-y-1.5">
                        <Label htmlFor="tier-name">Name</Label>
                        <Input
                            id="tier-name"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            placeholder="Scale"
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="tier-minutes">From (minutes / month)</Label>
                        <Input
                            id="tier-minutes"
                            value={minutes}
                            inputMode="numeric"
                            onChange={(e) => setMinutes(e.target.value)}
                            placeholder="50000"
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="tier-price">Price per minute ($)</Label>
                        <Input
                            id="tier-price"
                            value={price}
                            inputMode="decimal"
                            onChange={(e) => setPrice(e.target.value)}
                            placeholder="0.015"
                        />
                    </div>
                    <div className="flex items-end">
                        <Button disabled={saving !== null} onClick={submit}>
                            {saving === "tier" && (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            )}
                            Add tier
                        </Button>
                    </div>
                </div>
            )}

            {(card?.volume_tiers.length ?? 0) === 0 ? (
                <p className="mt-4 text-sm text-muted-foreground">
                    No tiers — every account pays the platform price above until you add
                    one.
                </p>
            ) : (
                <div className="mt-4 overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Tier</TableHead>
                                <TableHead className="text-right">From</TableHead>
                                <TableHead className="text-right">Price / min</TableHead>
                                <TableHead className="text-right">Pulse</TableHead>
                                <TableHead className="text-right" />
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {card!.volume_tiers.map((tier) => (
                                <TableRow key={tier.id}>
                                    <TableCell className="font-medium">
                                        {tier.name}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {formatNumber(tier.min_period_minutes)} min
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">
                                        {priceOf(tier)}
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums text-muted-foreground">
                                        {tier.pulse_seconds}s
                                        {/* Distinguish a pulse this tier sets
                                            from one it merely inherits — they
                                            behave differently when the global
                                            pulse changes. */}
                                        {tier.pulse_is_default && (
                                            <span className="ml-1 text-xs">
                                                inherited
                                            </span>
                                        )}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            disabled={saving !== null}
                                            onClick={() =>
                                                run(
                                                    `retire-${tier.id}`,
                                                    () =>
                                                        retireVolumeTierApiV1AdminBillingRateCardTiersMinPeriodMinutesDelete(
                                                            {
                                                                path: {
                                                                    min_period_minutes:
                                                                        tier.min_period_minutes,
                                                                },
                                                            },
                                                        ),
                                                    "Could not retire the tier",
                                                )
                                            }
                                        >
                                            Retire
                                        </Button>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}

            <SaveFeedback saveError={saveError} saved={saved} />
        </section>
    );
}

/**
 * The USD→INR rate.
 *
 * Deliberately cannot be backdated: repricing calls that have already been
 * invoiced is not something a form should be able to do.
 */
function ExchangeRatePanel({
    card,
    onSaved,
}: {
    card: Card | null;
    onSaved: () => Promise<void>;
}) {
    const { saving, saveError, saved, run } = useSaver(onSaved);
    const [rate, setRate] = useState("");
    const [source, setSource] = useState("");

    useEffect(() => {
        if (!card) return;
        const paise = card.exchange_rate?.paise_per_usd ?? card.fallback.paise_per_usd;
        setRate((paise / 100).toFixed(2));
        setSource(card.exchange_rate?.source ?? "");
    }, [card]);

    return (
        <section className="glass-panel px-6 pb-6 pt-5">
            <h2 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                Exchange rate
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
                What a dollar-quoted price converts to in rupees. Applies from the moment
                you save — never backwards, so invoices already sent are untouched.
            </p>

            <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <div className="space-y-1.5">
                    <Label htmlFor="fx">Rupees per dollar</Label>
                    <Input
                        id="fx"
                        value={rate}
                        inputMode="decimal"
                        onChange={(e) => setRate(e.target.value)}
                        placeholder="96.00"
                    />
                </div>
                <div className="space-y-1.5">
                    <Label htmlFor="fx-source">Source</Label>
                    <Input
                        id="fx-source"
                        value={source}
                        onChange={(e) => setSource(e.target.value)}
                        placeholder="RBI reference rate"
                    />
                </div>
                <div className="flex items-end gap-2">
                    {/* The daily cron (`refresh_exchange_rate`, 02:30 UTC) is the
                        only price in this system with a real feed behind it, and
                        a fetch that never succeeds writes nothing, logs a
                        warning, and leaves the fallback in force forever with no
                        visible symptom. This is how an operator finds that out:
                        press it and either a rate lands or the 502 names the
                        upstream problem. */}
                    <Button
                        variant="outline"
                        disabled={saving !== null}
                        onClick={() =>
                            void run(
                                "fx-refresh",
                                refreshExchangeRateApiV1AdminBillingRateCardExchangeRateRefreshPost,
                                "Could not reach the exchange rate feed",
                            )
                        }
                    >
                        {saving === "fx-refresh" && (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        )}
                        Fetch now
                    </Button>
                    <Button
                        disabled={saving !== null}
                        onClick={() => {
                            const value = Number(rate);
                            if (!Number.isFinite(value) || value <= 0) return;
                            void run(
                                "fx",
                                () =>
                                    setExchangeRateApiV1AdminBillingRateCardExchangeRatePut(
                                        {
                                            body: {
                                                paise_per_usd: Math.round(value * 100),
                                                source: source.trim() || null,
                                            },
                                        },
                                    ),
                                "Could not save the exchange rate",
                            );
                        }}
                    >
                        {saving === "fx" && (
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        )}
                        Update rate
                    </Button>
                </div>
                {card?.exchange_rate ? (
                    <div className="flex items-end text-xs text-muted-foreground">
                        Currently {formatPaisePerUsd(card.exchange_rate.paise_per_usd)},
                        set {formatDateIST(card.exchange_rate.recorded_at)}
                    </div>
                ) : (
                    card && (
                        <div className="flex items-end gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                            <span>
                                No rate on file — every dollar-quoted charge is
                                settling at the built-in{" "}
                                {formatPaisePerUsd(card.fallback.paise_per_usd)}{" "}
                                fallback. Fetch or set one.
                            </span>
                        </div>
                    )
                )}
            </div>

            <SaveFeedback saveError={saveError} saved={saved} />
        </section>
    );
}
