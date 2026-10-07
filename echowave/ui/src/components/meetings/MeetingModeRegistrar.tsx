"use client";

/**
 * Fills the shell's meeting-mode hook (lib/shell/chatEntryPoints.ts).
 *
 * While `meeting_capture` is on, Attach -> Meeting mode in Chat opens meeting
 * capture (screen 11), carrying the conversation it came from so "Return to
 * chat" goes back there. While it is off nothing registers, and the composer
 * keeps saying "Not available yet" -- never a button that does nothing.
 */

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useFeature } from "@/lib/features";
import { registerMeetingMode } from "@/lib/shell/chatEntryPoints";

export function meetingCaptureHref(threadId: string | null): string {
    return threadId ? `/meetings/new?thread=${encodeURIComponent(threadId)}` : "/meetings/new";
}

export function MeetingModeRegistrar() {
    const on = useFeature("meeting_capture");
    const router = useRouter();
    useEffect(() => {
        if (!on) return;
        return registerMeetingMode(({ threadId }) => router.push(meetingCaptureHref(threadId)));
    }, [on, router]);
    return null;
}

export default MeetingModeRegistrar;
