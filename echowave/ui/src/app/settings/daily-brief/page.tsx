"use client";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { BriefSettings } from "@/components/today/BriefSettings";
import { NotOn } from "@/components/today/NotOn";
import { useFeature } from "@/lib/features";

/** Screen 20: the person's own daily brief (and end-of-day note). */
export default function DailyBriefSettingsPage() {
    if (!useFeature("daily_brief")) return <NotOn what="The daily brief" />;
    return (
        <>
            <PageHeader title="Daily brief" description="One short brief a day, at your time, with every source it checked." />
            <PageBody className="max-w-3xl">
                <BriefSettings />
            </PageBody>
        </>
    );
}
