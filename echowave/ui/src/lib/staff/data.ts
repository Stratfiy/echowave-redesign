"use client";

/**
 * Reading the staff console's data with honest states (design "Freshness":
 * loading, empty, stale, partial and failed are distinct).
 *
 * - `loading`: nothing known yet.
 * - `ok`: the last read worked; `refreshedAt` says when.
 * - `stale`: a refresh failed after an earlier success; the old values stay
 *   on screen with the failure beside them, never replaced by blanks.
 * - `failed`: nothing was ever read; the reason is shown, never an empty
 *   table that reads as "no activity".
 * - `needs_setup`: the route is not there (404) -- a stream that has not
 *   landed, or a flag that is off -- which is not the same as failing.
 * - `denied`: 403; the role does not include this.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { client } from "@/client/client.gen";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

export type LoadState = "loading" | "ok" | "stale" | "failed" | "needs_setup" | "denied";

export type Loaded<T> = {
    state: LoadState;
    data: T | null;
    error: string | null;
    refreshedAt: Date | null;
    refresh: () => Promise<void>;
};

export type StaffResult<T> =
    | { ok: true; data: T; status: number }
    | { ok: false; status: number; error: string };

export async function staffGet<T>(url: string, query?: Record<string, unknown>): Promise<StaffResult<T>> {
    const result = await client.get({ url, query: query as never });
    const status = result.response?.status ?? 0;
    if (result.error !== undefined || status >= 400 || status === 0) {
        return { ok: false, status, error: detailFromResult(result, "Could not load this.") };
    }
    return { ok: true, data: result.data as T, status };
}

export async function staffPost<T>(url: string, body: unknown): Promise<StaffResult<T>> {
    const result = await client.post({ url, body: body as never, headers: { "Content-Type": "application/json" } });
    const status = result.response?.status ?? 0;
    if (result.error !== undefined || status >= 400 || status === 0) {
        return { ok: false, status, error: detailFromResult(result, "The request was not accepted.") };
    }
    return { ok: true, data: result.data as T, status };
}

export function stateFor(status: number, hadData: boolean): LoadState {
    if (status === 404) return "needs_setup";
    if (status === 403) return "denied";
    return hadData ? "stale" : "failed";
}

/** Read `url` once the person is signed in, again on `refresh`, and every
 *  `pollMs` if given. Keeps the last good data across a failed refresh. */
export function useStaffData<T>(
    url: string | null,
    query?: Record<string, unknown>,
    pollMs?: number,
): Loaded<T> {
    const { user, loading: authLoading } = useAuth();
    const [state, setState] = useState<LoadState>("loading");
    const [data, setData] = useState<T | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);
    const dataRef = useRef<T | null>(null);
    const key = JSON.stringify(query ?? {});

    const refresh = useCallback(async () => {
        if (!url) return;
        const outcome = await staffGet<T>(url, query);
        if (outcome.ok) {
            dataRef.current = outcome.data;
            setData(outcome.data);
            setError(null);
            setState("ok");
            setRefreshedAt(new Date());
        } else {
            setError(outcome.error);
            setState(stateFor(outcome.status, dataRef.current !== null));
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [url, key]);

    useEffect(() => {
        if (authLoading || !user || !url) return;
        void refresh();
        if (!pollMs) return;
        const timer = window.setInterval(() => void refresh(), pollMs);
        return () => window.clearInterval(timer);
    }, [authLoading, user, url, refresh, pollMs]);

    return { state, data, error, refreshedAt, refresh };
}
