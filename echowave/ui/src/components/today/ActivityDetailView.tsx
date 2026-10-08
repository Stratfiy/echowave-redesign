"use client";

/**
 * Screen 09, one item's detail: goal, owner and scope, the stages it went
 * through (a vertical timeline with words, not colours), what it was given,
 * the evidence it left, and what it relates to. Cancel stops future work and
 * says what already happened; Retry is never offered for an act whose
 * outcome is unknown -- Check delivery reads the authoritative state again.
 * Leaving the page does not stop background work, and the screen says so.
 */

import { RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
    activityDetailApiV1TodayActivityKindItemIdGet,
    moveLedgerTaskApiV1TasksTaskIdLedgerTransitionPost,
    settleActionApiV1TimelineActionsSettlePost,
} from "@/client/sdk.gen";
import { ErrorState, TaskStatus } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import type { ActivityDetail } from "@/lib/today/types";

function when(at: string | null): string {
    if (!at) return "";
    return new Date(at).toLocaleString(undefined, {
        weekday: "short",
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        timeZoneName: "short",
    });
}

export function ActivityDetailView({ kind, id, onChanged }: { kind: string; id: number; onChanged?: () => void }) {
    const { user, loading: authLoading } = useAuth();
    // Keyed on signed-in, not on the user object, so a re-render never
    // refetches.
    const signedIn = Boolean(user);
    const [detail, setDetail] = useState<ActivityDetail | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState<string | null>(null);

    const load = useCallback(async () => {
        const result = await activityDetailApiV1TodayActivityKindItemIdGet({ path: { kind, item_id: id } });
        if (result.error) {
            setError(detailFromError(result.error, "This could not load."));
            return;
        }
        setError(null);
        setDetail(result.data as unknown as ActivityDetail);
    }, [kind, id]);

    useEffect(() => {
        if (authLoading || !signedIn) return;
        void load();
    }, [authLoading, signedIn, load]);

    async function cancel() {
        if (!detail) return;
        setBusy(true);
        const result =
            detail.kind === "card"
                ? await settleActionApiV1TimelineActionsSettlePost({ body: { event_id: detail.id, verb: "undo" } })
                : await moveLedgerTaskApiV1TasksTaskIdLedgerTransitionPost({
                      path: { task_id: detail.id },
                      body: { to: "cancelled", expected_version: Number(detail.version ?? 0), reason_code: "cancelled_from_today" },
                  });
        setBusy(false);
        if (result.error) {
            setNote(detailFromError(result.error, "It could not be cancelled."));
        } else {
            setNote("Cancelled. Anything already done stays done, and is listed above.");
            onChanged?.();
        }
        await load();
    }

    if (error && !detail) return <ErrorState title="This could not load." description={error} onRetry={() => void load()} />;
    if (!detail) {
        return (
            <div className="flex flex-col gap-2" aria-busy="true">
                <Skeleton className="h-6 w-1/2" />
                <Skeleton className="h-32 w-full" />
            </div>
        );
    }
    const inputs = Object.entries(detail.inputs ?? {}).filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && v.length === 0));
    return (
        <article className="flex flex-col gap-4" data-testid="activity-detail" aria-labelledby={`activity-${detail.kind}-${detail.id}`}>
            <header className="flex flex-col gap-2">
                <h2 id={`activity-${detail.kind}-${detail.id}`} className="break-words text-lg font-semibold leading-snug">
                    {detail.goal}
                </h2>
                <TaskStatus state={detail.state} evidence={detail.evidence} />
                <p className="text-sm text-muted-foreground">
                    {detail.owner} · {detail.scope}
                    {detail.due ? ` · Due ${detail.due}` : ""}
                </p>
            </header>

            <section aria-label="What happened">
                <h3 className="mb-2 text-sm font-semibold">What happened</h3>
                <ol className="relative flex flex-col gap-3 border-l border-border pl-4">
                    {detail.stages.map((stage, i) => (
                        <li key={`${stage.label}-${i}`} className="text-sm">
                            <span className="absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full border border-border bg-background" aria-hidden />
                            <span className="font-medium">{stage.label}</span>
                            {stage.at && <span className="block text-xs text-muted-foreground">{when(stage.at)}</span>}
                            {stage.reason_code && <span className="block text-xs text-muted-foreground">Reason: {stage.reason_code.replace(/_/g, " ")}</span>}
                        </li>
                    ))}
                </ol>
            </section>

            {inputs.length > 0 && (
                <section aria-label="What it was given">
                    <h3 className="mb-2 text-sm font-semibold">What it was given</h3>
                    <dl className="flex flex-col gap-1.5 text-sm">
                        {inputs.map(([key, value]) => (
                            <div key={key} className="grid grid-cols-[minmax(5.5rem,auto)_1fr] gap-x-3">
                                <dt className="capitalize text-muted-foreground">{key}</dt>
                                <dd className="min-w-0 whitespace-pre-wrap break-words">{Array.isArray(value) ? value.join(", ") : String(value)}</dd>
                            </div>
                        ))}
                    </dl>
                </section>
            )}

            {detail.related.length > 0 && (
                <section aria-label="Related">
                    <h3 className="mb-2 text-sm font-semibold">Related</h3>
                    <ul className="flex flex-col gap-1 text-sm">
                        {detail.related.map((r) => (
                            <li key={`${r.kind}-${r.id}`}>
                                <Link href={r.href} className="inline-flex min-h-11 items-center underline underline-offset-2 md:min-h-8">
                                    {r.title ?? `Open the ${r.kind}`}
                                </Link>
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            <div className="flex flex-wrap gap-2">
                {detail.can_cancel && (
                    <Button variant="outline" className="motion-m1 min-h-11 md:min-h-9" disabled={busy} onClick={() => void cancel()}>
                        Cancel what has not happened yet
                    </Button>
                )}
                {detail.check_delivery && (
                    <Button variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void load()}>
                        <RefreshCw aria-hidden className="h-4 w-4" />
                        Check delivery
                    </Button>
                )}
            </div>
            {note && (
                <p role="status" className="text-sm text-muted-foreground">
                    {note}
                </p>
            )}
            <p className="text-xs text-muted-foreground">Work carries on in the background if you leave this page.</p>
        </article>
    );
}

export default ActivityDetailView;
