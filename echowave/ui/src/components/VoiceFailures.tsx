"use client";

/**
 * Voices that went quiet on real calls in the last day.
 *
 * The third check on this screen, and the only one that would have caught
 * 23 Sept 2026: ElevenLabs refused every request from our key while the key
 * still passed its check and the account still showed credit. Calls saw it --
 * text went in, no sound came out -- so calls now report it
 * (api/services/pipecat/voice_watch.py) and this shows what they reported.
 *
 * Renders nothing when there is nothing to say: a green "all quiet" row on a
 * screen full of rows is one more thing to read past.
 */

import { AlertTriangle } from "lucide-react";
import { useEffect, useState } from "react";

import { readVoiceFailuresApiV1AdminProviderKeysVoiceFailuresGet } from "@/client/sdk.gen";
import { providerLabel } from "@/components/providerCards";
import { useFeature } from "@/lib/features";

export type VoiceFailure = {
    provider: string;
    count: number;
    last_at: string;
    last_run_id: number | null;
    last_model: string | null;
};

/** One sentence per voice: what, how often, when last, and on which call. */
export function describeFailure(failure: VoiceFailure, now: Date = new Date()): string {
    const calls = failure.count === 1 ? "1 call" : `${failure.count} calls`;
    const voice = failure.last_model
        ? `${providerLabel(failure.provider)} (${failure.last_model})`
        : providerLabel(failure.provider);
    const minutes = Math.max(0, Math.round((now.getTime() - new Date(failure.last_at).getTime()) / 60_000));
    const ago = minutes < 1 ? "just now" : minutes < 60 ? `${minutes} min ago` : `${Math.round(minutes / 60)} h ago`;
    const run = failure.last_run_id ? `, run ${failure.last_run_id}` : "";
    return `${voice}: ${calls} got no audio back in the last 24 hours (last ${ago}${run}).`;
}

export function VoiceFailures() {
    const on = useFeature("voice_watch");
    const [failures, setFailures] = useState<VoiceFailure[]>([]);

    useEffect(() => {
        if (!on) return;
        let live = true;
        void readVoiceFailuresApiV1AdminProviderKeysVoiceFailuresGet().then((result) => {
            if (!live || result.error) return;
            setFailures(((result.data as { failures?: VoiceFailure[] } | undefined)?.failures ?? []) as VoiceFailure[]);
        });
        return () => {
            live = false;
        };
    }, [on]);

    if (!on || failures.length === 0) return null;

    return (
        <section role="alert" className="mb-5 rounded-lg border border-destructive/40 bg-destructive/10 px-5 py-4 text-sm text-destructive">
            <h2 className="flex items-center gap-2 font-semibold">
                <AlertTriangle className="h-4 w-4" aria-hidden />
                A voice provider is not answering on calls
            </h2>
            <ul className="mt-2 space-y-1">
                {failures.map((failure) => (
                    <li key={failure.provider}>{describeFailure(failure)}</li>
                ))}
            </ul>
            <p className="mt-2 text-destructive/90">
                Its key can still pass the check above and the account can still show credit. Look at the provider&apos;s own
                request log, and the key&apos;s credit limit and permissions.
            </p>
        </section>
    );
}
