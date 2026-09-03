"use client";

/**
 * What we sell, chosen from what the vendor actually serves.
 *
 * The operator's loop, in one place: pick an installed key, read the models
 * that provider serves — asked with our own key, not read out of a document —
 * tick the ones Decibyl offers, and see immediately which of them still need a
 * price before a customer can be shown them.
 *
 * Two things this screen has to be honest about, because both are invisible
 * failures rather than errors:
 *
 * **Where the list came from.** "These are the models OpenAI told us about" and
 * "these are the ones we have heard of" are different claims. A vendor that is
 * down falls back to the second, and a screen presenting them identically is
 * lying by omission.
 *
 * **Offered but unpriced.** A model on sale with no rate row does not fail. It
 * bills the platform fee, records its provider usage as uncosted, and reports
 * margin we did not earn. So it is drawn as a warning here, and the customer's
 * picker omits it entirely.
 */

import { AlertTriangle, Check, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { detailFromResult } from "@/lib/apiError";
import { cn } from "@/lib/utils";

type DiscoveredModel = { id: string; suggested: boolean; offered: boolean };

type Discovery = {
    provider: string;
    component: string;
    /** "vendor" — they listed these. "catalogue" — we could not ask. */
    source: string;
    note: string | null;
    has_key: boolean;
    allow_custom_model: boolean;
    models: DiscoveredModel[];
};

export type CatalogueRow = {
    component: string;
    provider: string;
    model: string;
    label: string;
    enabled: boolean;
    is_priced: boolean;
    priced_by_fallback: boolean;
    has_key: boolean;
    is_sellable: boolean;
};

export function ModelCatalogue({
    component,
    provider,
    onSaved,
}: {
    component: string;
    provider: string;
    onSaved?: () => void;
}) {
    const [found, setFound] = useState<Discovery | null>(null);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [labels, setLabels] = useState<Record<string, string>>({});
    const [custom, setCustom] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);

    const discover = useCallback(async () => {
        setLoading(true);
        setError(null);
        const result = await client.get({
            url: "/api/v1/admin/provider-keys/models",
            query: { component, provider },
        });
        setLoading(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not list models"));
            return;
        }
        const data = result.data as Discovery;
        setFound(data);
        // Pre-ticked with what is already on sale, so saving without touching
        // anything is a no-op rather than a withdrawal of the whole catalogue.
        setSelected(new Set(data.models.filter((m) => m.offered).map((m) => m.id)));
    }, [component, provider]);

    useEffect(() => {
        void discover();
    }, [discover]);

    const save = useCallback(async () => {
        setSaving(true);
        setError(null);
        const result = await client.put({
            url: "/api/v1/admin/provider-keys/models",
            body: {
                component,
                provider,
                models: [...selected],
                labels,
            },
        });
        setSaving(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not save the catalogue"));
            return;
        }
        onSaved?.();
        await discover();
    }, [component, provider, selected, labels, discover, onSaved]);

    const toggle = (id: string) =>
        setSelected((current) => {
            const next = new Set(current);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });

    if (loading && !found) {
        return (
            <div className="flex h-24 items-center justify-center">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
        );
    }

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium">
                    {provider} · {component}
                </span>
                {found?.source === "vendor" ? (
                    <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
                        listed by {provider}
                    </span>
                ) : (
                    <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                        models we know of
                    </span>
                )}
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => void discover()}
                    disabled={loading}
                >
                    <RefreshCw
                        className={cn("mr-1.5 h-3.5 w-3.5", loading && "animate-spin")}
                    />
                    Re-read
                </Button>
            </div>

            {/* Said out loud rather than logged: an operator seeing a short list
                needs to know whether it is short because the vendor is small or
                because the request failed. */}
            {found?.note && (
                <p className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>{found.note}</span>
                </p>
            )}

            {error && (
                <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
            )}

            <div className="max-h-72 space-y-1 overflow-y-auto rounded-lg border p-2">
                {(found?.models ?? []).map((model) => {
                    const on = selected.has(model.id);
                    return (
                        <div
                            key={model.id}
                            className={cn(
                                "flex items-center gap-2 rounded-md px-2 py-1.5",
                                on && "bg-primary/5",
                            )}
                        >
                            <button
                                type="button"
                                aria-pressed={on}
                                onClick={() => toggle(model.id)}
                                className={cn(
                                    "flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                                    on
                                        ? "border-primary bg-primary text-primary-foreground"
                                        : "border-input",
                                )}
                            >
                                {on && <Check className="h-3 w-3" />}
                            </button>
                            <span className="min-w-0 flex-1 truncate font-mono text-xs">
                                {model.id}
                            </span>
                            {model.suggested && (
                                <span
                                    className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                                    title="This codebase already integrates this model for this slot"
                                >
                                    integrated
                                </span>
                            )}
                            {on && (
                                <Input
                                    value={labels[model.id] ?? ""}
                                    placeholder="Name customers see"
                                    onChange={(e) =>
                                        setLabels({
                                            ...labels,
                                            [model.id]: e.target.value,
                                        })
                                    }
                                    className="h-7 w-44 text-xs"
                                />
                            )}
                        </div>
                    );
                })}
                {(found?.models ?? []).length === 0 && (
                    <p className="px-2 py-4 text-center text-sm text-muted-foreground">
                        Nothing to show. Install a key for this provider first.
                    </p>
                )}
            </div>

            {/* Vendors ship new models faster than we cut releases, so where one
                accepts a string we do not enumerate, it can be typed. The rate
                card is what stops that being dangerous: an unpriced model is
                never shown to a customer. */}
            {found?.allow_custom_model && (
                <div className="flex gap-2">
                    <Input
                        value={custom}
                        placeholder="Add a model id this vendor accepts"
                        onChange={(e) => setCustom(e.target.value)}
                        className="h-8 text-xs"
                    />
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={!custom.trim()}
                        onClick={() => {
                            const id = custom.trim();
                            setFound((current) =>
                                current && !current.models.some((m) => m.id === id)
                                    ? {
                                          ...current,
                                          models: [
                                              { id, suggested: false, offered: false },
                                              ...current.models,
                                          ],
                                      }
                                    : current,
                            );
                            toggle(id);
                            setCustom("");
                        }}
                    >
                        Add
                    </Button>
                </div>
            )}

            <div className="flex items-center gap-3">
                <Button type="button" size="sm" onClick={() => void save()} disabled={saving}>
                    {saving ? (
                        <>
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            Saving…
                        </>
                    ) : (
                        `Offer ${selected.size} model${selected.size === 1 ? "" : "s"}`
                    )}
                </Button>
                <span className="text-xs text-muted-foreground">
                    Unticked models are withdrawn from sale. Set a price for each on
                    the rate card before customers can choose it.
                </span>
            </div>
        </div>
    );
}

/**
 * The whole catalogue, and what each row still needs.
 *
 * The gap this closes: nothing anywhere said "we are selling four models we
 * have never priced". Every row that is offered but not sellable is a call that
 * would bill the platform fee alone and report margin we did not earn.
 */
export function CatalogueSummary({ rows }: { rows: CatalogueRow[] }) {
    const blocked = rows.filter((row) => row.enabled && !row.is_sellable);
    const approximate = rows.filter((row) => row.is_sellable && row.priced_by_fallback);

    if (rows.length === 0) return null;

    return (
        <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
                {rows.filter((r) => r.is_sellable).length} of {rows.length} models are
                sellable — keyed, priced and on sale.
            </p>

            {blocked.length > 0 && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm dark:border-amber-900/50 dark:bg-amber-950/30">
                    <p className="flex items-center gap-1.5 font-medium text-amber-800 dark:text-amber-300">
                        <AlertTriangle className="h-4 w-4" />
                        {blocked.length} offered model
                        {blocked.length === 1 ? "" : "s"} cannot be sold yet
                    </p>
                    <ul className="mt-2 space-y-1 text-xs text-amber-800 dark:text-amber-300">
                        {blocked.map((row) => (
                            <li key={`${row.component}-${row.provider}-${row.model}`}>
                                <span className="font-mono">
                                    {row.provider}/{row.model}
                                </span>{" "}
                                ({row.component}) —{" "}
                                {!row.has_key && "no platform key"}
                                {!row.has_key && !row.is_priced && ", "}
                                {!row.is_priced && "no rate on the rate card"}
                            </li>
                        ))}
                    </ul>
                    <p className="mt-2 text-xs text-amber-800 dark:text-amber-300">
                        These are hidden from customers rather than shown unpriced.
                    </p>
                </div>
            )}

            {approximate.length > 0 && (
                <p className="text-xs text-muted-foreground">
                    {approximate.length} model{approximate.length === 1 ? " is" : "s are"}{" "}
                    priced against their provider&apos;s default rather than their own
                    rate — two models on one provider will show the same price.
                </p>
            )}
        </div>
    );
}
