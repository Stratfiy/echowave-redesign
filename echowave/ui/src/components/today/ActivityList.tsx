"use client";

/**
 * Screen 09, Activity: finished, failed and cancelled work with its
 * evidence, in compact rows. Filters (state, helper) live in the URL so
 * Back and a shared link keep them; on a phone they open as a sheet-like
 * disclosure above the list.
 */

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { listActivityApiV1TodayActivityGet } from "@/client/sdk.gen";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { LiveNowStrip } from "@/components/live/LiveNowStrip";
import { ErrorState, TaskStatus } from "@/components/shell";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { TASK_STATE_LABEL, type TaskState } from "@/lib/shell/taskState";
import { TODAY_TABS } from "@/lib/today/tabs";
import type { ActivityItem } from "@/lib/today/types";

const STATES: TaskState[] = [
    "completed",
    "failed",
    "cancelled",
    "outcome_unknown",
    "running",
    "scheduled",
    "needs_input",
    "queued",
];

function when(at: string | null): string {
    if (!at) return "";
    return new Date(at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function ActivityList() {
    const { user, loading: authLoading } = useAuth();
    // Keyed on signed-in, not on the user object, so a re-render never
    // refetches.
    const signedIn = Boolean(user);
    const router = useRouter();
    const pathname = usePathname();
    const params = useSearchParams();
    const state = params.get("state") ?? "";
    const helper = params.get("helper") ?? "";
    const [items, setItems] = useState<ActivityItem[] | null>(null);
    const [helpers, setHelpers] = useState<string[]>([]);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        const result = await listActivityApiV1TodayActivityGet({
            query: { ...(state ? { state } : {}), ...(helper ? { helper } : {}) },
        });
        if (result.error) {
            setError(detailFromError(result.error, "Activity could not load."));
            return;
        }
        setError(null);
        const data = result.data as unknown as { items: ActivityItem[]; helpers: string[] };
        setItems(data.items);
        setHelpers((was) => (data.helpers.length ? data.helpers : was));
    }, [state, helper]);

    useEffect(() => {
        if (authLoading || !signedIn) return;
        void load();
    }, [authLoading, signedIn, load]);

    function setFilter(key: "state" | "helper", value: string) {
        const next = new URLSearchParams(params.toString());
        if (value) next.set(key, value);
        else next.delete(key);
        const query = next.toString();
        router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    }

    const query = params.toString();
    const filtered = Boolean(state || helper);
    return (
        <>
            <PageHeader title="Activity" tabs={TODAY_TABS} />
            <div className="mx-auto flex w-full max-w-[960px] flex-col gap-4 px-4 py-4 sm:px-6">
                {/* Work still in progress on the phone; nothing while none is. */}
                <LiveNowStrip />
                <details className="rounded-md border border-border p-2 md:open:border-transparent" open={filtered || undefined}>
                    <summary className="inline-flex min-h-11 cursor-pointer items-center px-1 text-sm font-medium md:min-h-8">
                        Filters{filtered ? " (on)" : ""}
                    </summary>
                    <div className="mt-2 flex flex-col gap-3 sm:flex-row">
                        <label className="flex flex-col gap-1 text-sm">
                            Status
                            <select
                                value={state}
                                onChange={(e) => setFilter("state", e.target.value)}
                                className="min-h-11 rounded-md border border-[#7B8491] bg-background px-2 text-base md:min-h-9 md:text-sm"
                            >
                                <option value="">Any</option>
                                {STATES.map((s) => (
                                    <option key={s} value={s}>
                                        {TASK_STATE_LABEL[s]}
                                    </option>
                                ))}
                            </select>
                        </label>
                        <label className="flex flex-col gap-1 text-sm">
                            Helper
                            <select
                                value={helper}
                                onChange={(e) => setFilter("helper", e.target.value)}
                                className="min-h-11 rounded-md border border-[#7B8491] bg-background px-2 text-base md:min-h-9 md:text-sm"
                            >
                                <option value="">Any</option>
                                {helpers.map((h) => (
                                    <option key={h} value={h}>
                                        {h}
                                    </option>
                                ))}
                            </select>
                        </label>
                    </div>
                </details>

                {error && !items && <ErrorState title="Activity could not load." description={error} onRetry={() => void load()} />}
                {!items && !error && (
                    <div className="flex flex-col gap-2" aria-busy="true">
                        <Skeleton className="h-12 w-full" />
                        <Skeleton className="h-12 w-full" />
                    </div>
                )}
                {items && items.length === 0 && (
                    <EmptyState
                        title={filtered ? "Nothing matches these filters" : "Nothing has happened here yet"}
                        description={filtered ? "Change or clear the filters." : "Finished, failed and cancelled work will be listed here with what it left behind."}
                    />
                )}
                {items && items.length > 0 && (
                    <ul className="flex flex-col divide-y divide-border" data-testid="activity-list">
                        {items.map((item) => (
                            <li key={`${item.kind}-${item.id}`}>
                                <Link
                                    href={`/tasks/activity/${item.kind}/${item.id}${query ? `?${query}` : ""}`}
                                    className="motion-m1 flex min-h-11 flex-col gap-1 px-2 py-2 hover:bg-muted/60 sm:flex-row sm:items-center sm:justify-between sm:gap-3"
                                >
                                    <span className="min-w-0">
                                        <span className="block break-words text-sm font-medium">{item.title}</span>
                                        <span className="block text-xs text-muted-foreground">
                                            {item.helper} · {when(item.at)}
                                        </span>
                                        {item.evidence && <span className="block break-words text-xs text-muted-foreground">{item.evidence}</span>}
                                    </span>
                                    <TaskStatus state={item.state} />
                                </Link>
                            </li>
                        ))}
                    </ul>
                )}
            </div>
        </>
    );
}

export default ActivityList;
