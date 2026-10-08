/**
 * The Tasks door: the work people and agents hand each other, on a board.
 * The full board (TB-1) when its flag is on, the simpler one (which was
 * /requests) until then. Schedules have their own tab either way. The server
 * says which board, on the same call that loads it, so the page never
 * guesses.
 *
 * With `today_list` on (launch stream today, screen 07) this is Today: the
 * ordered list of what needs attention. Off, it is exactly the board above.
 */

"use client";

import { Suspense, useEffect, useState } from "react";

import { listTasksApiV1TasksGet } from "@/client/sdk.gen";
import { SimpleTaskBoard } from "@/components/desk/SimpleTaskBoard";
import { type BoardPayload, TaskBoard } from "@/components/desk/TaskBoard";
import { DESK_TABS } from "@/components/layout/SectionTabs";
import { LearningToday } from "@/components/learning/LearningToday";
import SpinLoader from "@/components/SpinLoader";
import { TodayPage } from "@/components/today/TodayPage";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";

function Board() {
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

export default function TasksPage() {
    const today = useFeature("today_list");
    if (today) {
        return (
            <Suspense fallback={<SpinLoader />}>
                <TodayPage />
            </Suspense>
        );
    }
    return <Board />;
}
