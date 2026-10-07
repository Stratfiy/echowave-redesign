"use client";

import { Suspense } from "react";

import SpinLoader from "@/components/SpinLoader";
import { ActivityList } from "@/components/today/ActivityList";
import { NotOn } from "@/components/today/NotOn";
import { useFeature } from "@/lib/features";

/** Screen 09: Activity under Today. */
export default function ActivityPage() {
    if (!useFeature("today_list")) return <NotOn what="Activity" />;
    return (
        <Suspense fallback={<SpinLoader />}>
            <ActivityList />
        </Suspense>
    );
}
