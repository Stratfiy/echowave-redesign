"use client";

/** The person's own meetings in this workspace, newest first. */

import { Mic, Plus } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { listMeetingsApiV1MeetingsGet } from "@/client/sdk.gen";
import type { MeetingSummary } from "@/client/types.gen";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/shell/ErrorState";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { duration, statusLine } from "@/lib/meetings/format";

export function MeetingList() {
    const { user, loading: authLoading } = useAuth();
    const [rows, setRows] = useState<MeetingSummary[] | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setError(null);
        const response = await listMeetingsApiV1MeetingsGet();
        if (response.error) {
            setError(detailFromResult(response, "Could not load your meetings"));
            return;
        }
        setRows((response.data as { meetings: MeetingSummary[] }).meetings);
    }, []);

    const fetched = useRef(false);
    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void load();
    }, [authLoading, user, load]);

    return (
        <div className="mx-auto w-full max-w-[960px] px-4 py-6 md:px-6">
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="text-2xl font-semibold leading-8">Meetings</h1>
                    <p className="text-sm text-muted-foreground">Only you can see these.</p>
                </div>
                <Button asChild className="motion-m1 min-h-11">
                    <Link href="/meetings/new">
                        <Plus aria-hidden />
                        New meeting
                    </Link>
                </Button>
            </div>
            {error && <ErrorState title="Could not load your meetings" description={error} onRetry={() => void load()} />}
            {!error && rows === null && (
                <div aria-busy className="flex flex-col gap-2">
                    <Skeleton className="h-16 w-full" />
                    <Skeleton className="h-16 w-full" />
                </div>
            )}
            {!error && rows !== null && rows.length === 0 && (
                <EmptyState
                    icon={Mic}
                    title="No meetings yet"
                    description="Record one on this device, upload a recording, or paste notes."
                />
            )}
            {!error && rows !== null && rows.length > 0 && (
                <ul className="flex flex-col divide-y divide-border rounded-[8px] border border-border">
                    {rows.map((row) => {
                        const status = statusLine(row.status);
                        return (
                            <li key={row.id}>
                                <Link href={`/meetings/${row.id}`} className="motion-m1 flex min-h-16 flex-col gap-0.5 px-4 py-3 hover:bg-muted/50">
                                    <span className="break-words font-medium">{row.title}</span>
                                    <span className="text-sm text-muted-foreground">
                                        {status.label}
                                        {row.captured_ms > 0 && ` · ${duration(row.captured_ms)}`}
                                        {row.created_at &&
                                            ` · ${new Date(row.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`}
                                    </span>
                                </Link>
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
}

export default MeetingList;
