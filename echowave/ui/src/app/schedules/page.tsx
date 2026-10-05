/** The routines: what the agents do on a clock, a tab beside Tasks. */

"use client";

import { SchedulesBoard } from "@/components/desk/SchedulesBoard";
import { DESK_TABS } from "@/components/layout/SectionTabs";

export default function SchedulesPage() {
    return <SchedulesBoard tabs={DESK_TABS} />;
}
