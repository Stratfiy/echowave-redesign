"use client";

/**
 * Home is a conversation with Decibyl.
 *
 * The way a Slack workspace opens on Slackbot: a hello, the thread, and a
 * composer. Decibyl is the workspace's own assistant -- it knows the team's
 * numbers, the company's documents, what the business has confirmed, and
 * what every bot did lately (see services/workflow/decibyl.py). The two
 * openers are the questions an owner arrives with, sent as messages so the
 * answer comes from the same brain as everything else typed here.
 *
 * The chips under the hello are built from the account's own state: a
 * failing connector, a paused bot, a missed call to return. A link chip
 * opens the screen that fixes it; a prompt chip opens the shelf.
 */

import { AlertTriangle, ArrowRight } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { postMessageApiV1TimelineMessagePost, teamHomeApiV1TeamHomeGet } from "@/client/sdk.gen";
import type { Headline, Suggestion } from "@/client/types.gen";
import { ChannelComposer } from "@/components/channel/ChannelComposer";
import { ChannelStream } from "@/components/channel/ChannelStream";
import { useAuth } from "@/lib/auth";

export const OPENERS = ["What happened this week?", "What needs my attention today?"] as const;

/** Built from the reader's own clock. The server's is in a data centre, and
 *  half the accounts would be wished good morning at nine in the evening. */
function partOfDay(now: Date): string {
    const hour = now.getHours();
    if (hour < 12) return "Good morning";
    if (hour < 17) return "Good afternoon";
    return "Good evening";
}

/** What the team did, as a sentence rather than a row of tiles. */
function summarise(headline: Headline): string {
    if (headline.agents === 0) return "Let's put your first agent to work.";
    const parts: string[] = [];
    if (headline.calls > 0) {
        parts.push(`${headline.calls} ${headline.calls === 1 ? "call" : "calls"} today`);
        if (headline.answered > 0) parts.push(`${headline.answered} answered`);
        if (headline.outcomes > 0) parts.push(`${headline.outcomes} finished`);
    }
    if (parts.length === 0) {
        return headline.live === 0
            ? "No agent is taking calls right now."
            : "Nothing has come in yet today.";
    }
    const sentence = `${parts.join(", ")}.`;
    if (headline.needs_attention > 0) {
        return `${sentence} ${headline.needs_attention} ${
            headline.needs_attention === 1 ? "agent needs" : "agents need"
        } you.`;
    }
    return sentence;
}

function Chip({ chip }: { chip: Suggestion }) {
    const className =
        "inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";
    if (chip.action === "link" && chip.href) {
        return (
            <Link href={chip.href} className={className}>
                <AlertTriangle className="h-3 w-3" />
                {chip.text}
                <ArrowRight className="h-3 w-3" />
            </Link>
        );
    }
    return (
        <Link href="/marketplace" className={className}>
            {chip.text}
            <ArrowRight className="h-3 w-3" />
        </Link>
    );
}

export function HomeAboveTheFold({ firstName }: { firstName?: string }) {
    const { user, loading: authLoading } = useAuth();
    const [headline, setHeadline] = useState<Headline | null>(null);
    const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
    const [waitingFor, setWaitingFor] = useState<{ since: string; bots: number[] } | null>(null);
    const [sendingOpener, setSendingOpener] = useState<string | null>(null);
    const refreshStream = useRef<() => void>(() => {});
    const registerRefresh = useCallback((refresh: () => void) => {
        refreshStream.current = refresh;
    }, []);

    const greeting = useMemo(() => partOfDay(new Date()), []);

    useEffect(() => {
        if (authLoading || !user) return;
        let cancelled = false;
        (async () => {
            try {
                const response = await teamHomeApiV1TeamHomeGet({ query: { hours: 24 } });
                if (cancelled || response.error || !response.data) return;
                setHeadline(response.data.headline ?? null);
                setSuggestions(response.data.suggestions ?? []);
            } catch {
                // The thread below still works. A greeting that failed to
                // load is a missing sentence, not a broken screen.
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [authLoading, user]);

    // Decibyl has no workflow id; the thinking row is keyed on 0 and the
    // stream, in assistant mode, clears it on any reply after `since`.
    const asked = () => setWaitingFor({ since: new Date().toISOString(), bots: [0] });

    const sendOpener = async (text: string) => {
        setSendingOpener(text);
        const response = await postMessageApiV1TimelineMessagePost({ body: { assistant: true, text } });
        setSendingOpener(null);
        if (response.error) return;
        asked();
        refreshStream.current();
    };

    return (
        <div className="flex h-[calc(100vh-9rem)] min-h-[28rem] flex-col gap-4">
            <div className="flex items-start gap-3">
                <div
                    aria-hidden="true"
                    className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-rail text-lg font-semibold text-rail-foreground"
                >
                    d
                </div>
                <div className="min-w-0">
                    <h2 className="text-xl font-semibold tracking-tight">Hi, I&apos;m Decibyl.</h2>
                    <p className="mt-0.5 text-sm text-muted-foreground">
                        {greeting}
                        {firstName ? `, ${firstName}` : ""}. {headline ? summarise(headline) : ""}
                    </p>
                </div>
            </div>

            {suggestions.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                    {suggestions.map((chip) => (
                        <Chip key={`${chip.kind}-${chip.text}`} chip={chip} />
                    ))}
                </div>
            ) : null}

            <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card">
                <ChannelStream
                    assistant
                    assistantName="Decibyl"
                    botNames={{}}
                    onRegisterRefresh={registerRefresh}
                    waitingFor={waitingFor}
                />
                {/* The two questions an owner opens the app with, sent as
                    messages so they are answered from the same readings as
                    anything typed. */}
                <div className="flex flex-wrap gap-2 border-t border-border px-4 py-2" aria-label="Ask Decibyl">
                    {OPENERS.map((text) => (
                        <button
                            key={text}
                            type="button"
                            disabled={sendingOpener !== null}
                            onClick={() => void sendOpener(text)}
                            className="rounded-full border border-border bg-muted/30 px-3 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-60"
                        >
                            {sendingOpener === text ? "Asking…" : text}
                        </button>
                    ))}
                </div>
                <ChannelComposer
                    assistant
                    bots={[]}
                    channelName="Decibyl"
                    onSent={() => {
                        asked();
                        refreshStream.current();
                    }}
                />
            </div>
        </div>
    );
}
