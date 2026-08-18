"use client";

/**
 * The chat on the overview screen that builds an agent.
 *
 * The server keeps no session, so the whole transcript lives here and is sent
 * back with every message. That is what makes a reload lose the conversation
 * and nothing else — no half-built agent stranded in a table somewhere.
 *
 * Two pieces of state look redundant and are not. `history` is the model's
 * transcript, including tool calls and their results, and is opaque to this
 * component. `turns` is what a person reads. Rendering the first would show
 * someone the machinery; sending the second would lose the model its memory.
 */

import { Loader2, Send, Sparkles } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

interface Turn {
    role: "user" | "assistant";
    text: string;
    /** Set on the turn that created an agent, so the link appears with it. */
    workflowId?: number | null;
}

interface Usage {
    used: number;
    limit: number;
    remaining: number;
}

interface ChatResponse {
    reply: string;
    history: Record<string, unknown>[];
    created_workflow_id: number | null;
    usage: Usage;
}

interface BuilderConfig {
    available: boolean;
    provider: string | null;
    unavailable_reason: string | null;
    usage: Usage & { resets_at: string };
}

/** Openers that show what the thing does better than a paragraph would. */
const SUGGESTIONS = [
    "Answer my clinic's phone and book appointments",
    "Call my property leads and qualify them",
    "Confirm COD orders before we ship",
];

export function AgentBuilderPanel() {
    const { user, loading: authLoading } = useAuth();
    const [config, setConfig] = useState<BuilderConfig | null>(null);
    const [turns, setTurns] = useState<Turn[]>([]);
    const [history, setHistory] = useState<Record<string, unknown>[]>([]);
    const [draft, setDraft] = useState("");
    const [sending, setSending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const scrollRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        // The auth interceptor only attaches the bearer token once auth has
        // finished loading; fetching before that sends an unauthenticated
        // request that fails silently.
        if (authLoading || !user) return;

        let cancelled = false;
        (async () => {
            try {
                const response = await client.get({
                    url: "/api/v1/agent-builder/config",
                });
                // The generated client types responses as a status->shape map,
                // so a plain generic here widens into a union. These endpoints
                // are not in the generated SDK yet; assert the shape instead.
                const data = response.data as BuilderConfig | undefined;
                // The client resolves rather than throws on a 4xx/5xx, so the
                // error has to be checked explicitly — a catch alone only sees
                // network failures.
                if (!cancelled && !response.error && data) {
                    setConfig(data);
                    return;
                }
                if (!cancelled) {
                    setConfig({
                        available: false,
                        provider: null,
                        unavailable_reason: null,
                        usage: { used: 0, limit: 0, remaining: 0, resets_at: "" },
                    });
                }
            } catch {
                // A panel that cannot read its own config renders as
                // unavailable rather than throwing the page away.
                if (!cancelled) {
                    setConfig({
                        available: false,
                        provider: null,
                        unavailable_reason: null,
                        usage: { used: 0, limit: 0, remaining: 0, resets_at: "" },
                    });
                }
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [authLoading, user]);

    // Keep the newest turn in view as the conversation grows.
    useEffect(() => {
        scrollRef.current?.scrollTo({
            top: scrollRef.current.scrollHeight,
            behavior: "smooth",
        });
    }, [turns, sending]);

    const send = useCallback(
        async (text: string) => {
            const message = text.trim();
            if (!message || sending) return;

            setError(null);
            setDraft("");
            setTurns((previous) => [...previous, { role: "user", text: message }]);
            setSending(true);

            try {
                const response = await client.post({
                    url: "/api/v1/agent-builder/chat",
                    body: { message, history },
                });

                if (response.error) {
                    // 429 when the day's allowance is gone, 503 when no
                    // provider key is installed, 502 when the vendor failed.
                    // All of them carry a message written for the reader.
                    setError(
                        detailFromError(
                            response.error,
                            "The assistant could not reply.",
                        ),
                    );
                    return;
                }

                const data = response.data as ChatResponse | undefined;
                if (!data) throw new Error("Empty response");

                setHistory(data.history ?? []);
                setTurns((previous) => [
                    ...previous,
                    {
                        role: "assistant",
                        text: data.reply,
                        workflowId: data.created_workflow_id,
                    },
                ]);
                setConfig((previous) =>
                    previous
                        ? { ...previous, usage: { ...previous.usage, ...data.usage } }
                        : previous,
                );
            } catch (err) {
                setError(detailFromError(err, "The assistant could not reply."));
            } finally {
                setSending(false);
            }
        },
        [history, sending],
    );

    if (!config) {
        return (
            <Card className="mb-8">
                <CardContent className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Loading the agent builder…
                </CardContent>
            </Card>
        );
    }

    if (!config.available) {
        return (
            <Card className="mb-8">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-xl">
                        <Sparkles className="h-5 w-5 text-muted-foreground" />
                        Build an agent by chatting
                    </CardTitle>
                    <CardDescription>
                        {config.unavailable_reason ??
                            "The agent builder is not switched on for this deployment."}
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <Button asChild variant="outline">
                        <Link href="/workflow">Use the editor instead</Link>
                    </Button>
                </CardContent>
            </Card>
        );
    }

    const outOfMessages = config.usage.limit > 0 && config.usage.remaining <= 0;
    const started = turns.length > 0;

    return (
        <Card className="mb-8">
            <CardHeader>
                <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                        <CardTitle className="flex items-center gap-2 text-xl">
                            <Sparkles className="h-5 w-5 text-primary" />
                            Build an agent by chatting
                        </CardTitle>
                        <CardDescription className="mt-1">
                            Describe your business and I&apos;ll ask a few questions,
                            then build a working agent — with what it costs a minute.
                        </CardDescription>
                    </div>
                    {config.usage.limit > 0 ? (
                        <span
                            className="shrink-0 rounded-full border border-border bg-muted/40 px-2.5 py-1 text-xs tabular-nums text-muted-foreground"
                            title="Messages left today"
                        >
                            {config.usage.remaining} / {config.usage.limit} left today
                        </span>
                    ) : null}
                </div>
            </CardHeader>

            <CardContent>
                {started ? (
                    <div
                        ref={scrollRef}
                        className="mb-3 max-h-96 space-y-3 overflow-y-auto pr-1"
                    >
                        {turns.map((turn, index) => (
                            <div
                                key={index}
                                className={
                                    turn.role === "user"
                                        ? "flex justify-end"
                                        : "flex justify-start"
                                }
                            >
                                <div
                                    className={
                                        turn.role === "user"
                                            ? "max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
                                            : "max-w-[85%] rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm"
                                    }
                                >
                                    <p className="whitespace-pre-wrap">{turn.text}</p>
                                    {turn.workflowId ? (
                                        <Button
                                            asChild
                                            size="sm"
                                            className="mt-2"
                                            data-testid="agent-builder-open-agent"
                                        >
                                            <Link href={`/workflow/${turn.workflowId}`}>
                                                Open the agent
                                            </Link>
                                        </Button>
                                    ) : null}
                                </div>
                            </div>
                        ))}
                        {sending ? (
                            <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                Thinking…
                            </div>
                        ) : null}
                    </div>
                ) : (
                    <div className="mb-3 flex flex-wrap gap-2">
                        {SUGGESTIONS.map((suggestion) => (
                            <button
                                key={suggestion}
                                type="button"
                                onClick={() => void send(suggestion)}
                                disabled={outOfMessages}
                                className="rounded-full border border-border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                            >
                                {suggestion}
                            </button>
                        ))}
                    </div>
                )}

                {error ? (
                    <p
                        role="alert"
                        className="mb-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                    >
                        {error}
                    </p>
                ) : null}

                <div className="relative">
                    <Textarea
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        onKeyDown={(event) => {
                            // Enter sends; Shift+Enter is a newline, the
                            // convention the workflow tester already uses.
                            if (event.key === "Enter" && !event.shiftKey) {
                                event.preventDefault();
                                void send(draft);
                            }
                        }}
                        disabled={sending || outOfMessages}
                        rows={2}
                        aria-label="Describe the agent you want"
                        placeholder={
                            outOfMessages
                                ? "You've used today's messages. The allowance resets at midnight."
                                : "e.g. I run a dental clinic in Bengaluru and want the phone answered"
                        }
                        className="pr-12 resize-none"
                        data-testid="agent-builder-input"
                    />
                    <Button
                        type="button"
                        size="icon"
                        onClick={() => void send(draft)}
                        disabled={sending || outOfMessages || !draft.trim()}
                        className="absolute bottom-2 right-2 h-8 w-8"
                        aria-label="Send"
                        data-testid="agent-builder-send"
                    >
                        {sending ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                            <Send className="h-4 w-4" />
                        )}
                    </Button>
                </div>
            </CardContent>
        </Card>
    );
}
