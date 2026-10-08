"use client";

/**
 * "Today" under the composer on an empty Chat (the approved design's home,
 * Home.dc.html): the last few things that happened today, one quiet row
 * each -- who did it as a blob, what happened, when.
 *
 * Read from Today's own Activity list (GET /today/activity, screen 09), so
 * the home never says something the Activity tab does not. Only today's
 * items, newest first, at most five; nothing today draws nothing. Each row
 * opens that item's detail on Today.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { listActivityApiV1TodayActivityGet } from "@/client/sdk.gen";
import { BlobFace } from "@/components/brand/BlobFace";
import { useAuth } from "@/lib/auth";
import { TASK_STATE_LABEL } from "@/lib/shell/taskState";
import type { ActivityItem } from "@/lib/today/types";

/** How many rows the home holds; the rest is on Today. */
export const HOME_TODAY_LIMIT = 5;

function sameDay(a: Date, b: Date): boolean {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** Today's items, newest first, at most `limit`. */
export function todaysItems(items: ActivityItem[], now: Date = new Date(), limit = HOME_TODAY_LIMIT): ActivityItem[] {
    return items
        .filter((item) => item.at && sameDay(new Date(item.at), now))
        .sort((a, b) => (b.at ?? "").localeCompare(a.at ?? ""))
        .slice(0, limit);
}

function clock(at: string): string {
    return new Date(at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export function HomeToday({ bots }: { bots: { id: number; name: string }[] }) {
    const { user, loading: authLoading } = useAuth();
    const signedIn = Boolean(user);
    const [items, setItems] = useState<ActivityItem[] | null>(null);

    useEffect(() => {
        if (authLoading || !signedIn) return;
        let cancelled = false;
        void listActivityApiV1TodayActivityGet({ query: { days: 1 } })
            .then((result) => {
                if (cancelled || result.error || !result.data) return;
                setItems((result.data as unknown as { items: ActivityItem[] }).items ?? []);
            })
            .catch(() => undefined);
        return () => {
            cancelled = true;
        };
    }, [authLoading, signedIn]);

    const shown = items ? todaysItems(items) : [];
    if (shown.length === 0) return null;
    const botId = new Map(bots.map((bot) => [bot.name, bot.id]));

    return (
        <section aria-labelledby="home-today" className="flex w-full flex-col text-left" data-testid="home-today">
            <h2 id="home-today" className="mt-3.5 px-1 pb-1 text-[13px] font-normal tracking-normal text-[var(--ink-3,#8f8f8f)]">
                Today
            </h2>
            <ul className="flex flex-col">
                {shown.map((item) => {
                    const id = botId.get(item.helper);
                    const detail = [item.helper, item.state !== "completed" ? TASK_STATE_LABEL[item.state] : null]
                        .filter(Boolean)
                        .join(" · ");
                    return (
                        <li key={`${item.kind}-${item.id}`}>
                            <Link
                                href={`/tasks/activity/${item.kind}/${item.id}`}
                                className="motion-m1 flex min-h-11 items-center gap-3 rounded-[10px] px-1 py-2 hover:bg-[var(--paper-2,#f9f9f9)]"
                            >
                                {id !== undefined ? (
                                    <BlobFace seed={id} size={26} />
                                ) : (
                                    // Not one of the agents: Decibyl's own work (reminders, the brief, the task board).
                                    <span aria-hidden="true" className="grid h-[26px] w-[26px] shrink-0 place-items-center rounded-full bg-foreground text-[12px] font-semibold text-background">
                                        d
                                    </span>
                                )}
                                <span className="min-w-0 flex-1 truncate text-[15px]">
                                    {item.title} <span className="text-[var(--ink-3,#8f8f8f)]">· {detail}</span>
                                </span>
                                {item.at && (
                                    <time dateTime={item.at} className="shrink-0 text-[13px] text-[var(--ink-3,#8f8f8f)]">
                                        {clock(item.at)}
                                    </time>
                                )}
                            </Link>
                        </li>
                    );
                })}
            </ul>
        </section>
    );
}

export default HomeToday;
