"use client";

/**
 * One meeting record, kept fresh while something is still happening.
 *
 * Polls while the meeting is recording or processing, while its summary is
 * being written, or while a follow-up card is about to run -- with backoff,
 * and never while the tab is hidden. A failed refresh keeps the last good
 * record on screen and says it is stale (design, "Freshness"), rather than
 * blanking it.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { getMeetingApiV1MeetingsMeetingIdGet } from "@/client/sdk.gen";
import type { MeetingRecord } from "@/client/types.gen";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const BUSY = new Set(["recording", "paused", "uploading", "processing"]);
const CARD_BUSY = new Set(["armed", "running"]);

export function needsPolling(record: MeetingRecord | null): boolean {
    if (!record) return false;
    if (BUSY.has(record.status)) return true;
    if (record.reading_status === "reading") return true;
    return record.actions.some((action) => CARD_BUSY.has(action.card?.state ?? ""));
}

/** 2 s, growing to 10 s while nothing changes. */
export function nextDelay(previous: number, changed: boolean): number {
    if (changed) return 2000;
    return Math.min(10000, Math.round(previous * 1.5));
}

export function useMeeting(meetingId: string) {
    const { user, loading: authLoading } = useAuth();
    const [record, setRecord] = useState<MeetingRecord | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notFound, setNotFound] = useState(false);
    const [stale, setStale] = useState(false);
    const delay = useRef(2000);
    const lastRevision = useRef<number | null>(null);

    const load = useCallback(async (): Promise<MeetingRecord | null> => {
        const response = await getMeetingApiV1MeetingsMeetingIdGet({ path: { meeting_id: meetingId } });
        if (response.error) {
            if (response.response?.status === 404) {
                setNotFound(true);
                setRecord(null);
            } else if (lastRevision.current !== null) {
                setStale(true);
            } else {
                setError(detailFromResult(response, "Could not load this meeting"));
            }
            setLoading(false);
            return null;
        }
        const data = response.data as MeetingRecord;
        const changed = lastRevision.current !== data.revision;
        delay.current = nextDelay(delay.current, changed);
        lastRevision.current = data.revision;
        setRecord(data);
        setError(null);
        setStale(false);
        setLoading(false);
        return data;
    }, [meetingId]);

    const fetched = useRef(false);
    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void load();
    }, [authLoading, user, load]);

    useEffect(() => {
        if (!needsPolling(record)) return;
        const timer = window.setTimeout(() => {
            if (typeof document !== "undefined" && document.visibilityState === "hidden") {
                // Try again later without spending a request.
                setRecord((was) => (was ? { ...was } : was));
                return;
            }
            void load();
        }, delay.current);
        return () => window.clearTimeout(timer);
    }, [record, load]);

    /** Put a record the server just returned in place, without a fetch. */
    const accept = useCallback((data: MeetingRecord) => {
        delay.current = 2000;
        lastRevision.current = data.revision;
        setRecord(data);
        setStale(false);
    }, []);

    return { record, loading, error, notFound, stale, reload: load, accept };
}
