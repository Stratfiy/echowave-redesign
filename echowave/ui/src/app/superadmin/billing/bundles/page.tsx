"use client";

/**
 * Bundles — what we sell, what it runs on, and what it earns.
 *
 * A bundle is the choice a customer actually makes ("Everyday", "Natural"),
 * and it names *tiers* rather than vendors. A tier names a vendor somewhere
 * else. That indirection is what lets us move a model without touching a
 * customer's stored configuration, and it is also why deciding a bundle's
 * economics used to need two screens and a calculator.
 *
 * Three things this screen insists on:
 *
 * **The price is not computed here.** Every figure comes from the server,
 * through the same estimator that quotes the customer and that the invoice
 * reconciles against. Recomputing a per-minute price in the browser would be a
 * second calculation, and every pricing defect found this week was a second
 * calculation that had drifted from the first.
 *
 * **Cost and margin sit beside the price.** A bundle whose vendors cost more
 * than it charges is the one thing worth finding here, and it is invisible on
 * a screen that shows only what the customer pays.
 *
 * **Changing a tier is loud.** A stored agent configuration names a tier, so
 * repointing one reaches every agent already built — not only the ones created
 * next. That is usually the intent; it should never be a surprise.
 */

import { AlertTriangle, Check, Loader2, Pencil, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
    bundleEconomicsApiV1AdminBillingBundlesEconomicsGet,
    listBundlesApiV1AdminBillingBundlesGet,
    managedTierChoicesApiV1AdminBillingManagedTiersChoicesGet,
    setManagedTierApiV1AdminBillingManagedTiersPut,
    upsertBundleApiV1AdminBillingBundlesPut,
} from "@/client/sdk.gen";
import { useAuthReady } from "@/components/charts/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { detailFromError } from "@/lib/apiError";
import { cn } from "@/lib/utils";

type Slot = {
    component: string;
    tier: string | null;
    provider: string | null;
    model: string | null;
    chosen_by_customer: boolean;
};

type Bundle = {
    slug: string;
    label: string;
    blurb: string | null;
    architecture: string;
    stt_tier: string | null;
    tts_tier: string | null;
    llm_tier: string | null;
    realtime_tier: string | null;
    display_order: number;
    is_enabled: boolean;
    slots: Slot[];
    residency: { india_only: boolean; reason: string };
};

type Variant = {
    tier: string;
    label: string;
    blurb: string;
    paise_per_minute: number;
    cost_paise_per_minute: number;
    margin_paise_per_minute: number;
    margin_pct: number | null;
    india_only: boolean;
};

type Economics = {
    slug: string;
    label: string;
    architecture: string;
    variants: Variant[];
};

type Choice = {
    provider: string;
    models: string[];
    allow_custom_model: boolean;
};

const COMPONENT_LABEL: Record<string, string> = {
    stt: "Speech to text",
    llm: "Language model",
    tts: "Text to speech",
    realtime: "Speech to speech",
    embeddings: "Embeddings",
};

/** Paise to rupees, for a per-minute figure that is always small. */
function rupees(paise: number): string {
    return `₹${(paise / 100).toFixed(2)}`;
}

export default function BundlesPage() {
    const [bundles, setBundles] = useState<Bundle[] | null>(null);
    const [economics, setEconomics] = useState<Economics[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const authReady = useAuthReady();

    const load = useCallback(async () => {
        const [list, money] = await Promise.all([
            listBundlesApiV1AdminBillingBundlesGet({}),
            bundleEconomicsApiV1AdminBillingBundlesEconomicsGet({}),
        ]);
        if (list.error) {
            setError(detailFromError(list.error, "Failed to load bundles"));
        } else {
            setBundles((list.data as unknown as { bundles: Bundle[] }).bundles);
            setError(null);
        }
        // The money is a separate concern from the configuration: a rate card
        // that cannot be read should not blank the screen that edits vendors.
        if (!money.error && money.data) {
            setEconomics((money.data as unknown as { bundles: Economics[] }).bundles);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (!authReady) return;
        void load();
    }, [authReady, load]);

    const moneyBySlug = useMemo(
        () => new Map(economics.map((e) => [e.slug, e])),
        [economics],
    );

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
                A bundle names tiers, and a tier names a vendor. Repointing a tier reaches
                every agent already built, not only the ones created next — that is the
                point of the indirection, and the thing to be sure about before saving.
            </p>

            {(bundles ?? []).map((bundle) => (
                <BundleCard
                    key={bundle.slug}
                    bundle={bundle}
                    economics={moneyBySlug.get(bundle.slug)}
                    onSaved={load}
                />
            ))}
        </div>
    );
}

function BundleCard({
    bundle,
    economics,
    onSaved,
}: {
    bundle: Bundle;
    economics: Economics | undefined;
    onSaved: () => Promise<void>;
}) {
    const [busy, setBusy] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);

    const toggle = async (enabled: boolean) => {
        setBusy(true);
        setSaveError(null);
        const result = await upsertBundleApiV1AdminBillingBundlesPut({
            body: { slug: bundle.slug, is_enabled: enabled },
        });
        if (result.error) {
            setSaveError(detailFromError(result.error, "Could not save the bundle"));
        } else {
            await onSaved();
        }
        setBusy(false);
    };

    const losing = economics?.variants.some((v) => v.margin_paise_per_minute <= 0);

    return (
        <section className="glass-panel px-6 pb-6 pt-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="flex items-center gap-2">
                        <h2 className="text-[0.9375rem] font-semibold tracking-[-0.018em] text-foreground">
                            {bundle.label}
                        </h2>
                        <Badge variant="outline" className="text-[0.6875rem] font-normal">
                            {bundle.architecture === "realtime"
                                ? "speech to speech"
                                : "pipeline"}
                        </Badge>
                        {bundle.residency.india_only && (
                            <Badge
                                variant="outline"
                                className="text-[0.6875rem] font-normal text-emerald-700 dark:text-emerald-400"
                                title={bundle.residency.reason}
                            >
                                processed in India
                            </Badge>
                        )}
                    </div>
                    {bundle.blurb && (
                        <p className="mt-0.5 text-xs text-muted-foreground">
                            {bundle.blurb}
                        </p>
                    )}
                </div>
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    {busy ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                        <Switch
                            checked={bundle.is_enabled}
                            onCheckedChange={toggle}
                            aria-label={`Offer ${bundle.label}`}
                        />
                    )}
                    {bundle.is_enabled ? "Offered" : "Hidden"}
                </label>
            </div>

            <div className="mt-4 grid gap-4 lg:grid-cols-2">
                <div className="space-y-2">
                    <h3 className="text-xs font-medium uppercase tracking-[0.05em] text-muted-foreground">
                        Runs on
                    </h3>
                    {bundle.slots.map((slot) => (
                        <SlotRow key={slot.component} slot={slot} onSaved={onSaved} />
                    ))}
                </div>

                <div>
                    <h3 className="mb-2 text-xs font-medium uppercase tracking-[0.05em] text-muted-foreground">
                        Per connected minute
                    </h3>
                    {economics ? (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Variant</TableHead>
                                    <TableHead className="text-right">Customer</TableHead>
                                    <TableHead className="text-right">Our cost</TableHead>
                                    <TableHead className="text-right">Margin</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {economics.variants.map((variant) => (
                                    <TableRow key={variant.tier}>
                                        <TableCell className="font-medium">
                                            {variant.label}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            {rupees(variant.paise_per_minute)}
                                        </TableCell>
                                        <TableCell className="text-right tabular-nums text-muted-foreground">
                                            {rupees(variant.cost_paise_per_minute)}
                                        </TableCell>
                                        <TableCell
                                            className={cn(
                                                "text-right tabular-nums",
                                                variant.margin_paise_per_minute <= 0 &&
                                                    "text-destructive",
                                            )}
                                        >
                                            {rupees(variant.margin_paise_per_minute)}
                                            {variant.margin_pct !== null && (
                                                <span className="ml-1 text-xs text-muted-foreground">
                                                    {variant.margin_pct}%
                                                </span>
                                            )}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    ) : (
                        <p className="text-sm text-muted-foreground">
                            No pricing available. Set this bundle&apos;s vendor rates on
                            the rate card — until then its calls are reported as uncosted
                            rather than priced at zero.
                        </p>
                    )}

                    {losing && (
                        <p className="mt-2 flex items-start gap-2 text-xs text-destructive">
                            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                            At least one variant earns nothing or loses money on every
                            minute. Either the vendor rate is unpriced or the markup is
                            below cost.
                        </p>
                    )}
                </div>
            </div>

            {saveError && (
                <p className="mt-3 flex items-start gap-2 text-sm text-destructive">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    {saveError}
                </p>
            )}
        </section>
    );
}

/**
 * One slot: what serves it, and the control that repoints it.
 *
 * The vendor list is read from the registry on the server, so a provider added
 * to the platform is selectable the day it lands. Saving validates there —
 * an unbuildable provider, or one with no platform key, is refused whether it
 * was picked from the list or typed into it.
 */
function SlotRow({ slot, onSaved }: { slot: Slot; onSaved: () => Promise<void> }) {
    const [editing, setEditing] = useState(false);
    const [choices, setChoices] = useState<Choice[] | null>(null);
    const [provider, setProvider] = useState(slot.provider ?? "");
    const [model, setModel] = useState(slot.model ?? "");
    const [busy, setBusy] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);

    const open = async () => {
        setEditing(true);
        setSaveError(null);
        if (choices) return;
        const result = await managedTierChoicesApiV1AdminBillingManagedTiersChoicesGet({
            query: { component: slot.component },
        });
        if (!result.error && result.data) {
            setChoices((result.data as unknown as { choices: Choice[] }).choices);
        }
    };

    const save = async () => {
        if (!slot.tier || !provider) return;
        setBusy(true);
        setSaveError(null);
        const result = await setManagedTierApiV1AdminBillingManagedTiersPut({
            body: {
                component: slot.component,
                tier: slot.tier,
                provider,
                model,
            },
        });
        if (result.error) {
            setSaveError(detailFromError(result.error, "Could not repoint the tier"));
        } else {
            setEditing(false);
            await onSaved();
        }
        setBusy(false);
    };

    if (slot.chosen_by_customer) {
        return (
            <div className="rounded-lg border border-dashed border-border/60 px-3 py-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">
                    {COMPONENT_LABEL[slot.component] ?? slot.component}
                </span>{" "}
                — the customer picks this one. Its price is the variant table beside this,
                one row per choice.
            </div>
        );
    }

    const models = choices?.find((c) => c.provider === provider)?.models ?? [];

    return (
        <div className="rounded-lg border border-border/60 px-3 py-2">
            <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-medium text-foreground">
                    {COMPONENT_LABEL[slot.component] ?? slot.component}
                </span>
                <Badge variant="outline" className="text-[0.6875rem] font-normal">
                    {slot.tier}
                </Badge>
                <span className="text-muted-foreground">
                    {slot.provider} · {slot.model}
                </span>
                <Button
                    variant="ghost"
                    size="sm"
                    className="ml-auto h-6 px-2"
                    onClick={() => (editing ? setEditing(false) : void open())}
                    aria-label={`Change ${slot.component}`}
                >
                    {editing ? <X className="h-3 w-3" /> : <Pencil className="h-3 w-3" />}
                </Button>
            </div>

            {editing && (
                <div className="mt-2 space-y-2">
                    <div className="flex flex-wrap gap-2">
                        <Select
                            value={provider}
                            onValueChange={(value) => {
                                setProvider(value);
                                // The previous vendor's model is meaningless on a
                                // new vendor, and leaving it there would send a
                                // pairing that cannot be built.
                                const next = choices?.find((c) => c.provider === value);
                                setModel(next?.models[0] ?? "");
                            }}
                        >
                            <SelectTrigger className="h-8 w-48 text-xs">
                                <SelectValue placeholder="Provider" />
                            </SelectTrigger>
                            <SelectContent>
                                {(choices ?? []).map((choice) => (
                                    <SelectItem
                                        key={choice.provider}
                                        value={choice.provider}
                                    >
                                        {choice.provider}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>

                        <Select value={model} onValueChange={setModel}>
                            <SelectTrigger className="h-8 w-56 text-xs">
                                <SelectValue placeholder="Model" />
                            </SelectTrigger>
                            <SelectContent>
                                {models.map((name) => (
                                    <SelectItem key={name} value={name}>
                                        {name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>

                        <Button size="sm" className="h-8" disabled={busy} onClick={save}>
                            {busy ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                                <Check className="h-3.5 w-3.5" />
                            )}
                        </Button>
                    </div>

                    <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
                        Takes effect immediately, on every agent using the{" "}
                        <span className="font-medium">{slot.tier}</span> tier — including
                        calls already scheduled. The price beside this updates from the
                        rate card once saved.
                    </p>

                    {saveError && (
                        <p className="flex items-start gap-2 text-xs text-destructive">
                            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                            {saveError}
                        </p>
                    )}
                </div>
            )}
        </div>
    );
}
