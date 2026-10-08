"use client";

import { Loader2, Lock } from "lucide-react";
import { useState } from "react";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { detailFromResult } from "@/lib/apiError";

export type ContentAccess = {
    state: "granted" | "consent_required";
    detail: string | null;
    grant: { id: number; scope: string; expires_at: string; reason: string | null } | null;
};

type Turn = { role: "caller" | "agent"; text: string; at: string | null };

/**
 * A call's transcript and recording for staff (phase 3): open only while the
 * workspace allows it (an owner or admin grants it from the call's page),
 * and every read is logged against the reader. Without consent the card
 * says so and offers nothing to click.
 */
export function CallContent({ runId, access, recordingUrl, hasRecording }: { runId: number; access: ContentAccess | undefined; recordingUrl: string | null; hasRecording: boolean }) {
    const [turns, setTurns] = useState<Turn[] | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = async () => {
        setLoading(true);
        setError(null);
        const result = await client.get({ url: `/api/v1/admin/billing/calls/${runId}/transcript` });
        if (result.error) {
            setError(detailFromResult(result, "Could not read the transcript."));
        } else {
            setTurns(((result.data as { turns?: Turn[] })?.turns ?? []) as Turn[]);
        }
        setLoading(false);
    };

    return (
        <Card data-testid="call-content">
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">Transcript and recording</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
                {access?.state !== "granted" ? (
                    <p className="flex items-start gap-2 text-muted-foreground" data-testid="consent-required">
                        <Lock aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                        <span>
                            {access?.detail ?? "The workspace has not allowed Decibyl staff to read this call."}
                            {hasRecording ? " A recording exists." : ""}
                        </span>
                    </p>
                ) : (
                    <>
                        <p className="text-xs text-muted-foreground">
                            Allowed by the workspace ({access.grant?.scope === "all_calls" ? "every call" : "this call"}) until{" "}
                            {access.grant ? new Date(access.grant.expires_at).toLocaleString() : "?"}
                            {access.grant?.reason ? `: “${access.grant.reason}”` : ""}. Your reading is logged.
                        </p>
                        {recordingUrl ? (
                            <audio controls src={recordingUrl} className="w-full">
                                Your browser does not support audio playback.
                            </audio>
                        ) : (
                            <p className="text-muted-foreground">No recording for this call.</p>
                        )}
                        {turns === null ? (
                            <Button variant="outline" className="min-h-11 md:min-h-9" onClick={() => void load()} disabled={loading}>
                                {loading && <Loader2 aria-hidden className="h-4 w-4 animate-spin" />} Read the transcript
                            </Button>
                        ) : turns.length === 0 ? (
                            <p className="text-muted-foreground">No words were transcribed on this call.</p>
                        ) : (
                            <ol className="space-y-2" data-testid="transcript">
                                {turns.map((t, i) => (
                                    <li key={i} className={t.role === "agent" ? "rounded-md bg-muted p-2" : "rounded-md border border-border p-2"}>
                                        <span className="block text-xs text-muted-foreground">
                                            {t.role === "agent" ? "Agent" : "Caller"}
                                            {t.at ? ` · ${t.at}` : ""}
                                        </span>
                                        <span className="break-words">{t.text}</span>
                                    </li>
                                ))}
                            </ol>
                        )}
                    </>
                )}
                {error && (
                    <p role="alert" className="text-[#772322] dark:text-red-300">
                        {error}
                    </p>
                )}
            </CardContent>
        </Card>
    );
}
