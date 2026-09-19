"use client";

/**
 * What the bots have handed over: one list, across every bot.
 *
 * The timeline has marked these rows since it was built — an outcome filed,
 * a file produced, a step that could not be done, a bot that needs somebody
 * — and the route has taken `deliverables_only` for as long. Nothing ever
 * asked for it, so the only way to find what a bot produced on Tuesday was
 * to scroll its thread past every message and call it also wrote.
 *
 * Newest first, grouped by day, because the question is nearly always "what
 * came in since I last looked". Each row says which bot, what it is, and
 * opens the call it came from — the thread stays the place for the
 * conversation around it.
 */

import { AlertTriangle, CheckCircle2, CircleSlash, FileText, Loader2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { getWorkflowsApiV1WorkflowFetchGet, timelineApiV1TimelineGet } from "@/client/sdk.gen";
import type { TimelineEvent } from "@/client/types.gen";
import { BotAvatar } from "@/components/bot/BotAvatar";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { DESK_TABS } from "@/components/layout/SectionTabs";

import { byDay } from "./grouping";

/** How each kind reads, so a row says what happened before it is read. */
const TONE: Record<string, { icon: typeof FileText; className: string; word: string }> = {
    outcome_filed: { icon: CheckCircle2, className: "text-emerald-600", word: "Sorted" },
    deliverable: { icon: FileText, className: "text-emerald-600", word: "Produced" },
    could_not: { icon: CircleSlash, className: "text-amber-600", word: "Could not" },
    needs_attention: { icon: AlertTriangle, className: "text-amber-600", word: "Needs you" },
};

type Attached = { document_uuid: string; filename: string };

function filesOf(event: TimelineEvent): Attached[] {
    const list = (event.payload as { attachments?: unknown } | null)?.attachments;
    if (!Array.isArray(list)) return [];
    return list.filter(
        (a): a is Attached =>
            !!a && typeof a === "object" && typeof (a as Attached).filename === "string",
    );
}

function clock(at: string): string {
    const date = new Date(at);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export default function DeliverablesPage() {
    const [events, setEvents] = useState<TimelineEvent[] | null>(null);
    const [names, setNames] = useState<Record<number, string>>({});
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            const [feed, bots] = await Promise.all([
                timelineApiV1TimelineGet({ query: { deliverables_only: true, limit: 100 } }),
                getWorkflowsApiV1WorkflowFetchGet(),
            ]);
            if (cancelled) return;
            if (feed.error || !feed.data) {
                setError("Could not read what your bots have handed over.");
                setEvents([]);
            } else {
                setEvents(feed.data.events ?? []);
            }
            if (bots.data) {
                setNames(Object.fromEntries(bots.data.map((bot) => [bot.id, bot.name])));
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    return (
        <>
            <PageHeader
                tabs={DESK_TABS}
                title="Handed over"
                description="What your bots produced, sorted or could not do — every bot, newest first."
            />
            <PageBody className="max-w-3xl space-y-6">
                {events === null && (
                    <p className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                        Reading…
                    </p>
                )}

                {error && (
                    <p className="flex items-center gap-2 text-sm text-destructive" role="alert">
                        <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
                        {error}
                    </p>
                )}

                {events !== null && !error && events.length === 0 && (
                    <div>
                        <p className="text-sm font-medium">Nothing handed over yet.</p>
                        <p className="mt-1 text-sm text-muted-foreground">
                            When a bot files an outcome, produces a file, or stops and
                            needs you, it lands here as well as in its own thread.
                        </p>
                    </div>
                )}

                {byDay(events ?? []).map((group) => (
                    <section key={group.day} aria-label={group.day}>
                        <h2 className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                            {group.day}
                        </h2>
                        <ul className="mt-2 divide-y divide-border/60">
                            {group.events.map((event) => {
                                const tone = TONE[event.kind];
                                const Icon = tone?.icon ?? FileText;
                                const who =
                                    (event.workflow_id != null && names[event.workflow_id]) ||
                                    "A bot";
                                const href =
                                    event.workflow_id != null && event.workflow_run_id != null
                                        ? `/workflow/${event.workflow_id}/run/${event.workflow_run_id}`
                                        : event.workflow_id != null
                                          ? `/workflow/${event.workflow_id}/thread`
                                          : null;
                                const files = filesOf(event);
                                return (
                                    <li key={event.id} className="flex gap-3 py-3">
                                        <BotAvatar
                                            id={event.workflow_id ?? who}
                                            name={who}
                                            size="sm"
                                            className="mt-0.5 h-7 w-7 rounded-md"
                                        />
                                        <div className="min-w-0 flex-1">
                                            <p className="text-sm">
                                                <span className="font-medium">{who}</span>
                                                {tone && (
                                                    <span className="ml-2 inline-flex items-center gap-1 text-xs text-muted-foreground">
                                                        <Icon
                                                            className={`h-3.5 w-3.5 ${tone.className}`}
                                                            aria-hidden
                                                        />
                                                        {tone.word}
                                                    </span>
                                                )}
                                                <span className="ml-2 text-xs text-muted-foreground">
                                                    <time dateTime={event.at}>{clock(event.at)}</time>
                                                </span>
                                            </p>
                                            <p className="mt-0.5 text-sm">
                                                {href ? (
                                                    <Link
                                                        href={href}
                                                        className="underline-offset-2 hover:underline"
                                                    >
                                                        {event.summary}
                                                    </Link>
                                                ) : (
                                                    event.summary
                                                )}
                                            </p>
                                            {files.length > 0 && (
                                                <ul
                                                    className="mt-1.5 flex flex-wrap gap-2"
                                                    aria-label="Files"
                                                >
                                                    {files.map((file) => (
                                                        <li
                                                            key={file.document_uuid}
                                                            className="flex items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1.5 text-sm"
                                                        >
                                                            <FileText
                                                                className="h-4 w-4 shrink-0 text-muted-foreground"
                                                                aria-hidden
                                                            />
                                                            <span className="max-w-[16rem] truncate">
                                                                {file.filename}
                                                            </span>
                                                        </li>
                                                    ))}
                                                </ul>
                                            )}
                                        </div>
                                    </li>
                                );
                            })}
                        </ul>
                    </section>
                ))}
            </PageBody>
        </>
    );
}
