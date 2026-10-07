"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { MeetingCapture } from "@/components/meetings/MeetingCapture";
import { MeetingsGate } from "@/components/meetings/MeetingsGate";
import SpinLoader from "@/components/SpinLoader";

/** Screen 11: meeting capture, opened from Chat's Attach -> Meeting mode. */
function Capture() {
    const thread = useSearchParams().get("thread");
    return <MeetingCapture threadId={thread} />;
}

export default function NewMeetingPage() {
    return (
        <MeetingsGate>
            <Suspense fallback={<SpinLoader />}>
                <Capture />
            </Suspense>
        </MeetingsGate>
    );
}
