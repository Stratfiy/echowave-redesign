"use client";

/**
 * Trackers the builder made ("ask Decibyl to build anything"): a list of
 * them, and one tracker with its latest rows and a form to add a row. New
 * trackers come from Chat, where a card shows the columns before anything
 * exists.
 */

import { ArrowLeft, Loader2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
    addTrackerRowApiV1HelpersTrackersTrackerUuidRowsPost,
    getTrackerApiV1HelpersTrackersTrackerUuidGet,
    listTrackersApiV1HelpersTrackersGet,
} from "@/client/sdk.gen";
import type { TrackerDetail, TrackerOut } from "@/client/types.gen";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/shell/ErrorState";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

type Load<T> = { state: "loading" } | { state: "failed"; message: string } | { state: "ready"; data: T };

const INPUT_TYPE: Record<string, string> = { date: "date", number: "number", money: "text", yes_no: "text", text: "text" };

export function TrackerList() {
    const { user, loading: authLoading } = useAuth();
    const [load, setLoad] = useState<Load<TrackerOut[]>>({ state: "loading" });
    const fetchList = useCallback(async () => {
        const response = await listTrackersApiV1HelpersTrackersGet();
        if (response.error || !response.data) {
            setLoad({ state: "failed", message: detailFromResult(response, "Trackers did not load.") });
            return;
        }
        setLoad({ state: "ready", data: response.data });
    }, []);
    useEffect(() => {
        if (authLoading || !user) return;
        void fetchList();
    }, [authLoading, user, fetchList]);
    if (load.state === "loading") return <p className="px-4 py-8 text-sm text-muted-foreground" role="status">Loading trackers…</p>;
    if (load.state === "failed") return <ErrorState title="Trackers did not load" description={load.message} onRetry={() => void fetchList()} />;
    if (!load.data.length) {
        return (
            <EmptyState
                title="No trackers yet."
                description="Describe one in Chat, such as “track my client visits”, and confirm the card."
                action={
                    <Link className="underline" href="/overview?helper=builder">
                        Build one in Chat
                    </Link>
                }
            />
        );
    }
    return (
        <ul className="mx-auto w-full max-w-[760px] divide-y divide-border px-4 sm:px-6" aria-label="Trackers">
            {load.data.map((t) => (
                <li key={t.uuid}>
                    <Link href={`/trackers/${t.uuid}`} className="flex min-h-11 flex-col py-3 hover:underline">
                        <span className="font-medium break-words">{t.name}</span>
                        <span className="text-xs text-muted-foreground break-words">
                            {t.columns.map((c) => c.name).join(", ")} · {t.visibility === "workspace" ? "Shared" : "Private"}
                        </span>
                    </Link>
                </li>
            ))}
        </ul>
    );
}

export function TrackerView({ uuid }: { uuid: string }) {
    const { user, loading: authLoading } = useAuth();
    const [load, setLoad] = useState<Load<TrackerDetail>>({ state: "loading" });
    const [values, setValues] = useState<Record<string, string>>({});
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const fetchOne = useCallback(async () => {
        const response = await getTrackerApiV1HelpersTrackersTrackerUuidGet({ path: { tracker_uuid: uuid } });
        if (response.error || !response.data) {
            setLoad({ state: "failed", message: detailFromResult(response, "The tracker did not load.") });
            return;
        }
        setLoad({ state: "ready", data: response.data });
    }, [uuid]);
    useEffect(() => {
        if (authLoading || !user) return;
        void fetchOne();
    }, [authLoading, user, fetchOne]);
    if (load.state === "loading") return <p className="px-4 py-8 text-sm text-muted-foreground" role="status">Loading…</p>;
    if (load.state === "failed") return <ErrorState title="The tracker did not load" description={load.message} onRetry={() => void fetchOne()} />;
    const tracker = load.data;
    return (
        <div className="mx-auto w-full max-w-[760px] space-y-4 px-4 py-4 sm:px-6">
            <Link href="/trackers" className="inline-flex min-h-11 items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
                <ArrowLeft aria-hidden className="h-4 w-4" /> Trackers
            </Link>
            <h1 className="text-2xl font-semibold break-words">{tracker.name}</h1>
            <form
                className="grid gap-2 rounded-md border border-border p-3 sm:grid-cols-2"
                onSubmit={async (event) => {
                    event.preventDefault();
                    setSaving(true);
                    setError(null);
                    const response = await addTrackerRowApiV1HelpersTrackersTrackerUuidRowsPost({
                        path: { tracker_uuid: uuid },
                        body: { values },
                    });
                    setSaving(false);
                    if (response.error || !response.data) {
                        setError(detailFromResult(response, "The row was not added. Try again."));
                        return;
                    }
                    setValues({});
                    setLoad({ state: "ready", data: response.data });
                }}
            >
                {tracker.columns.map((c) => (
                    <label key={c.name} className="text-sm">
                        {c.name}
                        <input
                            className="min-h-11 w-full rounded-md border border-border bg-background px-3 text-base md:text-sm"
                            type={INPUT_TYPE[c.type] ?? "text"}
                            value={values[c.name] ?? ""}
                            onChange={(e) => setValues({ ...values, [c.name]: e.target.value })}
                        />
                    </label>
                ))}
                {error && (
                    <p className="text-sm text-destructive sm:col-span-2" role="status">
                        {error}
                    </p>
                )}
                <Button type="submit" className="min-h-11 sm:col-span-2 sm:w-fit" disabled={saving}>
                    {saving && <Loader2 aria-hidden className="h-4 w-4 animate-spin" />} Add row
                </Button>
            </form>
            {tracker.rows.length === 0 ? (
                <EmptyState title="Nothing in it yet." description="Add a row above, or tell Decibyl in Chat." />
            ) : (
                <ul className="divide-y divide-border" aria-label={`${tracker.name} rows`}>
                    {tracker.rows.map((row) => (
                        <li key={row.id} className="py-3 text-sm">
                            <dl className="grid grid-cols-[minmax(0,8rem)_1fr] gap-x-3 gap-y-1">
                                {tracker.columns.map((c) => (
                                    <div key={c.name} className="contents">
                                        <dt className="text-muted-foreground break-words">{c.name}</dt>
                                        <dd className="break-words">{String(row.values[c.name] ?? "—")}</dd>
                                    </div>
                                ))}
                            </dl>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
