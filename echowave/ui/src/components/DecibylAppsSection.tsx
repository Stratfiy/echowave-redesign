"use client";

/**
 * Settings → Decibyl in your apps (DCH-1, KAN-277).
 *
 * Talk to Decibyl from WhatsApp, Telegram, Slack or Teams as yourself. Each
 * app is linked to the signed-in member with a one-time code sent from that
 * app, so Decibyl knows who is asking and acts with their permissions. Slack
 * is also installed once per company, by an admin, with "Add to Slack".
 *
 * Draws nothing while the feature is off for this workspace; the server says.
 */

import { Check, Copy, ExternalLink } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";

type Channel = { channel: string; available: boolean };
type Linked = { id: number; channel: string; display_name: string; handle: string };
type State = { enabled: boolean; channels: Channel[]; linked: Linked[] };
type Pending = {
    channel: string;
    code: string;
    link: string | null;
    instructions: string;
    expires_in_seconds: number;
};

const NAMES: Record<string, string> = {
    whatsapp: "WhatsApp",
    telegram: "Telegram",
    slack: "Slack",
    teams: "Microsoft Teams",
};

export function DecibylAppsSection() {
    const params = useSearchParams();
    const [state, setState] = useState<State | null>(null);
    const [pending, setPending] = useState<Pending | null>(null);
    const [error, setError] = useState<string | null>(
        params?.get("slack_error") ?? null,
    );
    const [notice] = useState<string | null>(
        params?.get("slack_installed")
            ? `Decibyl is added to ${params.get("slack_installed")}. Now connect yourself below.`
            : null,
    );
    const [copied, setCopied] = useState(false);
    const [busy, setBusy] = useState(false);

    const load = useCallback(async () => {
        const response = await client.get({ url: "/api/v1/channel-links" });
        if (response.data) setState(response.data as unknown as State);
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    if (!state || !state.enabled) return null;

    const connect = async (channel: string) => {
        setBusy(true);
        setError(null);
        setCopied(false);
        const response = await client.post({
            url: "/api/v1/channel-links/start",
            body: { channel },
        });
        setBusy(false);
        if (response.error) {
            setError(detailFromResult(response, "Could not start linking."));
            return;
        }
        setPending({ channel, ...(response.data as unknown as Omit<Pending, "channel">) });
    };

    const addToSlack = async () => {
        setError(null);
        const response = await client.get({ url: "/api/v1/channel-links/slack/install" });
        if (response.error) {
            setError(detailFromResult(response, "Could not open Slack."));
            return;
        }
        const url = (response.data as unknown as { url: string }).url;
        window.location.assign(url);
    };

    const unlink = async (id: number) => {
        setBusy(true);
        const response = await client.delete({ url: `/api/v1/channel-links/${id}` });
        setBusy(false);
        if (response.error) {
            setError(detailFromResult(response, "Could not unlink."));
            return;
        }
        await load();
    };

    const copy = async (code: string) => {
        try {
            await navigator.clipboard.writeText(code);
            setCopied(true);
        } catch {
            setCopied(false);
        }
    };

    const available = state.channels.filter((c) => c.available);

    return (
        <div className="space-y-4">
            {error && (
                <div
                    role="alert"
                    className="rounded-[var(--radius-control)] border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
                >
                    {error}
                </div>
            )}
            {notice && (
                <p className="text-sm" aria-live="polite">
                    {notice}
                </p>
            )}

            {state.linked.length > 0 && (
                <ul className="space-y-2">
                    {state.linked.map((item) => (
                        <li
                            key={item.id}
                            className="flex items-center justify-between gap-3 text-sm"
                        >
                            <span className="min-w-0">
                                <span className="font-medium">
                                    {NAMES[item.channel] ?? item.channel}
                                </span>{" "}
                                <span className="text-muted-foreground">
                                    {item.display_name || `…${item.handle}`}
                                </span>
                            </span>
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => void unlink(item.id)}
                                disabled={busy}
                            >
                                Unlink
                            </Button>
                        </li>
                    ))}
                </ul>
            )}

            {available.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                    No apps are set up on this workspace yet.
                </p>
            ) : (
                <div className="flex flex-wrap gap-2">
                    {available.map((c) => (
                        <Button
                            key={c.channel}
                            variant="outline"
                            size="sm"
                            onClick={() => void connect(c.channel)}
                            disabled={busy}
                        >
                            Connect {NAMES[c.channel] ?? c.channel}
                        </Button>
                    ))}
                    {available.some((c) => c.channel === "slack") && (
                        <Button variant="ghost" size="sm" onClick={() => void addToSlack()}>
                            Add Decibyl to your Slack
                        </Button>
                    )}
                </div>
            )}

            {pending && (
                <div
                    className="space-y-2 rounded-[var(--radius-control)] border border-border p-3 text-sm"
                    aria-live="polite"
                >
                    <p>{pending.instructions}</p>
                    <div className="flex flex-wrap items-center gap-2">
                        <code className="rounded bg-muted px-2 py-1 font-mono text-base tracking-widest">
                            {pending.code}
                        </code>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => void copy(pending.code)}
                        >
                            {copied ? (
                                <Check className="h-4 w-4" aria-hidden />
                            ) : (
                                <Copy className="h-4 w-4" aria-hidden />
                            )}
                            {copied ? "Copied" : "Copy"}
                        </Button>
                        {pending.link && (
                            <Button asChild size="sm">
                                <a href={pending.link} target="_blank" rel="noreferrer">
                                    Open {NAMES[pending.channel] ?? pending.channel}
                                    <ExternalLink className="h-4 w-4" aria-hidden />
                                </a>
                            </Button>
                        )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                        The code works once and expires in{" "}
                        {Math.round(pending.expires_in_seconds / 60)} minutes.
                    </p>
                </div>
            )}
        </div>
    );
}
