/**
 * The model dropdown, driven by what Decibyl actually sells.
 *
 * The Advanced screen used to list every vendor and model this codebase has
 * ever integrated, read straight off the provider registry's `examples`. That
 * list answers "what could this software talk to", which is a question nobody
 * buying a phone agent is asking. It included models we hold no key for and
 * models nobody has priced — both of which save cleanly, build an agent, and
 * fail on the first live call or bill at the platform fee alone.
 *
 * Two lists replace it, and they are exactly the two things a customer can
 * legitimately run:
 *
 * * **{@link CatalogueModelSelect}** — the managed catalogue. Every entry is
 *   something an operator put on sale, we hold a key for, and has a rate on
 *   file, so every entry carries what it costs a minute.
 * * **{@link OwnKeyModelSelect}** — what this account's own key unlocks, minus
 *   anything we already sell. The escape hatch, asked of the vendor with their
 *   key so the list is what their account can actually reach.
 *
 * Neither invents an option. A slot with nothing to offer says so rather than
 * falling back to the registry, because the registry is the thing that was
 * wrong.
 */

"use client";

import { AlertTriangle, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { discoverModelsApiV1ProviderKeysModelsGet } from "@/client/sdk.gen";
import { Input } from "@/components/ui/input";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { formatPaise } from "@/lib/billing/format";

/** One sellable model, as `GET /agent-options/catalogue` serves it. */
export type CatalogueOption = {
    provider: string;
    model: string;
    /** What an operator named it. Falls back to the vendor's own id. */
    label: string;
    /**
     * This slot's own contribution to a minute, markup included — not the
     * price of a call, which also has the other slots, telephony and the
     * platform fee in it.
     *
     * Null when the rate vanished between the sellable check and the quote.
     * Never zero: zero would read as free, which is the one thing it never
     * means.
     */
    paise_per_minute: number | null;
    /** Priced against the vendor's default rate rather than this model's. */
    approximate: boolean;
};

/** Sellable models per slot, keyed `stt` / `llm` / `tts` / `realtime`. */
export type SlotCatalogue = Record<string, CatalogueOption[]>;

/**
 * Whether a slot pointed at this provider runs on Decibyl's key or the
 * account's own.
 *
 * The billing question, so it is one function rather than a condition written
 * twice. Three inputs, and the order they are read in is the whole rule:
 *
 * * we do not sell this vendor → their key, whatever else is true;
 * * they already saved it as ours → ours, and a key they happen to also hold
 *   does not change a choice they made;
 * * otherwise ours exactly when they hold no key of their own for it — which
 *   is what the picker offered them, and what the price beside it meant.
 *
 * The third clause is the one that matters. A slot showing a managed price and
 * saving `use_platform_key: false` resolves no credential at dial time: it
 * saves cleanly, builds an agent, and fails on the first live call.
 */
export function runsOnPlatformKey({
    sells,
    holdsOwnKey,
    saved,
}: {
    /** The managed catalogue offers this slot on this provider. */
    sells: boolean;
    /** The account has its own key stored for this provider. */
    holdsOwnKey: boolean;
    /** What the stored configuration says today. */
    saved: boolean;
}): boolean {
    if (!sells) return false;
    return saved || !holdsOwnKey;
}

/** A slot's price line, or the reason there isn't one. */
export function priceCaption(option: CatalogueOption | undefined): string | null {
    if (!option) return null;
    if (option.paise_per_minute === null) return "No price on file for this model.";
    const price = `${formatPaise(option.paise_per_minute)} a minute for this slot`;
    return option.approximate
        ? `About ${price} — priced at this vendor's default rate rather than this model's.`
        : price;
}

/** The catalogue entry a saved slot resolves to, if it is one we sell. */
export function findCatalogueOption(
    options: CatalogueOption[] | undefined,
    provider: string | undefined,
    model: string | undefined,
): CatalogueOption | undefined {
    if (!options || !provider || !model) return undefined;
    return options.find((o) => o.provider === provider && o.model === model);
}

function Caption({ children }: { children: React.ReactNode }) {
    return <p className="text-xs text-muted-foreground">{children}</p>;
}

/**
 * Models we sell on this provider, cheapest first, each with its price.
 *
 * The price sits on the option itself rather than only under the closed
 * select: choosing between two models is a comparison, and a comparison you
 * have to make by opening and closing a dropdown four times is one people give
 * up on and take the default.
 */
export function CatalogueModelSelect({
    options,
    value,
    onChange,
}: {
    options: CatalogueOption[];
    value: string;
    onChange: (model: string) => void;
}) {
    const selected = options.find((o) => o.model === value);
    // A saved model that has since been withdrawn from sale, or was never in
    // the catalogue. Kept selectable so the select renders its own value —
    // silently blanking somebody's stored choice is how an agent changes
    // behaviour without anyone deciding to change it.
    const stale = value && !selected;

    return (
        <div className="space-y-1.5">
            <Select value={value} onValueChange={(next) => next && onChange(next)}>
                <SelectTrigger className="w-full">
                    <SelectValue placeholder="Select model" />
                </SelectTrigger>
                <SelectContent>
                    {stale && (
                        <SelectItem value={value}>{value} (no longer offered)</SelectItem>
                    )}
                    {options.map((option) => (
                        <SelectItem key={option.model} value={option.model}>
                            <span className="flex w-full items-center justify-between gap-6">
                                <span>{option.label}</span>
                                <span className="text-xs text-muted-foreground">
                                    {option.paise_per_minute === null
                                        ? "—"
                                        : `${option.approximate ? "~" : ""}${formatPaise(
                                              option.paise_per_minute,
                                          )}/min`}
                                </span>
                            </span>
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>
            {stale ? (
                <Caption>
                    We no longer offer this model. Existing agents keep running on it;
                    pick another to move off it.
                </Caption>
            ) : (
                selected && <Caption>{priceCaption(selected)}</Caption>
            )}
        </div>
    );
}

type Discovery = {
    models: { id: string; suggested: boolean }[];
    source: string;
    note: string | null;
    allow_custom_model: boolean;
    provided_by_decibyl: number;
};

/**
 * What this account's key unlocks on a vendor we do not sell.
 *
 * Asked of the vendor with their key, so a tier that excludes a model cannot
 * offer it here and then fail at session open on a live call. Nothing is
 * priced: this runs on their account, and they are billed by the vendor for it
 * — the only thing on their Decibyl invoice is the platform fee.
 */
export function OwnKeyModelSelect({
    component,
    provider,
    value,
    onChange,
}: {
    component: string;
    provider: string;
    value: string;
    onChange: (model: string) => void;
}) {
    const [discovery, setDiscovery] = useState<Discovery | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError(null);
        setDiscovery(null);
        (async () => {
            const response = await discoverModelsApiV1ProviderKeysModelsGet({
                query: { component, provider },
            });
            if (cancelled) return;
            setLoading(false);
            if (response.error || !response.data) {
                setError(
                    `We could not read the model list from ${provider} with your stored key.`,
                );
                return;
            }
            setDiscovery(response.data as unknown as Discovery);
        })();
        return () => {
            cancelled = true;
        };
    }, [component, provider]);

    if (loading) {
        return (
            <div className="flex h-9 items-center gap-2 rounded-md border border-input px-3 text-sm text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Reading what your {provider} key can use…
            </div>
        );
    }

    // Discovery failed, or the vendor has no list endpoint and we know nothing
    // about this slot. Either way the customer knows their own model id, and
    // refusing to accept it would strand a working key behind our ignorance.
    if (error || !discovery) {
        return (
            <div className="space-y-1.5">
                <Input
                    value={value}
                    placeholder="Model id"
                    onChange={(event) => onChange(event.target.value)}
                />
                <p className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-500">
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                    {error ?? "No model list available."} Type the model id instead.
                </p>
            </div>
        );
    }

    const models = discovery.models;
    const stale = value && !models.some((m) => m.id === value);

    return (
        <div className="space-y-1.5">
            {models.length === 0 && !stale ? (
                <Input
                    value={value}
                    placeholder="Model id"
                    onChange={(event) => onChange(event.target.value)}
                />
            ) : (
                <Select value={value} onValueChange={(next) => next && onChange(next)}>
                    <SelectTrigger className="w-full">
                        <SelectValue placeholder="Select model" />
                    </SelectTrigger>
                    <SelectContent>
                        {stale && <SelectItem value={value}>{value}</SelectItem>}
                        {models.map((model) => (
                            <SelectItem key={model.id} value={model.id}>
                                {model.id}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            )}
            <Caption>
                {models.length === 0
                    ? `Your ${provider} key reaches nothing we don't already sell.`
                    : `${models.length} model${models.length === 1 ? "" : "s"} ${
                          discovery.source === "vendor"
                              ? `listed by ${provider}`
                              : "we know of"
                      }, billed by ${provider} to your account.`}
                {discovery.provided_by_decibyl > 0 &&
                    ` ${discovery.provided_by_decibyl} more are hidden here because we already offer them.`}
            </Caption>
        </div>
    );
}
