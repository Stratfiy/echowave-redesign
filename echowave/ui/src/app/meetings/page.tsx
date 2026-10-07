"use client";

import { MeetingList } from "@/components/meetings/MeetingList";
import { MeetingsGate } from "@/components/meetings/MeetingsGate";

/** Saved meeting records, the person's own. */
export default function MeetingsPage() {
    return (
        <MeetingsGate>
            <MeetingList />
        </MeetingsGate>
    );
}
