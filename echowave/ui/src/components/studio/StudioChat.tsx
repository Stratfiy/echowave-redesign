"use client";

/**
 * The Studio chat: one conversation that makes agents and a website.
 *
 * Two shapes. Before the first message it is a page with one question on
 * it -- "What do you want to build?" -- a large box to answer in, and idea
 * cards underneath, the way Grok opens. After it, a thread: your messages
 * on the right, Studio's replies as plain reading text with what the turn
 * did listed under them, the agents it made as cards, and a row of
 * next-step chips under the latest reply, so the next move is a tap.
 *
 * Openers and chips fill the box; they never send. Every message is metered,
 * so the person presses Send, every time.
 *
 * Like the agent builder's panel, the server keeps no session: `history` is
 * the model's transcript, sent back with every message, and `turns` is what a
 * person reads. Both are kept in this tab's sessionStorage, so a reload in
 * the middle of building a site does not lose the conversation. Storage can
 * be refused -- a private window, blocked site data -- and the chat then
 * simply starts empty.
 */

import {
    ArrowUp,
    Bot,
    Check,
    Copy,
    Globe,
    Link2,
    Loader2,
    MessageSquareText,
    RotateCcw,
    ShoppingBag,
    Sparkles,
    Stethoscope,
    Users,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

import {
    type StudioAgent,
    studioApi,
    type StudioChatResponse,
    type StudioUsage,
} from "./api";
import { RichReply } from "./RichReply";

export interface StudioTurn {
    role: "user" | "assistant";
    text: string;
    /** Agents this turn created, by id (kept for chats saved before names). */
    agents?: number[];
    /** The same agents by name. */
    agentCards?: StudioAgent[];
    /** What the turn did, in words: "Built the site", "Wrote files". */
    did?: string[];
    /** Apps to connect, opened from here rather than another screen. */
    links?: { app: string; url: string }[];
}

const STORAGE_KEY = "decibyl.studio.chat.v1";

interface Idea {
    icon: typeof Globe;
    title: string;
    prompt: string;
}

/** What a first message can look like. Filled into the box, never sent. */
export const IDEAS: Idea[] = [
    {
        icon: Stethoscope,
        title: "Clinic website + receptionist",
        prompt: "Build a website for my dental clinic with a receptionist agent that books appointments",
    },
    {
        icon: ShoppingBag,
        title: "Bakery page + order taker",
        prompt: "Make a landing page for my bakery and an agent that takes cake orders",
    },
    {
        icon: Users,
        title: "Sales + support team",
        prompt: "Create a sales agent and a support agent for my coaching institute, and a site for both",
    },
    {
        icon: MessageSquareText,
        title: "Just an agent",
        prompt: "Make a WhatsApp agent that answers questions about my shop's timings, prices and delivery",
    },
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

/**
 * What to offer next, under the latest reply. Read off what exists, so the
 * chips are about this person's site and agents, not a fixed list.
 */
export function suggestNext({
    hasSite,
    hasAgents,
}: {
    hasSite: boolean;
    hasAgents: boolean;
}): string[] {
    if (!hasSite && !hasAgents) {
        return [
            "Make a website for it too",
            "Add an agent that answers customers on WhatsApp",
        ];
    }
    const out: string[] = [];
    if (hasSite) {
        out.push(
            "Make the design more premium",
            "Add a testimonials section",
            "Check how it looks on a phone",
        );
    }
    if (hasAgents && hasSite) out.push("Connect the contact form to my agent");
    if (hasAgents) out.push("Send me an email when a customer books");
    if (!hasSite) out.push("Make a website for my agents");
    return out.slice(0, 4);
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

function usageLabel(usage: StudioUsage | null): string | null {
    if (!usage || usage.limit <= 0) return null;
    return usage.past_allowance
        ? `${usage.per_message_credits ?? 5} credits a message`
        : `${usage.remaining} of ${usage.limit} messages left`;
}

/** Seconds since ``on`` turned true, ticking once a second. */
function useElapsed(on: boolean): number {
    const [seconds, setSeconds] = useState(0);
    useEffect(() => {
        if (!on) {
            setSeconds(0);
            return;
        }
        const started = Date.now();
        const timer = window.setInterval(
            () => setSeconds(Math.floor((Date.now() - started) / 1000)),
            1000,
        );
        return () => window.clearInterval(timer);
    }, [on]);
    return seconds;
}

function Composer({
    draft,
    setDraft,
    onSend,
    sending,
    usage,
    large,
    inputRef,
}: {
    draft: string;
    setDraft: (value: string) => void;
    onSend: () => void;
    sending: boolean;
    usage: StudioUsage | null;
    large: boolean;
    inputRef: React.RefObject<HTMLTextAreaElement | null>;
}) {
    // Grow with the text, up to a cap, the way a message box should.
    useEffect(() => {
        const box = inputRef.current;
        if (!box) return;
        box.style.height = "auto";
        box.style.height = `${Math.min(box.scrollHeight, large ? 260 : 200)}px`;
    }, [draft, large, inputRef]);

    const label = usageLabel(usage);
    return (
        <form
            className={cn(
                "group relative rounded-3xl border border-border bg-card shadow-sm transition-shadow focus-within:border-primary/40 focus-within:shadow-md",
                large ? "p-3 sm:p-4" : "p-2.5",
            )}
            onSubmit={(event) => {
                event.preventDefault();
                onSend();
            }}
        >
            <textarea
                ref={inputRef}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        onSend();
                    }
                }}
                placeholder={
                    large
                        ? "Describe your business and what you want built…"
                        : "Ask for a change, or something new…"
                }
                aria-label="Message Studio"
                rows={large ? 3 : 1}
                maxLength={8000}
                disabled={sending}
                className={cn(
                    "block w-full resize-none bg-transparent px-2 text-foreground placeholder:text-muted-foreground focus:outline-none disabled:opacity-60",
                    large ? "min-h-[84px] py-1 text-base" : "min-h-[40px] py-2 text-[15px]",
                )}
            />
            <div className="mt-1 flex items-center justify-between gap-2 px-1">
                <span className="truncate text-xs text-muted-foreground">
                    {label ?? (large ? "Enter to send · Shift + Enter for a new line" : "")}
                </span>
                <button
                    type="submit"
                    disabled={sending || !draft.trim()}
                    aria-label="Send"
                    className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-30"
                >
                    {sending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                        <ArrowUp className="h-4 w-4" />
                    )}
                </button>
            </div>
        </form>
    );
}

function CopyButton({ text }: { text: string }) {
    const [copied, setCopied] = useState(false);
    return (
        <button
            type="button"
            aria-label={copied ? "Copied" : "Copy reply"}
            onClick={() => {
                void navigator.clipboard?.writeText(text).then(
                    () => {
                        setCopied(true);
                        window.setTimeout(() => setCopied(false), 1500);
                    },
                    () => undefined,
                );
            }}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
        >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        </button>
    );
}

function AgentCard({ agent }: { agent: StudioAgent }) {
    const initials = agent.name
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((word) => word[0]?.toUpperCase())
        .join("");
    return (
        <Link
            href={`/workflow/${agent.id}`}
            className="group flex min-w-0 items-center gap-3 rounded-2xl border border-border bg-card px-3 py-2.5 transition-colors hover:border-primary/40 hover:bg-muted/40"
        >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                {initials || <Bot className="h-4 w-4" />}
            </span>
            <span className="min-w-0">
                <span className="block truncate text-sm font-medium">{agent.name}</span>
                <span className="block text-xs text-muted-foreground">
                    New agent · open to test it
                </span>
            </span>
        </Link>
    );
}

export function StudioChat({
    usage,
    onTurn,
    onStarted,
    hasSite = false,
}: {
    usage: StudioUsage | null;
    /** Called after every reply, so the site panel can follow along. */
    onTurn: (response: StudioChatResponse) => void;
    /** Whether the conversation has begun, so the screen can lay itself out. */
    onStarted?: (started: boolean) => void;
    /** Whether a site exists, for the next-step chips. */
    hasSite?: boolean;
}) {
    const [turns, setTurns] = useState<StudioTurn[]>([]);
    const [history, setHistory] = useState<Record<string, unknown>[]>([]);
    const [draft, setDraft] = useState("");
    const [sending, setSending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [restored, setRestored] = useState(false);
    const scrollRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const elapsed = useElapsed(sending);

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

    const started = turns.length > 0 || sending;
    useEffect(() => {
        if (restored) onStarted?.(started);
    }, [started, restored, onStarted]);

    useEffect(() => {
        const thread = scrollRef.current;
        if (thread) thread.scrollTop = thread.scrollHeight;
    }, [turns, sending]);

    const fill = (text: string) => {
        setDraft(text);
        inputRef.current?.focus();
    };

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
                    agentCards: data.created_agents ?? [],
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

    const errorBox = error ? (
        <div
            role="alert"
            className="rounded-2xl border border-destructive/40 bg-destructive/10 px-4 py-2.5 text-sm text-destructive"
        >
            {error}
        </div>
    ) : null;

    // --- before the first message: one question and a box --------------------
    if (!started) {
        return (
            <div className="mx-auto flex w-full max-w-3xl flex-col items-center px-1 pb-10 pt-[4vh] sm:pt-[7vh]">
                <span className="mb-5 inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                    <Sparkles className="h-6 w-6" />
                </span>
                <h2 className="text-center text-3xl font-semibold tracking-tight sm:text-4xl">
                    What do you want to build?
                </h2>
                <p className="mt-3 max-w-xl text-center text-[15px] text-muted-foreground">
                    Describe your business in your own words. Studio makes the agents
                    that talk to your customers, a website for them, and shows you the
                    preview.
                </p>
                <div className="mt-8 w-full">
                    <Composer
                        draft={draft}
                        setDraft={setDraft}
                        onSend={() => void send(draft)}
                        sending={sending}
                        usage={usage}
                        large
                        inputRef={inputRef}
                    />
                </div>
                {errorBox ? <div className="mt-3 w-full">{errorBox}</div> : null}
                <div className="mt-6 grid w-full gap-2.5 sm:grid-cols-2">
                    {IDEAS.map((idea) => (
                        <button
                            key={idea.title}
                            type="button"
                            onClick={() => fill(idea.prompt)}
                            className="group flex items-start gap-3 rounded-2xl border border-border bg-card/60 p-3.5 text-left transition-colors hover:border-primary/40 hover:bg-card"
                        >
                            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-muted text-muted-foreground group-hover:bg-primary/10 group-hover:text-primary">
                                <idea.icon className="h-4 w-4" />
                            </span>
                            <span className="min-w-0">
                                <span className="block text-sm font-medium">{idea.title}</span>
                                <span className="mt-0.5 line-clamp-2 block text-xs text-muted-foreground">
                                    {idea.prompt}
                                </span>
                            </span>
                        </button>
                    ))}
                </div>
            </div>
        );
    }

    // --- the thread ----------------------------------------------------------
    const lastAssistant = turns.map((t) => t.role).lastIndexOf("assistant");
    const anyAgents = turns.some(
        (t) => (t.agentCards?.length ?? 0) > 0 || (t.agents?.length ?? 0) > 0,
    );
    const next = suggestNext({ hasSite, hasAgents: anyAgents });

    return (
        <div className="flex h-[70vh] min-h-[480px] flex-col lg:h-full lg:min-h-0">
            <div className="flex items-center justify-between gap-2 pb-2">
                <span className="text-sm font-medium text-muted-foreground">Conversation</span>
                <button
                    type="button"
                    onClick={startOver}
                    disabled={sending}
                    className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                >
                    <RotateCcw className="h-3.5 w-3.5" />
                    New chat
                </button>
            </div>

            <div ref={scrollRef} className="min-h-0 flex-1 space-y-6 overflow-y-auto pb-4 pr-1">
                {turns.map((turn, index) =>
                    turn.role === "user" ? (
                        <div key={index} className="flex justify-end">
                            <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-3xl rounded-br-lg bg-muted px-4 py-2.5 text-[15px]">
                                {turn.text}
                            </div>
                        </div>
                    ) : (
                        <div key={index} className="flex gap-3">
                            <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                                <Sparkles className="h-3.5 w-3.5" />
                            </span>
                            <div className="min-w-0 flex-1 space-y-3">
                                <RichReply text={turn.text} />

                                {turn.did && turn.did.length > 0 ? (
                                    <ul className="space-y-1 rounded-2xl border border-border bg-muted/30 px-3 py-2">
                                        {turn.did.map((words) => (
                                            <li
                                                key={words}
                                                className="flex items-center gap-2 text-xs text-muted-foreground"
                                            >
                                                <Check className="h-3.5 w-3.5 text-primary" />
                                                {words}
                                            </li>
                                        ))}
                                    </ul>
                                ) : null}

                                {turn.links && turn.links.length > 0 ? (
                                    <div className="flex flex-wrap gap-2">
                                        {turn.links.map((link) => (
                                            <a
                                                key={link.url}
                                                href={link.url}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                className="inline-flex items-center gap-1.5 rounded-full bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90"
                                            >
                                                <Link2 className="h-3.5 w-3.5" />
                                                Connect {link.app}
                                            </a>
                                        ))}
                                    </div>
                                ) : null}

                                {turn.agentCards && turn.agentCards.length > 0 ? (
                                    <div className="grid gap-2 sm:grid-cols-2">
                                        {turn.agentCards.map((agent) => (
                                            <AgentCard key={agent.id} agent={agent} />
                                        ))}
                                    </div>
                                ) : turn.agents && turn.agents.length > 0 ? (
                                    <div className="flex flex-wrap gap-2">
                                        {turn.agents.map((id) => (
                                            <Link
                                                key={id}
                                                href={`/workflow/${id}`}
                                                className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs hover:bg-muted"
                                            >
                                                <Bot className="h-3.5 w-3.5" />
                                                Open agent {id}
                                            </Link>
                                        ))}
                                    </div>
                                ) : null}

                                <div className="flex items-center gap-1">
                                    <CopyButton text={turn.text} />
                                </div>

                                {index === lastAssistant && !sending ? (
                                    <div className="flex flex-wrap gap-2 pt-1">
                                        {next.map((suggestion) => (
                                            <button
                                                key={suggestion}
                                                type="button"
                                                onClick={() => fill(suggestion)}
                                                className="rounded-full border border-border px-3 py-1.5 text-xs text-foreground/80 transition-colors hover:border-primary/40 hover:bg-muted"
                                            >
                                                {suggestion}
                                            </button>
                                        ))}
                                    </div>
                                ) : null}
                            </div>
                        </div>
                    ),
                )}

                {sending ? (
                    <div className="flex gap-3" aria-live="polite">
                        <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        </span>
                        <div className="min-w-0 rounded-2xl border border-border bg-muted/30 px-3.5 py-2.5">
                            <p className="text-sm font-medium">
                                Studio is working
                                <span className="ml-2 font-normal tabular-nums text-muted-foreground">
                                    {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
                                </span>
                            </p>
                            <p className="mt-0.5 text-xs text-muted-foreground">
                                Planning, writing the pages, building and checking the design
                                can take a minute or two.
                            </p>
                        </div>
                    </div>
                ) : null}
            </div>

            {errorBox ? <div className="pb-2">{errorBox}</div> : null}

            <Composer
                draft={draft}
                setDraft={setDraft}
                onSend={() => void send(draft)}
                sending={sending}
                usage={usage}
                large={false}
                inputRef={inputRef}
            />
        </div>
    );
}
