"use client";

/**
 * People I look after (launch stream `care`): the family member's side.
 *
 * Join with the code the older person gave you, then see only what they
 * chose to share: alerts, their medicine reminders and today's calls, and
 * when they checked something for a scam (never what it said). A kind they
 * did not share is not shown at all -- not as an empty list, since "nothing
 * missed" would be a claim this screen cannot make.
 */

import { Bell, Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    acceptInviteApiV1CareFamilyAcceptPost,
    familyApiV1CareFamilyGet,
    readAlertApiV1CareFamilyAlertsAlertIdReadPost,
} from "@/client/sdk.gen";
import type { CaredFor } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

import { DOSE_WORDS, SHARE_LABELS } from "./copy";
import { localTime } from "./MedicinesPanel";

const VERDICT_WORDS: Record<string, string> = {
    likely_scam: "Looked like a scam",
    be_careful: "Had warning signs",
    no_signs_found: "No warning signs found",
};

function when(iso: string): string {
    return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function Person({ person, onRead }: { person: CaredFor; onRead: (id: number) => void }) {
    return (
        <li className="flex flex-col gap-4 rounded-xl border border-border p-4" data-testid="cared-for">
            <div>
                <p className="text-lg font-semibold">{person.person}</p>
                <p className="text-sm text-muted-foreground">Shares with you: {person.shares.map((s) => SHARE_LABELS[s] ?? s).join("; ") || "nothing right now"}</p>
            </div>
            {person.alerts.length > 0 && (
                <div>
                    <h4 className="mb-2 font-semibold">Alerts</h4>
                    <ul className="flex flex-col gap-2">
                        {person.alerts.map((alert) => (
                            <li key={alert.id} className={cn("flex flex-wrap items-start justify-between gap-2 rounded-lg border p-3", alert.read ? "border-border" : "border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40")}>
                                <span className="min-w-0 flex-1">
                                    <span className="block">{alert.title}</span>
                                    <span className="text-sm text-muted-foreground">{when(alert.at)}</span>
                                </span>
                                {!alert.read && (
                                    <Button type="button" variant="outline" className="min-h-11" onClick={() => onRead(alert.id)}>
                                        Seen
                                    </Button>
                                )}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
            {person.medicines && (
                <div>
                    <h4 className="mb-2 font-semibold">Medicine reminders</h4>
                    {person.medicines.length === 0 ? (
                        <p className="text-sm text-muted-foreground">No reminders are on.</p>
                    ) : (
                        <ul className="flex flex-col gap-2">
                            {person.medicines.map((medicine) => (
                                <li key={medicine.id} className="rounded-lg bg-muted/40 p-3">
                                    <p className="font-medium">
                                        {medicine.label} · {medicine.times.join(", ")}
                                        {medicine.state === "paused" ? " · paused" : ""}
                                    </p>
                                    {medicine.doses.length > 0 && (
                                        <ul className="mt-1 text-sm">
                                            {medicine.doses.map((dose) => (
                                                <li key={dose.due_at}>
                                                    {localTime(dose.due_at, medicine.timezone)}: {DOSE_WORDS[dose.state] ?? dose.state}
                                                </li>
                                            ))}
                                        </ul>
                                    )}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}
            {person.scam_checks && (
                <div>
                    <h4 className="mb-2 font-semibold">Scam checks</h4>
                    {person.scam_checks.length === 0 ? (
                        <p className="text-sm text-muted-foreground">No checks in the last two weeks.</p>
                    ) : (
                        <ul className="flex flex-col gap-1 text-sm">
                            {person.scam_checks.map((check, index) => (
                                <li key={`${check.at}-${index}`}>
                                    {when(check.at)}: a {check.kind}, {VERDICT_WORDS[check.verdict] ?? check.verdict}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}
        </li>
    );
}

export function FamilyPanel() {
    const { user, loading: authLoading } = useAuth();
    const signedIn = Boolean(user);
    const [people, setPeople] = useState<CaredFor[] | null>(null);
    const [code, setCode] = useState("");
    const [joining, setJoining] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [joined, setJoined] = useState<string | null>(null);

    const load = useCallback(async () => {
        const response = await familyApiV1CareFamilyGet();
        if (response.error || !response.data) {
            setError(detailFromResult(response, "Could not load the people you look after."));
            setPeople([]);
            return;
        }
        setPeople(response.data.people);
    }, []);

    useEffect(() => {
        if (authLoading || !signedIn) return;
        void load();
    }, [authLoading, signedIn, load]);

    const join = async () => {
        setJoining(true);
        setError(null);
        setJoined(null);
        const response = await acceptInviteApiV1CareFamilyAcceptPost({ body: { code } });
        setJoining(false);
        if (response.error || !response.data) {
            setError(detailFromResult(response, "That code did not work."));
            return;
        }
        setJoined(`You joined ${response.data.person}'s family circle.`);
        setCode("");
        void load();
    };

    const read = async (id: number) => {
        await readAlertApiV1CareFamilyAlertsAlertIdReadPost({ path: { alert_id: id } });
        void load();
    };

    return (
        <section className="flex flex-col gap-5" data-testid="family-view">
            <form
                className="flex flex-col gap-2"
                onSubmit={(e) => {
                    e.preventDefault();
                    void join();
                }}
            >
                <label className="flex flex-col gap-2">
                    <span className="font-medium">Have a code from someone in your family?</span>
                    <input
                        value={code}
                        onChange={(e) => setCode(e.target.value.toUpperCase())}
                        maxLength={16}
                        autoComplete="off"
                        className="min-h-12 w-full max-w-xs rounded-lg border border-input bg-background px-3 font-mono text-lg tracking-widest focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        placeholder="ABCD-EFGH"
                    />
                </label>
                <Button type="submit" className="min-h-12 self-start px-6 text-base" disabled={joining || code.trim().length < 6}>
                    {joining && <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />}
                    Join
                </Button>
            </form>
            {joined && <p role="status">{joined}</p>}
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
            {people === null ? (
                <p className="text-muted-foreground">Loading…</p>
            ) : people.length === 0 ? (
                <p className="flex items-center gap-2 text-base">
                    <Bell aria-hidden className="h-4 w-4" />
                    You are not in anyone&apos;s family circle yet.
                </p>
            ) : (
                <ul className="flex flex-col gap-3">
                    {people.map((person) => (
                        <Person key={person.member_id} person={person} onRead={(id) => void read(id)} />
                    ))}
                </ul>
            )}
        </section>
    );
}

export default FamilyPanel;
