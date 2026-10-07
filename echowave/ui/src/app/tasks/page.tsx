/**
 * The Tasks door: the work people and agents hand each other, on a board.
 * The full board (TB-1) when its flag is on, the simpler one (which was
 * /requests) until then. Schedules have their own tab either way. The server
 * says which board, on the same call that loads it, so the page never
 * guesses.
 */

"use client";

import { useEffect, useState } from "react";

import { listTasksApiV1TasksGet } from "@/client/sdk.gen";
import { SimpleTaskBoard } from "@/components/desk/SimpleTaskBoard";
import { type BoardPayload, TaskBoard } from "@/components/desk/TaskBoard";
import { DESK_TABS } from "@/components/layout/SectionTabs";
import { LearningToday } from "@/components/learning/LearningToday";
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
    // Learning reviews that are due sit above the work (learning_today);
    // the section draws nothing when there is nothing due.
    if (payload?.board?.enabled) {
        return (
            <>
                <LearningToday />
                <TaskBoard initial={payload} tabs={DESK_TABS} />
            </>
        );
    }
    return (
        <>
            <LearningToday />
            <SimpleTaskBoard tabs={DESK_TABS} />
        </>
    );
}
