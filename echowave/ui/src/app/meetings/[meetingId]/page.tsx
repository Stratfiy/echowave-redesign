"use client";

import { useParams } from "next/navigation";

import { MeetingRecordView } from "@/components/meetings/MeetingRecordView";
import { MeetingsGate } from "@/components/meetings/MeetingsGate";

/** Screen 12: one meeting record and its actions. */
export default function MeetingPage() {
    const { meetingId } = useParams<{ meetingId: string }>();
    return (
        <MeetingsGate>
            <MeetingRecordView meetingId={meetingId} />
        </MeetingsGate>
    );
}
