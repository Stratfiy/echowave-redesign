/** The routines, on their own tab once the board is a board (TB-1). */

"use client";

import { SchedulesBoard } from "@/components/desk/SchedulesBoard";
import { deskTabs } from "@/components/layout/SectionTabs";

export default function SchedulesPage() {
    return <SchedulesBoard tabs={deskTabs(true)} />;
}
