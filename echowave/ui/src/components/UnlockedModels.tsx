"use client";

/**
 * What a key you added actually gets you.
 *
 * Decibyl manages its own providers — curated, keyed, priced and billed on your
 * invoice. A key you bring is the escape hatch for a model we do **not** offer,
 * so this lists exactly that: the models your key reaches, minus everything we
 * already provide.
 *
 * Excluding what we sell is the product decision, not a tidying-up. Offering a
 * key-shaped way to buy a model that is already on your Decibyl invoice would
 * mean paying the vendor directly for something you are already paying us for.
 *
 * The list is fetched with your key rather than read from a document, so a
 * vendor tier that excludes a model does not let you pick one that fails at
 * session open — on a live call, which is where that failure used to land.
 */

import { AlertTriangle, Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { detailFromResult } from "@/lib/apiError";

type Unlocked = {
    provider: string;
    component: string;
    source: string;
    note: string | null;
    models: { id: string; suggested: boolean }[];
    /** How many were hidden because Decibyl already sells them. */
    provided_by_decibyl: number;
};

const COMPONENT_NOUN: Record<string, string> = {
    stt: "transcription",
    llm: "language",
    tts: "voice",
    realtime: "speech-to-speech",
};

export function UnlockedModels({
    provider,
    component,
}: {
    provider: string;
    component: string;
}) {
    const [data, setData] = useState<Unlocked | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        const result = await client.get({
            url: "/api/v1/provider-keys/models",
            query: { component, provider },
        });
        setLoading(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not list models"));
            return;
        }
        setData(result.data as Unlocked);
        setError(null);
    }, [provider, component]);

    useEffect(() => {
        void load();
    }, [load]);

    if (loading && !data) {
        return (
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />
                Checking what this key can use…
            </p>
        );
    }

    if (error) {
        return (
            <p className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                {error}
            </p>
        );
    }

    if (!data) return null;

    const noun = COMPONENT_NOUN[component] ?? component;

    if (data.models.length === 0) {
        return (
            <p className="text-xs text-muted-foreground">
                {data.provided_by_decibyl > 0
                    ? `Decibyl already provides every ${noun} model this key reaches, so there is nothing extra to add. You do not need this key.`
                    : `This key reaches no ${noun} models we can offer.`}
            </p>
        );
    }

    return (
        <div className="space-y-1">
            <p className="text-xs text-muted-foreground">
                Adds {data.models.length} {noun} model
                {data.models.length === 1 ? "" : "s"} we do not provide
                {data.provided_by_decibyl > 0 &&
                    ` (${data.provided_by_decibyl} more are already managed by Decibyl)`}
                . Pick them on the Models screen.
            </p>
            <div className="flex flex-wrap gap-1">
                {data.models.slice(0, 12).map((model) => (
                    <span
                        key={model.id}
                        className="rounded-full bg-muted px-2 py-0.5 font-mono text-[10px] text-muted-foreground"
                    >
                        {model.id}
                    </span>
                ))}
                {data.models.length > 12 && (
                    <span className="px-1 text-[10px] text-muted-foreground">
                        +{data.models.length - 12} more
                    </span>
                )}
            </div>
            {data.note && (
                <p className="text-[11px] text-amber-600 dark:text-amber-400">
                    {data.note}
                </p>
            )}
        </div>
    );
}
