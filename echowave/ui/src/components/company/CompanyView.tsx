"use client";

/**
 * Company: the whole operation on one screen, Paperclip-style, in the Grok
 * look the home and Studio already have -- one calm column, a large ask box
 * at the top, and cards with room around them.
 *
 * Top to bottom: ask the company (it goes to Decibyl and opens the thread),
 * the headline strip, the org chart beside what needs you, then heartbeats
 * and the activity feed.
 */

import {
    ArrowUp,
    Ban,
    Brush,
    CircleAlert,
    FileCheck2,
    HeartPulse,
    Loader2,
    Plus,
    Wallet,
    X,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useMemo, useState } from "react";

import { postMessageApiV1TimelineMessagePost } from "@/client/sdk.gen";
import type { RoutineResponse, TimelineEvent } from "@/client/types.gen";
import { AgentAvatar } from "@/components/avatar/AgentAvatar";
import { type Avatar, faceOf } from "@/components/avatar/avatar";
import { AvatarCustomizer } from "@/components/avatar/AvatarCustomizer";
import { detailFromError } from "@/lib/apiError";
import { useFeature } from "@/lib/features";
import { cn } from "@/lib/utils";

import {
    buildOrg,
    headline,
    heartbeats,
    inbox,
    type InboxItem,
    type OrgAgent,
    type OrgTeam,
    relative,
} from "./company";
import { useCompany } from "./useCompany";

const ASKS = [
    "Who needs me today?",
    "What did the team finish this week?",
    "Where is the money going this month?",
    "Which agent should I hire next?",
];

const TONE: Record<string, { dot: string; label: string }> = {
    attention: { dot: "bg-amber-500", label: "Needs you" },
    working: { dot: "bg-emerald-500", label: "Working" },
    idle: { dot: "bg-slate-400", label: "Idle" },
    paused: { dot: "bg-slate-300 dark:bg-slate-600", label: "Paused" },
};
const toneOf = (tone: string) => TONE[tone] ?? TONE.idle;

export function CompanyView() {
    const { data, error, dismissIncident } = useCompany();
    // Faces changed here, ahead of the next refresh bringing them back.
    const [faces, setFaces] = useState<Record<number, Avatar | null>>({});
    const [editing, setEditing] = useState<OrgAgent | null>(null);

    const view = useMemo(() => {
        if (!data) return null;
        const names = new Map(data.members.map((m) => [m.workflow_id, m.name]));
        return {
            teams: buildOrg(data).map((team) => ({
                ...team,
                agents: team.agents.map((agent) => (agent.id in faces ? { ...agent, avatar: faces[agent.id] } : agent)),
            })),
            head: headline(data.members, data.tasks, data.policies),
            items: inbox({
                incidents: data.incidents,
                tasks: data.tasks,
                members: data.members,
                nameOf: (id) => (id === null ? "The workspace" : (names.get(id) ?? "An agent")),
            }),
            beats: heartbeats(data.routines).slice(0, 6),
            events: data.events,
        };
    }, [data, faces]);

    return (
        <div className="mx-auto w-full max-w-6xl px-4 pb-16 pt-8 sm:px-6">
            <header className="mx-auto max-w-2xl text-center">
                <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Your company</h1>
                <p className="mt-2 text-muted-foreground">
                    Every agent, what they are working on, what they cost, and what is waiting on you.
                </p>
            </header>

            <AskBox />

            {error && (
                <p role="alert" className="mx-auto mt-4 max-w-2xl text-center text-sm text-destructive">
                    {error}
                </p>
            )}

            {!view ? (
                <div className="mt-16 flex justify-center text-muted-foreground">
                    <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" />
                </div>
            ) : (
                <>
                    <section aria-label="At a glance" className="mt-10 grid grid-cols-2 gap-3 lg:grid-cols-4">
                        <Stat
                            label="Agents"
                            value={view.head.agents}
                            detail={`${view.head.working} working · ${view.head.needsYou} need you`}
                        />
                        <Stat
                            label="Open tasks"
                            value={view.head.openTasks}
                            detail={`${view.head.inProgress} in progress · ${view.head.blocked} blocked`}
                        />
                        <Stat label="Waiting on you" value={view.items.length} detail={`${view.head.inReview} reports to sign off`} />
                        <SpendStat spent={view.head.spent} limit={view.head.limit} />
                    </section>

                    <div className="mt-8 grid gap-6 lg:grid-cols-3">
                        <OrgChart teams={view.teams} onEditFace={setEditing} />
                        <Inbox items={view.items} onDismiss={dismissIncident} />
                    </div>

                    <div className="mt-6 grid gap-6 lg:grid-cols-3">
                        <Heartbeats beats={view.beats} />
                        <Activity events={view.events} />
                    </div>
                </>
            )}

            {editing && (
                <AvatarCustomizer
                    key={editing.id}
                    workflowId={editing.id}
                    name={editing.name}
                    avatar={faceOf(editing.id, editing.avatar)}
                    open
                    onOpenChange={(open) => {
                        if (!open) setEditing(null);
                    }}
                    onSaved={(avatar) => setFaces((current) => ({ ...current, [editing.id]: avatar }))}
                />
            )}
        </div>
    );
}

// --- ask -------------------------------------------------------------------

function AskBox() {
    const router = useRouter();
    const [text, setText] = useState("");
    const [sending, setSending] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const send = async (body: string) => {
        const message = body.trim();
        if (!message || sending) return;
        setSending(true);
        setError(null);
        const response = await postMessageApiV1TimelineMessagePost({ body: { assistant: true, text: message } });
        if (response.error) {
            setSending(false);
            setError(detailFromError(response.error, "Could not send that"));
            return;
        }
        router.push("/overview");
    };

    const onSubmit = (event: FormEvent) => {
        event.preventDefault();
        void send(text);
    };

    return (
        <div className="mx-auto mt-8 max-w-2xl">
            <form
                onSubmit={onSubmit}
                className="flex items-end gap-2 rounded-3xl border bg-card p-2 pl-5 shadow-sm transition focus-within:shadow-md"
            >
                <label htmlFor="company-ask" className="sr-only">
                    Ask your company
                </label>
                <textarea
                    id="company-ask"
                    rows={1}
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                            e.preventDefault();
                            void send(text);
                        }
                    }}
                    placeholder="Ask your company anything"
                    className="max-h-40 min-h-[2.75rem] flex-1 resize-none bg-transparent py-3 text-base outline-none placeholder:text-muted-foreground"
                />
                <button
                    type="submit"
                    disabled={!text.trim() || sending}
                    aria-label="Send"
                    className="mb-1 grid h-10 w-10 shrink-0 place-items-center rounded-full bg-foreground text-background transition disabled:opacity-30"
                >
                    {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
                </button>
            </form>
            <div className="mt-3 flex flex-wrap justify-center gap-2">
                {ASKS.map((ask) => (
                    <button
                        key={ask}
                        type="button"
                        disabled={sending}
                        onClick={() => void send(ask)}
                        className="rounded-full border px-3 py-1.5 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-50"
                    >
                        {ask}
                    </button>
                ))}
            </div>
            {error && (
                <p role="alert" className="mt-2 text-center text-sm text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}

// --- the strip -------------------------------------------------------------

function Stat({ label, value, detail }: { label: string; value: number | string; detail: string }) {
    return (
        <div className="rounded-2xl border bg-card p-4">
            <div className="text-sm text-muted-foreground">{label}</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
            <div className="mt-1 truncate text-xs text-muted-foreground">{detail}</div>
        </div>
    );
}

function SpendStat({ spent, limit }: { spent: number; limit: number | null }) {
    const percent = limit ? Math.min(100, Math.round((spent / limit) * 100)) : null;
    return (
        <div className="rounded-2xl border bg-card p-4">
            <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                <Wallet className="h-3.5 w-3.5" aria-hidden="true" /> Spend this month
            </div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">
                {spent.toLocaleString()}
                <span className="text-sm font-normal text-muted-foreground">
                    {limit ? ` / ${limit.toLocaleString()} credits` : " credits"}
                </span>
            </div>
            {percent !== null ? (
                <Meter percent={percent} className="mt-2" />
            ) : (
                <Link href="/billing" className="mt-1 block text-xs text-muted-foreground underline-offset-2 hover:underline">
                    No budget set
                </Link>
            )}
        </div>
    );
}

function Meter({ percent, className }: { percent: number; className?: string }) {
    const tone = percent >= 100 ? "bg-red-500" : percent >= 80 ? "bg-amber-500" : "bg-foreground/70";
    return (
        <div
            className={cn("h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}
            role="meter"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            aria-label={`${percent}% of budget`}
        >
            <div className={cn("h-full rounded-full", tone)} style={{ width: `${percent}%` }} />
        </div>
    );
}

// --- the org chart ---------------------------------------------------------

function OrgChart({ teams, onEditFace }: { teams: OrgTeam[]; onEditFace: (agent: OrgAgent) => void }) {
    return (
        <section aria-labelledby="org-chart" className="rounded-3xl border bg-card p-5 lg:col-span-2">
            <div className="flex items-center justify-between">
                <h2 id="org-chart" className="text-lg font-semibold">
                    Org chart
                </h2>
                <Link
                    href="/start"
                    className="inline-flex items-center gap-1 rounded-full border px-3 py-1 text-sm hover:bg-muted"
                >
                    <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Hire an agent
                </Link>
            </div>

            {teams.length === 0 ? (
                <p className="mt-6 text-sm text-muted-foreground">
                    No agents yet. Hire your first one and it will show up here with its team.
                </p>
            ) : (
                <div className="mt-6 flex flex-col items-center">
                    <div className="rounded-2xl border bg-background px-4 py-2 text-sm font-medium">You</div>
                    <div className="h-5 w-px bg-border" aria-hidden="true" />
                    <ul className="grid w-full gap-4 border-t pt-5 sm:grid-cols-2 xl:grid-cols-3">
                        {teams.map((team) => (
                            <li key={team.id ?? "none"} className="min-w-0">
                                <div className="mb-2 flex items-baseline justify-between gap-2 px-1">
                                    <h3 className="truncate text-sm font-medium">{team.name}</h3>
                                    <span className="text-xs text-muted-foreground">{team.agents.length}</span>
                                </div>
                                <ul className="space-y-2">
                                    {team.agents.map((agent) => (
                                        <li key={agent.id}>
                                            <AgentNode agent={agent} onEditFace={() => onEditFace(agent)} />
                                        </li>
                                    ))}
                                </ul>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </section>
    );
}

function AgentNode({ agent, onEditFace }: { agent: OrgAgent; onEditFace: () => void }) {
    const tone = toneOf(agent.tone);
    const facesOn = useFeature("agent_faces");
    return (
        <div className="group relative">
            <Link
                href={`/workflow/${agent.id}`}
                className="block rounded-2xl border bg-background p-3 transition hover:border-foreground/30 hover:shadow-sm"
            >
                <div className="flex items-center gap-3 pr-6">
                    <span className="relative shrink-0">
                        {facesOn ? (
                            <AgentAvatar avatar={faceOf(agent.id, agent.avatar)} tone={agent.tone} size={40} />
                        ) : (
                            <span
                                className="grid h-10 w-10 place-items-center rounded-full bg-muted text-sm font-medium"
                                aria-hidden="true"
                            >
                                {agent.name.slice(0, 1).toUpperCase()}
                            </span>
                        )}
                        <span
                            className={cn("absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full ring-2 ring-background", tone.dot)}
                            aria-hidden="true"
                        />
                    </span>
                    <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                            <span className="truncate text-sm font-medium">{agent.name}</span>
                            <span className="sr-only">, {tone.label}</span>
                        </div>
                        <div className="truncate text-xs text-muted-foreground">
                            {agent.handle ? `@${agent.handle} · ` : ""}
                            {agent.status}
                        </div>
                    </div>
                </div>
                <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
                    <span>{agent.openTasks} open</span>
                    <span>{agent.outcomes} done today</span>
                    {agent.failures > 0 && <span className="text-red-600 dark:text-red-400">{agent.failures} failed</span>}
                    {agent.lastAt && <span className="ml-auto">{relative(agent.lastAt)}</span>}
                </div>
                {agent.budget && (
                    <div className="mt-2">
                        <Meter percent={agent.budget.percent} />
                        <div className="mt-1 text-[11px] text-muted-foreground tabular-nums">
                            {agent.budget.spent} / {agent.budget.limit} credits
                        </div>
                    </div>
                )}
            </Link>
            {facesOn && (
                <button
                    type="button"
                    onClick={onEditFace}
                    aria-label={`Change ${agent.name}'s face`}
                    title="Change face"
                    className="absolute right-2 top-2 rounded-full p-1.5 text-muted-foreground opacity-0 transition hover:bg-muted hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
                >
                    <Brush className="h-3.5 w-3.5" />
                </button>
            )}
        </div>
    );
}

// --- needs you -------------------------------------------------------------

const KIND_ICON: Record<InboxItem["kind"], typeof Ban> = {
    budget: Wallet,
    blocked: Ban,
    review: FileCheck2,
    agent: CircleAlert,
};

function Inbox({ items, onDismiss }: { items: InboxItem[]; onDismiss: (incidentId: number) => void }) {
    return (
        <section aria-labelledby="needs-you" className="rounded-3xl border bg-card p-5">
            <div className="flex items-center justify-between">
                <h2 id="needs-you" className="text-lg font-semibold">
                    Needs you
                </h2>
                {items.length > 0 && (
                    <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300">
                        {items.length}
                    </span>
                )}
            </div>
            {items.length === 0 ? (
                <p className="mt-6 text-sm text-muted-foreground">Nothing is waiting on you. The team has it.</p>
            ) : (
                <ul className="mt-4 space-y-2">
                    {items.slice(0, 8).map((item) => {
                        const Icon = KIND_ICON[item.kind];
                        return (
                            <li key={item.key} className="group relative rounded-2xl border bg-background p-3 hover:border-foreground/30">
                                <Link href={item.href} className="flex gap-3">
                                    <Icon
                                        className={cn(
                                            "mt-0.5 h-4 w-4 shrink-0",
                                            item.kind === "budget" || item.kind === "blocked" ? "text-red-500" : "text-amber-500",
                                        )}
                                        aria-hidden="true"
                                    />
                                    <span className="min-w-0 flex-1 pr-5">
                                        <span className="block truncate text-sm font-medium">{item.title}</span>
                                        <span className="block truncate text-xs text-muted-foreground">{item.detail}</span>
                                    </span>
                                </Link>
                                {item.incidentId !== undefined && (
                                    <button
                                        type="button"
                                        onClick={() => onDismiss(item.incidentId as number)}
                                        aria-label={`Dismiss: ${item.title}`}
                                        className="absolute right-2 top-2 rounded-full p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                                    >
                                        <X className="h-3.5 w-3.5" />
                                    </button>
                                )}
                            </li>
                        );
                    })}
                </ul>
            )}
            {items.length > 8 && (
                <Link href="/tasks" className="mt-3 block text-sm text-muted-foreground hover:text-foreground">
                    {items.length - 8} more on the board
                </Link>
            )}
        </section>
    );
}

// --- heartbeats and activity -----------------------------------------------

function Heartbeats({ beats }: { beats: RoutineResponse[] }) {
    return (
        <section aria-labelledby="heartbeats" className="rounded-3xl border bg-card p-5">
            <div className="flex items-center justify-between">
                <h2 id="heartbeats" className="flex items-center gap-2 text-lg font-semibold">
                    <HeartPulse className="h-4 w-4" aria-hidden="true" /> Heartbeats
                </h2>
                <Link href="/tasks" className="text-sm text-muted-foreground hover:text-foreground">
                    All
                </Link>
            </div>
            {beats.length === 0 ? (
                <p className="mt-6 text-sm text-muted-foreground">
                    No agent wakes on a schedule yet. Ask one to do something every morning and it shows here.
                </p>
            ) : (
                <ul className="mt-4 space-y-3">
                    {beats.map((beat) => (
                        <li key={beat.id} className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                                <div className="truncate text-sm font-medium">{beat.name}</div>
                                <div className="truncate text-xs text-muted-foreground">
                                    {beat.workflow_name ?? "Decibyl"} · {beat.schedule_summary || beat.cadence}
                                </div>
                            </div>
                            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{relative(beat.next_run_at)}</span>
                        </li>
                    ))}
                </ul>
            )}
        </section>
    );
}

function Activity({ events }: { events: TimelineEvent[] }) {
    return (
        <section aria-labelledby="activity" className="rounded-3xl border bg-card p-5 lg:col-span-2">
            <div className="flex items-center justify-between">
                <h2 id="activity" className="text-lg font-semibold">
                    Activity
                </h2>
                <Link href="/usage" className="text-sm text-muted-foreground hover:text-foreground">
                    All activity
                </Link>
            </div>
            {events.length === 0 ? (
                <p className="mt-6 text-sm text-muted-foreground">Quiet so far. What the agents do lands here as it happens.</p>
            ) : (
                <ol className="mt-4 space-y-3 border-l pl-4">
                    {events.slice(0, 12).map((event) => (
                        <li key={event.id} className="relative">
                            <span className="absolute -left-[1.3rem] top-1.5 h-2 w-2 rounded-full bg-border" aria-hidden="true" />
                            <div className="flex items-baseline justify-between gap-3">
                                <p className="min-w-0 text-sm">
                                    <span className="font-medium">{event.actor}</span>{" "}
                                    <span className="text-muted-foreground">{event.summary}</span>
                                </p>
                                <time dateTime={event.at} className="shrink-0 text-xs tabular-nums text-muted-foreground">
                                    {relative(event.at)}
                                </time>
                            </div>
                        </li>
                    ))}
                </ol>
            )}
        </section>
    );
}
