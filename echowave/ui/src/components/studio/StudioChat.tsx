"use client";

/**
 * The Studio chat: one conversation that makes agents and a website.
 *
 * Like the agent builder's panel, the server keeps no session: `history` is
 * the model's transcript, sent back with every message, and `turns` is what a
 * person reads. Unlike the builder, both are kept in this tab's
 * sessionStorage, so a reload in the middle of building a site does not lose
 * the conversation (the 7 September audit's finding 4, for this screen).
 * Storage can be refused -- a private window, blocked site data -- and the
 * chat then simply starts empty.
 */

import { Bot, Hammer, Link2, Loader2, Send, Wand2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import { studioApi,type StudioChatResponse, type StudioUsage } from "./api";

export interface StudioTurn {
    role: "user" | "assistant";
    text: string;
    /** Agents this turn created, linked under the reply. */
    agents?: number[];
    /** What the turn did, in words: "Built the site", "Wrote 4 files". */
    did?: string[];
    /** Apps to connect, opened from here rather than another screen. */
    links?: { app: string; url: string }[];
}

const STORAGE_KEY = "decibyl.studio.chat.v1";

const OPENERS = [
    "Build a website for my dental clinic with a receptionist agent that books appointments",
    "Make a landing page for my bakery and an agent that takes cake orders",
    "Create a sales agent and a support agent for my coaching institute, and a site for both",
];

const ACTION_WORDS: Record<string, string> = {
    create_site: "Started a site",
    write_site_files: "Wrote files",
    build_site: "Built the site",
    put_agents_on_site: "Put agents on the site",
    apply_design_theme: "Changed the theme",
    find_images: "Found photos",
    review_site_design: "Checked the design",
    connect_form_to_agent: "Connected the form",
    create_agent: "Made an agent",
    attach_app_tool: "Gave an agent an app action",
    set_voice_and_brain: "Set a voice",
    revise_agent_prompt: "Reworded an agent",
    revise_agent_facts: "Updated an agent's facts",
};

/** The actions a turn ran, as a few distinct phrases, in order. */
export function describeActions(actions: string[]): string[] {
    const out: string[] = [];
    for (const action of actions) {
        const words = ACTION_WORDS[action];
        if (words && !out.includes(words)) out.push(words);
    }
    return out;
}

interface Saved {
    turns: StudioTurn[];
    history: Record<string, unknown>[];
}

function load(): Saved {
    try {
        const raw = window.sessionStorage.getItem(STORAGE_KEY);
        if (!raw) return { turns: [], history: [] };
        const parsed = JSON.parse(raw) as Saved;
        if (!Array.isArray(parsed.turns) || !Array.isArray(parsed.history)) {
            return { turns: [], history: [] };
        }
        return parsed;
    } catch {
        return { turns: [], history: [] };
    }
}

function save(value: Saved) {
    try {
        window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    } catch {
        // Storage refused or full: the conversation still works, it just
        // does not survive a reload.
    }
}

export function StudioChat({
    usage,
    onTurn,
}: {
    usage: StudioUsage | null;
    /** Called after every reply, so the site panel can follow along. */
    onTurn: (response: StudioChatResponse) => void;
}) {
    const [turns, setTurns] = useState<StudioTurn[]>([]);
    const [history, setHistory] = useState<Record<string, unknown>[]>([]);
    const [draft, setDraft] = useState("");
    const [sending, setSending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [restored, setRestored] = useState(false);
    const scrollRef = useRef<HTMLDivElement>(null);

    // Read storage after mount, never during render: the server render has
    // no window, and reading it there would mismatch on hydration.
    useEffect(() => {
        const saved = load();
        setTurns(saved.turns);
        setHistory(saved.history);
        setRestored(true);
    }, []);

    useEffect(() => {
        if (restored) save({ turns, history });
    }, [turns, history, restored]);

    useEffect(() => {
        const thread = scrollRef.current;
        if (thread) thread.scrollTop = thread.scrollHeight;
    }, [turns, sending]);

    const send = useCallback(
        async (text: string) => {
            const message = text.trim();
            if (!message || sending) return;
            setError(null);
            setSending(true);
            setTurns((previous) => [...previous, { role: "user", text: message }]);
            setDraft("");

            const result = await studioApi.chat(message, history);
            setSending(false);
            if (result.error !== undefined) {
                // Take the unsent message back out of the thread and put it
                // back in the box, so trying again is one click.
                setTurns((previous) => previous.slice(0, -1));
                setDraft(message);
                setError(result.error);
                return;
            }
            const data = result.data;
            setHistory(data.history ?? []);
            setTurns((previous) => [
                ...previous,
                {
                    role: "assistant",
                    text: data.reply,
                    agents: data.created_workflow_ids ?? [],
                    did: describeActions(data.actions ?? []),
                    links: data.connect_links ?? [],
                },
            ]);
            onTurn(data);
        },
        [history, sending, onTurn],
    );

    const startOver = () => {
        setTurns([]);
        setHistory([]);
        setError(null);
    };

    return (
        <div className="flex min-h-[420px] flex-col rounded-xl border border-border bg-card lg:h-[calc(100vh-11rem)]">
            <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
                <div className="flex items-center gap-2 text-sm font-medium">
                    <Wand2 className="h-4 w-4 text-primary" />
                    Studio chat
                </div>
                <div className="flex items-center gap-2">
                    {usage && usage.limit > 0 ? (
                        <span className="rounded-full border border-border bg-muted/40 px-2.5 py-0.5 text-xs tabular-nums text-muted-foreground">
                            {usage.past_allowance
                                ? `${usage.per_message_credits ?? 5} credits a message`
                                : `${usage.remaining} / ${usage.limit} left`}
                        </span>
                    ) : null}
                    {turns.length > 0 ? (
                        <Button variant="ghost" size="sm" onClick={startOver} disabled={sending}>
                            Start over
                        </Button>
                    ) : null}
                </div>
            </div>

            <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
                {turns.length === 0 ? (
                    <div className="space-y-3">
                        <p className="text-sm text-muted-foreground">
                            Describe your business. Studio makes the agents that talk to
                            your customers and a website for them, builds it, and shows
                            you the preview.
                        </p>
                        <div className="flex flex-col gap-2">
                            {OPENERS.map((opener) => (
                                <button
                                    key={opener}
                                    type="button"
                                    // Fills the box; never sends. The person
                                    // presses Send, every time.
                                    onClick={() => setDraft(opener)}
                                    className="rounded-lg border border-border px-3 py-2 text-left text-sm hover:bg-muted"
                                >
                                    {opener}
                                </button>
                            ))}
                        </div>
                    </div>
                ) : null}

                {turns.map((turn, index) => (
                    <div
                        key={index}
                        className={cn(
                            "flex",
                            turn.role === "user" ? "justify-end" : "justify-start",
                        )}
                    >
                        <div
                            className={cn(
                                "max-w-[90%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm",
                                turn.role === "user"
                                    ? "bg-primary text-primary-foreground"
                                    : "bg-muted text-foreground",
                            )}
                        >
                            {turn.text}
                            {turn.did && turn.did.length > 0 ? (
                                <div className="mt-2 flex flex-wrap gap-1.5">
                                    {turn.did.map((words) => (
                                        <span
                                            key={words}
                                            className="inline-flex items-center gap-1 rounded-full bg-background/70 px-2 py-0.5 text-xs text-muted-foreground"
                                        >
                                            <Hammer className="h-3 w-3" />
                                            {words}
                                        </span>
                                    ))}
                                </div>
                            ) : null}
                            {turn.links && turn.links.length > 0 ? (
                                <div className="mt-2 flex flex-wrap gap-1.5">
                                    {turn.links.map((link) => (
                                        <a
                                            key={link.url}
                                            href={link.url}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="inline-flex items-center gap-1 rounded-full bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:opacity-90"
                                        >
                                            <Link2 className="h-3 w-3" />
                                            Connect {link.app}
                                        </a>
                                    ))}
                                </div>
                            ) : null}
                            {turn.agents && turn.agents.length > 0 ? (
                                <div className="mt-2 flex flex-wrap gap-1.5">
                                    {turn.agents.map((id) => (
                                        <Link
                                            key={id}
                                            href={`/workflow/${id}`}
                                            className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2 py-0.5 text-xs underline-offset-2 hover:underline"
                                        >
                                            <Bot className="h-3 w-3" />
                                            Open agent {id}
                                        </Link>
                                    ))}
                                </div>
                            ) : null}
                        </div>
                    </div>
                ))}

                {sending ? (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Working — writing and building can take a minute…
                    </div>
                ) : null}
            </div>

            {error ? (
                <div
                    role="alert"
                    className="mx-4 mb-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                >
                    {error}
                </div>
            ) : null}

            <form
                className="flex items-end gap-2 border-t border-border p-3"
                onSubmit={(event) => {
                    event.preventDefault();
                    void send(draft);
                }}
            >
                <Textarea
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey) {
                            event.preventDefault();
                            void send(draft);
                        }
                    }}
                    placeholder="Describe what to build or change…"
                    aria-label="Message Studio"
                    rows={2}
                    maxLength={8000}
                    className="min-h-[44px] resize-none"
                    disabled={sending}
                />
                <Button
                    type="submit"
                    size="icon"
                    disabled={sending || !draft.trim()}
                    aria-label="Send"
                >
                    {sending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                        <Send className="h-4 w-4" />
                    )}
                </Button>
            </form>
        </div>
    );
}
