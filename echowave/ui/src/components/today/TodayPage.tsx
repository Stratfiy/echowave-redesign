"use client";

/**
 * Screen 07, Today: "What needs my attention?" as one ordered list --
 * approvals, what is due (with work under way beside it), the daily brief,
 * upcoming events and at most three suggestions -- in a 960 px reading
 * column. Not a dashboard: no decorative counts.
 *
 * Each section shows its own state. One that could not load says so with
 * Try again; "Nothing due in Decibyl" appears only when the server says
 * every section answered and nothing is there. A missing calendar is its
 * own line, with the connect step right here rather than on another screen.
 * Items open in a drawer with a deep link (`?open=`), so Back and a shared
 * link both work and the list keeps its place.
 */

import { CalendarPlus, RefreshCw } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
    dismissSuggestionApiV1TodaySuggestionsKeyDismissPost,
    getTodayApiV1TodayGet,
    proposeCallBackApiV1TodayMissedCallsMissedCallIdCallBackPost,
    refreshBriefApiV1TodayBriefRefreshPost,
    refreshEndOfDayApiV1TodayEndOfDayRefreshPost,
    setReminderStatusApiV1TodayRemindersReminderIdStatusPost,
    snoozeReminderApiV1TodayRemindersReminderIdSnoozePost,
} from "@/client/sdk.gen";
import { EmptyState } from "@/components/EmptyState";
import { GoogleCalendarConnect } from "@/components/integrations/GoogleCalendarConnect";
import { PageHeader } from "@/components/layout/PageHeader";
import { LearningToday } from "@/components/learning/LearningToday";
import { LiveNowStrip } from "@/components/live/LiveNowStrip";
import { Announcer, ErrorState, SourceCoverage, TaskStatus } from "@/components/shell";
import { ActivityDetailView } from "@/components/today/ActivityDetailView";
import { ApprovalDetail } from "@/components/today/ApprovalDetail";
import { EventRow } from "@/components/today/EventRow";
import { TodayDrawer } from "@/components/today/TodayDrawer";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import type { SourceRead } from "@/lib/shell/taskState";
import { TODAY_TABS } from "@/lib/today/tabs";
import type { BriefView, DueItem, SourceView, Suggestion, TodayView } from "@/lib/today/types";

const ROW =
    "motion-m6-enter flex min-h-11 w-full flex-col gap-0.5 rounded-md px-3 py-2 text-left hover:bg-muted/60 focus-visible:outline-2 focus-visible:outline-offset-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3";

function sourceReads(sources: SourceView[]): SourceRead[] {
    return sources.map((s) => ({ kind: s.kind, label: s.label, status: s.status, detail: s.detail ?? undefined }));
}

function Section({
    id,
    title,
    count,
    state,
    message,
    onRetry,
    children,
}: {
    id: string;
    title: string;
    count?: number;
    state?: "ok" | "failed";
    message?: string;
    onRetry?: () => void;
    children: React.ReactNode;
}) {
    return (
        <section aria-labelledby={`today-${id}`} className="flex flex-col gap-2" data-testid={`today-section-${id}`}>
            <h2 id={`today-${id}`} className="flex items-center gap-2 text-base font-semibold">
                {title}
                {count !== undefined && count > 0 && (
                    <span className="rounded-[var(--radius-pill)] border border-border px-2 text-xs font-normal" aria-label={`${count} waiting`}>
                        {count}
                    </span>
                )}
            </h2>
            {state === "failed" ? (
                <ErrorState title={message ?? `${title} could not load.`} onRetry={onRetry} />
            ) : (
                children
            )}
        </section>
    );
}

function BriefCard({
    brief,
    kind,
    onRefreshed,
}: {
    brief: BriefView | null;
    kind: "brief" | "end_of_day";
    onRefreshed: () => void;
}) {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [open, setOpen] = useState(false);

    async function refresh() {
        setBusy(true);
        setError(null);
        const result =
            kind === "brief" ? await refreshBriefApiV1TodayBriefRefreshPost() : await refreshEndOfDayApiV1TodayEndOfDayRefreshPost();
        setBusy(false);
        // A failed refresh keeps the older brief on screen, with its own
        // refresh time, and says the refresh failed.
        if (result.error) setError(detailFromError(result.error, "The refresh failed. This is the last one that worked."));
        else onRefreshed();
    }

    const label = kind === "brief" ? "brief" : "end-of-day note";
    if (!brief) {
        return (
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground">
                <span>No {label} yet today.</span>
                <Button variant="outline" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => void refresh()}>
                    Make it now
                </Button>
                {error && <span role="alert" className="text-destructive">{error}</span>}
            </div>
        );
    }
    const sections = brief.sections as Record<string, unknown>;
    const lists: { title: string; items: { title?: string; when?: string; at?: string }[] }[] = [];
    const appointments = (sections.appointments as { items?: { title?: string; when?: string; at?: string }[] } | undefined)?.items;
    if (appointments?.length) lists.push({ title: "Appointments", items: appointments });
    if (Array.isArray(sections.due) && sections.due.length) lists.push({ title: "Due", items: sections.due as { title?: string }[] });
    if (Array.isArray(sections.done) && sections.done.length) lists.push({ title: "Done today", items: sections.done as { title?: string }[] });
    if (Array.isArray(sections.left) && sections.left.length) lists.push({ title: "Still open", items: sections.left as { title?: string }[] });
    return (
        <div className="flex flex-col gap-2 rounded-md border border-border p-3" data-testid={`today-${kind}`} data-status={brief.status}>
            {brief.stale && (
                <p className="text-xs text-[#705500] dark:text-amber-300">This is from {brief.date}. Refresh for today.</p>
            )}
            <p className="text-sm">{brief.summary}</p>
            <p className="text-xs text-muted-foreground">Covers {brief.covered}</p>
            <SourceCoverage sources={sourceReads(brief.sources)} refreshedAt={brief.refreshed_at} />
            <div className="flex flex-wrap items-center gap-2">
                <Button variant="outline" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => void refresh()}>
                    <RefreshCw aria-hidden className={busy ? "motion-continuous h-4 w-4 animate-spin" : "h-4 w-4"} />
                    Refresh
                </Button>
                {lists.length > 0 && (
                    <Button variant="ghost" className="motion-m1 min-h-11 md:min-h-9" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
                        {open ? "Show less" : "Show detail"}
                    </Button>
                )}
            </div>
            {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
            {open && (
                <div className="motion-m2-enter flex flex-col gap-2">
                    {lists.map((list) => (
                        <div key={list.title}>
                            <h3 className="text-sm font-medium">{list.title}</h3>
                            <ul className="list-disc pl-5 text-sm">
                                {list.items.map((item, i) => (
                                    <li key={i} className="break-words">
                                        {item.title}
                                        {item.when ? ` · ${item.when}` : ""}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

export function TodayPage() {
    const { user, loading: authLoading } = useAuth();
    // Keyed on signed-in, not on the user object, so a re-render never
    // refetches.
    const signedIn = Boolean(user);
    const remindersOn = useFeature("today_reminders");
    const router = useRouter();
    const pathname = usePathname();
    const params = useSearchParams();
    const [view, setView] = useState<TodayView | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [announcement, setAnnouncement] = useState<string | null>(null);
    const [rowError, setRowError] = useState<string | null>(null);
    const [connecting, setConnecting] = useState(false);

    const open = params.get("open");

    const load = useCallback(async () => {
        setLoading(true);
        const result = await getTodayApiV1TodayGet();
        setLoading(false);
        if (result.error) {
            // The last good list stays, labelled; a failed refresh is not an
            // empty Today.
            setError(detailFromError(result.error, "Today could not refresh."));
            return;
        }
        setError(null);
        setView(result.data as unknown as TodayView);
    }, []);

    useEffect(() => {
        if (authLoading || !signedIn) return;
        void load();
    }, [authLoading, signedIn, load]);

    const setOpen = useCallback(
        (value: string | null) => {
            const next = new URLSearchParams(params.toString());
            if (value) next.set("open", value);
            else next.delete("open");
            const query = next.toString();
            router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
        },
        [params, pathname, router],
    );

    async function reminderVerb(item: DueItem, verb: "complete" | "snooze") {
        setRowError(null);
        const result =
            verb === "complete"
                ? await setReminderStatusApiV1TodayRemindersReminderIdStatusPost({ path: { reminder_id: item.id }, body: { verb: "complete" } })
                : await snoozeReminderApiV1TodayRemindersReminderIdSnoozePost({ path: { reminder_id: item.id }, body: { minutes: 60 } });
        if (result.error) {
            setRowError(detailFromError(result.error, "That did not go through."));
            return;
        }
        // Moved only after the server confirmed it.
        setAnnouncement(verb === "complete" ? `Done: ${item.title}. It is in Activity now.` : `Snoozed for an hour: ${item.title}.`);
        await load();
    }

    async function callBack(missedCallId: number) {
        setRowError(null);
        const result = await proposeCallBackApiV1TodayMissedCallsMissedCallIdCallBackPost({ path: { missed_call_id: missedCallId } });
        if (result.error) {
            setRowError(detailFromError(result.error, "Could not ask for that."));
            return;
        }
        const eventId = (result.data as { event_id: number }).event_id;
        await load();
        setOpen(`approval:${eventId}`);
    }

    async function suggestion(item: Suggestion, verb: "prepare" | "dismiss" | "later") {
        if (verb === "prepare") {
            if (item.action.kind === "propose_callback") return void callBack(item.action.missed_call_id);
            if (item.action.kind === "open_reminder") return void router.push(`/tasks/reminders/${item.action.reminder_id}`);
            if (item.action.kind === "open_brief_settings") return void router.push("/settings/daily-brief");
            return;
        }
        await dismissSuggestionApiV1TodaySuggestionsKeyDismissPost({
            path: { key: item.key },
            body: verb === "later" ? { later_minutes: 180 } : {},
        });
        setAnnouncement(verb === "later" ? "We will suggest it again later." : "Dismissed.");
        await load();
    }

    const header = (
        <PageHeader
            title="Today"
            tabs={TODAY_TABS}
            actions={
                <>
                    {remindersOn && (
                        <Button asChild variant="outline" className="motion-m1 min-h-11 md:min-h-9">
                            <Link href="/tasks/reminders/new">
                                <CalendarPlus aria-hidden className="h-4 w-4" />
                                New reminder
                            </Link>
                        </Button>
                    )}
                    <Button variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void load()} disabled={loading} aria-label="Refresh Today">
                        <RefreshCw aria-hidden className={loading ? "motion-continuous h-4 w-4 animate-spin" : "h-4 w-4"} />
                        <span className="hidden sm:inline">Refresh</span>
                    </Button>
                </>
            }
        />
    );

    if (!view) {
        return (
            <>
                {header}
                <div className="mx-auto w-full max-w-[960px] px-4 py-4 sm:px-6">
                    {error ? (
                        <ErrorState title="Today could not load." description={error} onRetry={() => void load()} />
                    ) : (
                        <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading Today">
                            <Skeleton className="h-5 w-48" />
                            <Skeleton className="h-20 w-full" />
                            <Skeleton className="h-20 w-full" />
                        </div>
                    )}
                </div>
            </>
        );
    }

    const s = view.sections;
    const [openKind, openId, openSub] = (open ?? "").split(":");
    return (
        <>
            {header}
            <Announcer message={announcement} />
            <div className="mx-auto flex w-full max-w-[960px] flex-col gap-6 px-4 py-4 sm:px-6" data-testid="today-list">
                <p className="text-sm text-muted-foreground" data-testid="today-scope">
                    <time dateTime={view.date}>{view.date_label}</time> · {view.timezone}
                    {error && <span className="ml-2 text-[#705500] dark:text-amber-300">{error} Showing the last list that loaded.</span>}
                </p>
                {rowError && (
                    <p role="alert" className="text-sm text-destructive">
                        {rowError}
                    </p>
                )}

                {/* The day's lesson, streak and due reviews (stream `learning`);
                    draws nothing unless learning is on and has something. */}
                <LearningToday className="" />

                {/* Calls in progress across the workspace; draws nothing
                    unless live_supervision is on and a call is live. */}
                <LiveNowStrip />

                <Section id="approvals" title="Waiting for your approval" count={s.approvals.count} state={s.approvals.state} message={s.approvals.message} onRetry={() => void load()}>
                    {s.approvals.items && s.approvals.items.length > 0 ? (
                        <ul className="flex flex-col">
                            {s.approvals.items.map((item) => (
                                <li key={item.id}>
                                    <button type="button" className={ROW} onClick={() => setOpen(`approval:${item.id}`)} aria-current={open === `approval:${item.id}` ? "true" : undefined}>
                                        <span className="min-w-0 break-words text-sm font-medium">{item.sentence}</span>
                                        {item.detail && <span className="min-w-0 break-words text-xs text-muted-foreground">{item.detail}</span>}
                                    </button>
                                </li>
                            ))}
                        </ul>
                    ) : (
                        <p className="px-3 text-sm text-muted-foreground">Nothing is waiting for you to approve.</p>
                    )}
                </Section>

                <Section id="due" title="Due" state={s.due.state} message={s.due.message} onRetry={() => void load()}>
                    {s.due.items.length > 0 ? (
                        <ul className="flex flex-col gap-1">
                            {s.due.items.map((item) => (
                                <li key={`${item.kind}-${item.id}`} className="flex flex-col gap-1 rounded-md px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
                                    <div className="min-w-0">
                                        <p className="break-words text-sm font-medium">{item.title}</p>
                                        {item.when && <p className="text-xs text-muted-foreground">{item.when}</p>}
                                        {(item.overdue || item.needs_input) && (
                                            <p className="text-xs text-[#705500] dark:text-amber-300">{item.needs_input ? "Needs your input" : "Past its time"}</p>
                                        )}
                                    </div>
                                    <div className="flex flex-wrap gap-2">
                                        {item.kind === "reminder" && (
                                            <>
                                                <Button variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void reminderVerb(item, "complete")}>
                                                    Done
                                                </Button>
                                                <Button variant="ghost" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void reminderVerb(item, "snooze")}>
                                                    Snooze 1 hour
                                                </Button>
                                                <Button asChild variant="ghost" className="motion-m1 min-h-11 md:min-h-9">
                                                    <Link href={`/tasks/reminders/${item.id}`}>Edit</Link>
                                                </Button>
                                            </>
                                        )}
                                        {item.kind === "task" && (
                                            <Button variant="ghost" className="motion-m1 min-h-11 md:min-h-9" onClick={() => setOpen(`activity:task:${item.id}`)}>
                                                Open
                                            </Button>
                                        )}
                                        {item.kind === "missed_call" && (
                                            <Button variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void callBack(item.id)}>
                                                Call back
                                            </Button>
                                        )}
                                    </div>
                                </li>
                            ))}
                        </ul>
                    ) : (
                        <p className="px-3 text-sm text-muted-foreground">Nothing due today.</p>
                    )}
                    {s.due.active && s.due.active.length > 0 && (
                        <div className="flex flex-col gap-1 px-3">
                            <h3 className="text-sm font-medium">Under way</h3>
                            {s.due.active.map((job) => (
                                <button key={job.id} type="button" className={ROW} onClick={() => setOpen(`activity:task:${job.id}`)}>
                                    <span className="break-words text-sm">{job.title}</span>
                                    <TaskStatus state={job.state} />
                                </button>
                            ))}
                        </div>
                    )}
                </Section>

                {s.brief && (
                    <Section id="brief" title="Daily brief" state={s.brief.state} message={s.brief.message} onRetry={() => void load()}>
                        <BriefCard brief={s.brief.item} kind="brief" onRefreshed={() => void load()} />
                        <p className="px-1 text-xs text-muted-foreground">
                            {s.brief.next_sentence}{" "}
                            <Link href="/settings/daily-brief" className="inline-flex min-h-11 items-center underline underline-offset-2 md:min-h-0">
                                Brief settings
                            </Link>
                        </p>
                    </Section>
                )}

                {s.upcoming && (
                    <Section id="upcoming" title="Upcoming" state={s.upcoming.state} message={s.upcoming.message} onRetry={() => void load()}>
                        {s.upcoming.items.length > 0 ? (
                            <ul className="flex flex-col gap-1">
                                {s.upcoming.items.map((event) => (
                                    <EventRow key={event.id} event={event} onChanged={(line) => {
                                        setAnnouncement(line);
                                        void load();
                                    }} />
                                ))}
                            </ul>
                        ) : (
                            <p className="px-3 text-sm text-muted-foreground">No events in Decibyl for the next seven days.</p>
                        )}
                    </Section>
                )}

                {view.missing_sources.map((missing) => (
                    <div key={missing.kind} className="flex flex-col gap-2 rounded-md border border-dashed border-border p-3" data-testid={`missing-${missing.kind}`}>
                        <div className="flex flex-wrap items-center gap-2">
                            <p className="text-sm">{missing.message}.</p>
                            {missing.kind === "calendar" && !connecting && (
                                <Button variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => setConnecting(true)}>
                                    Connect calendar
                                </Button>
                            )}
                        </div>
                        {missing.kind === "calendar" && connecting && <GoogleCalendarConnect returnPath="/tasks" />}
                    </div>
                ))}

                {s.suggestions.items.length > 0 && (
                    <Section id="suggestions" title="Suggestions">
                        <ul className="flex flex-col gap-2">
                            {s.suggestions.items.map((item) => (
                                <li key={item.key} className="flex flex-col gap-1 rounded-md border border-border p-3">
                                    <p className="break-words text-sm font-medium">{item.title}</p>
                                    <details className="text-sm">
                                        <summary className="inline-flex min-h-11 cursor-pointer items-center text-muted-foreground md:min-h-8">Why this?</summary>
                                        <p className="mt-1 text-muted-foreground">{item.why}</p>
                                    </details>
                                    <div className="flex flex-wrap gap-2">
                                        {item.action.kind !== "none" && (
                                            <Button variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void suggestion(item, "prepare")}>
                                                {item.action.kind === "open_brief_settings" ? "Set it up" : "Prepare"}
                                            </Button>
                                        )}
                                        <Button variant="ghost" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void suggestion(item, "later")}>
                                            Later
                                        </Button>
                                        <Button variant="ghost" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void suggestion(item, "dismiss")}>
                                            Dismiss
                                        </Button>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    </Section>
                )}

                {s.end_of_day && (
                    <Section id="end-of-day" title="End of day" state={s.end_of_day.state} message={s.end_of_day.message} onRetry={() => void load()}>
                        <BriefCard brief={s.end_of_day.item} kind="end_of_day" onRefreshed={() => void load()} />
                    </Section>
                )}

                {view.empty && view.empty_copy && (
                    <EmptyState
                        title={view.empty_copy}
                        description={remindersOn ? "Add a reminder, or ask Decibyl in Chat to keep track of something." : "Ask Decibyl in Chat to keep track of something."}
                    />
                )}
            </div>

            {openKind === "approval" && openId && (
                <TodayDrawer title="Approval" onClose={() => setOpen(null)}>
                    <ApprovalDetail eventId={Number(openId)} onChanged={() => void load()} />
                </TodayDrawer>
            )}
            {openKind === "activity" && openId && openSub && (
                <TodayDrawer title="Task" onClose={() => setOpen(null)}>
                    <ActivityDetailView kind={openId} id={Number(openSub)} onChanged={() => void load()} />
                </TodayDrawer>
            )}
        </>
    );
}

export default TodayPage;
