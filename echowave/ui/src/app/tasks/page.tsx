/**
 * The Tasks door. Until the board is a board (TB-1), it opens on the
 * schedules, as it always has. With the flag on, it opens on the kanban and
 * the schedules move to their own tab. The server says which, on the same
 * call that loads the board, so the page never guesses.
 */

"use client";

import { useEffect, useState } from "react";

import { listTasksApiV1TasksGet } from "@/client/sdk.gen";
import { SchedulesBoard } from "@/components/desk/SchedulesBoard";
import { type BoardPayload,TaskBoard } from "@/components/desk/TaskBoard";
import { deskTabs } from "@/components/layout/SectionTabs";
import SpinLoader from "@/components/SpinLoader";
import { useAuth } from "@/lib/auth";

export default function TasksPage() {
    const { user, loading: authLoading } = useAuth();
    const [payload, setPayload] = useState<BoardPayload | null | undefined>(undefined);

    useEffect(() => {
        if (authLoading || !user) return;
        let cancelled = false;
        void listTasksApiV1TasksGet().then((result) => {
            if (cancelled) return;
            setPayload(result.error ? null : ((result.data as BoardPayload | undefined) ?? null));
        });
        return () => {
            cancelled = true;
        };
    }, [authLoading, user]);

    if (authLoading || payload === undefined) return <SpinLoader />;
    if (payload?.board?.enabled) return <TaskBoard initial={payload} tabs={deskTabs(true)} />;
    return <SchedulesBoard />;
}
