"use client";

/**
 * My medicine reminders (launch stream `care`).
 *
 * Decibyl rings the person at the times they choose and names the medicine
 * the way they wrote it -- a reminder, never advice about doses. Setting one
 * up shows the card with the exact number, times, language and who is told
 * if a call is missed; nothing rings until the person confirms it here.
 *
 * Honest states: "needs setup" for phone calls when this workspace has no
 * phone line, with the way past right there -- a reminder in Decibyl, which
 * needs no number, and a link to set up a line -- and "test mode" when calls
 * are simulated.
 */

import { Loader2, Pause, Pencil, Phone, Play, Plus, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
    addMedicineApiV1CareMedicinesPost,
    careCardApiV1CareCardsEventIdGet,
    editMedicineApiV1CareMedicinesMedicineIdPatch,
    markTakenApiV1CareMedicinesMedicineIdTakenPost,
    myCircleApiV1CareCircleGet,
    myMedicinesApiV1CareMedicinesGet,
    pauseMedicineApiV1CareMedicinesMedicineIdPausePost,
    removeMedicineApiV1CareMedicinesMedicineIdDelete,
    resumeMedicineApiV1CareMedicinesMedicineIdResumePost,
} from "@/client/sdk.gen";
import type { CircleMember, Medicine, MedicineList, TimelineEvent } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import { cn } from "@/lib/utils";

import { ConsentCard } from "./ConsentCard";
import { DOSE_WORDS, MEDICINE_STATE_WORDS } from "./copy";
import { SpeakButton } from "./SpeakButton";

const FIELD =
    "min-h-12 w-full rounded-lg border border-input bg-background px-3 text-base focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** A dose's time in the reminder's own timezone -- the time the person chose,
 *  whatever timezone this browser is in. */
export function localTime(iso: string, timeZone?: string): string {
    try {
        return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false, timeZone });
    } catch {
        return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
    }
}

function AddReminder({
    languages,
    family,
    callsReady,
    callsReason,
    onProposed,
}: {
    languages: Record<string, string>;
    family: CircleMember[];
    /** Whether a phone call can be placed here; a reminder in Decibyl always can. */
    callsReady: boolean;
    callsReason: string;
    onProposed: (card: TimelineEvent | null) => void;
}) {
    const [label, setLabel] = useState("");
    const [times, setTimes] = useState<string[]>(["08:00"]);
    // Where calls cannot be placed, the reminder comes in Decibyl: it needs
    // no phone number, so it works on any workspace.
    const [channel, setChannel] = useState<"app" | "call">(callsReady ? "call" : "app");
    const [phone, setPhone] = useState("");
    const [language, setLanguage] = useState("");
    const [tell, setTell] = useState<number[]>([]);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const told = family.filter((m) => (m.status === "active" || m.status === "invited") && m.shares.includes("medicine_alerts"));

    const save = async () => {
        setSaving(true);
        setError(null);
        const response = await addMedicineApiV1CareMedicinesPost({
            body: {
                label,
                times: times.filter(Boolean),
                channel,
                phone: channel === "call" ? phone : null,
                language: language || null,
                alert_member_ids: tell,
            },
        });
        setSaving(false);
        if (response.error || !response.data) {
            setError(detailFromResult(response, "The reminder was not saved. Try again."));
            return;
        }
        onProposed(response.data.card ?? null);
    };

    return (
        <form
            className="flex flex-col gap-4 rounded-xl border border-border p-4"
            onSubmit={(event) => {
                event.preventDefault();
                void save();
            }}
            data-testid="medicine-form"
        >
            <h3 className="text-lg font-semibold">Add a reminder</h3>
            <label className="flex flex-col gap-2">
                <span className="font-medium">Which medicine? Write it the way you call it.</span>
                <input className={FIELD} value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} placeholder="For example: BP tablet after breakfast" required />
            </label>
            <SpeakButton onText={(said) => setLabel(said.slice(0, 80))} />
            <TimesField times={times} setTimes={setTimes} />
            <fieldset className="flex flex-col gap-2">
                <legend className="mb-2 font-medium">How should Decibyl remind you?</legend>
                <label className="flex min-h-11 items-start gap-3 py-1">
                    <input type="radio" name="reminder-channel" className="mt-1 h-5 w-5 shrink-0" checked={channel === "app"} onChange={() => setChannel("app")} />
                    <span className="text-base">In Decibyl, with I took it. No phone number needed.</span>
                </label>
                <label className="flex min-h-11 items-start gap-3 py-1">
                    <input
                        type="radio"
                        name="reminder-channel"
                        className="mt-1 h-5 w-5 shrink-0"
                        checked={channel === "call"}
                        disabled={!callsReady}
                        onChange={() => setChannel("call")}
                    />
                    <span className="text-base">
                        A phone call
                        {!callsReady && <span className="block text-sm text-muted-foreground">Not yet: {callsReason}</span>}
                    </span>
                </label>
            </fieldset>
            {channel === "call" && (
                <label className="flex flex-col gap-2">
                    <span className="font-medium">Which phone should ring?</span>
                    <input className={FIELD} type="tel" inputMode="tel" autoComplete="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="98765 43210" required />
                </label>
            )}
            <label className="flex flex-col gap-2">
                <span className="font-medium">In which language?</span>
                <select className={FIELD} value={language} onChange={(e) => setLanguage(e.target.value)}>
                    <option value="">My language (from my preferences)</option>
                    {Object.entries(languages).map(([tag, name]) => (
                        <option key={tag} value={tag}>
                            {name}
                        </option>
                    ))}
                </select>
            </label>
            {told.length > 0 && (
                <fieldset className="flex flex-col gap-2">
                    <legend className="mb-2 font-medium">If a call is missed, tell:</legend>
                    {told.map((member) => (
                        <label key={member.id} className="flex min-h-11 items-center gap-3">
                            <input
                                type="checkbox"
                                className="h-5 w-5"
                                checked={tell.includes(member.id)}
                                onChange={(e) => setTell((all) => (e.target.checked ? [...all, member.id] : all.filter((id) => id !== member.id)))}
                            />
                            <span className="text-base">{member.name}</span>
                        </label>
                    ))}
                </fieldset>
            )}
            <p className="text-sm text-muted-foreground">
                Decibyl only reminds. It never gives advice about doses; ask your doctor about those.
            </p>
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
            <Button type="submit" className="motion-m1 min-h-12 text-base" disabled={saving}>
                {saving && <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />}
                Review before it starts
            </Button>
        </form>
    );
}

function TimesField({ times, setTimes }: { times: string[]; setTimes: (update: (all: string[]) => string[]) => void }) {
    return (
        <fieldset className="flex flex-col gap-2">
            <legend className="mb-2 font-medium">At what times?</legend>
            {times.map((time, index) => (
                <div key={index} className="flex items-center gap-2">
                    <input
                        type="time"
                        className={cn(FIELD, "max-w-[12rem]")}
                        value={time}
                        aria-label={`Time ${index + 1}`}
                        onChange={(e) => setTimes((all) => all.map((t, i) => (i === index ? e.target.value : t)))}
                        required
                    />
                    {times.length > 1 && (
                        <Button
                            type="button"
                            variant="ghost"
                            className="min-h-11 min-w-11"
                            aria-label={`Remove time ${index + 1}`}
                            onClick={() => setTimes((all) => all.filter((_, i) => i !== index))}
                        >
                            <X aria-hidden className="h-4 w-4" />
                        </Button>
                    )}
                </div>
            ))}
            {times.length < 6 && (
                <Button type="button" variant="outline" className="min-h-11 self-start gap-2" onClick={() => setTimes((all) => [...all, ""])}>
                    <Plus aria-hidden className="h-4 w-4" />
                    Add another time
                </Button>
            )}
        </fieldset>
    );
}

/** Change the name, times or language. The number is not here: a different
 *  phone is a new reminder, with its own card. A running reminder stops
 *  until the card with the new details is confirmed -- the card said so. */
function EditReminder({
    medicine,
    languages,
    onDone,
    onCancel,
}: {
    medicine: Medicine;
    languages: Record<string, string>;
    onDone: (card: TimelineEvent | null) => void;
    onCancel: () => void;
}) {
    const [label, setLabel] = useState(medicine.label);
    const [times, setTimes] = useState<string[]>(medicine.times.length ? medicine.times : ["08:00"]);
    const [language, setLanguage] = useState(medicine.language);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const running = medicine.state === "active" || medicine.state === "awaiting_approval";

    const save = async () => {
        setSaving(true);
        setError(null);
        const response = await editMedicineApiV1CareMedicinesMedicineIdPatch({
            path: { medicine_id: medicine.id },
            body: { label, times: times.filter(Boolean), language },
        });
        setSaving(false);
        if (response.error || !response.data) {
            setError(detailFromResult(response, "The change was not saved. Try again."));
            return;
        }
        onDone(response.data.card ?? null);
    };

    return (
        <form
            className="flex flex-col gap-4"
            onSubmit={(event) => {
                event.preventDefault();
                void save();
            }}
            data-testid="medicine-edit"
        >
            <label className="flex flex-col gap-2">
                <span className="font-medium">Which medicine?</span>
                <input className={FIELD} value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} required />
            </label>
            <TimesField times={times} setTimes={setTimes} />
            {medicine.channel !== "app" && (
                <label className="flex flex-col gap-2">
                    <span className="font-medium">In which language?</span>
                    <select className={FIELD} value={language} onChange={(e) => setLanguage(e.target.value)}>
                        {Object.entries(languages).map(([tag, name]) => (
                            <option key={tag} value={tag}>
                                {name}
                            </option>
                        ))}
                    </select>
                </label>
            )}
            {running && (
                <p className="text-sm text-muted-foreground">
                    {medicine.channel === "app" ? "The reminders" : "The calls"} stop until you confirm the new details.
                </p>
            )}
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
            <div className="flex flex-wrap gap-2">
                <Button type="submit" className="motion-m1 min-h-11" disabled={saving}>
                    {saving && <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />}
                    {running ? "Save and review" : "Save"}
                </Button>
                <Button type="button" variant="ghost" className="min-h-11" onClick={onCancel} disabled={saving}>
                    Cancel
                </Button>
            </div>
        </form>
    );
}

function MedicineRow({ medicine, languages, onChanged }: { medicine: Medicine; languages: Record<string, string>; onChanged: (card?: TimelineEvent | null) => void }) {
    const [busy, setBusy] = useState(false);
    const [editing, setEditing] = useState(false);
    const [confirmRemove, setConfirmRemove] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const remove = async () => {
        setBusy(true);
        setError(null);
        const response = await removeMedicineApiV1CareMedicinesMedicineIdDelete({ path: { medicine_id: medicine.id } });
        setBusy(false);
        if (response.error) {
            setError(detailFromResult(response, "It was not removed. Try again."));
            return;
        }
        onChanged();
    };
    const act = async (verb: "pause" | "resume") => {
        setBusy(true);
        setError(null);
        const response =
            verb === "pause"
                ? await pauseMedicineApiV1CareMedicinesMedicineIdPausePost({ path: { medicine_id: medicine.id } })
                : await resumeMedicineApiV1CareMedicinesMedicineIdResumePost({ path: { medicine_id: medicine.id } });
        setBusy(false);
        if (response.error) {
            setError(detailFromResult(response, "That did not work. Try again."));
            return;
        }
        onChanged(verb === "resume" ? ((response.data as { card?: TimelineEvent | null })?.card ?? null) : undefined);
    };
    const took = async (dueAt: string) => {
        setBusy(true);
        const response = await markTakenApiV1CareMedicinesMedicineIdTakenPost({
            path: { medicine_id: medicine.id },
            body: { due_at: dueAt },
        });
        setBusy(false);
        if (response.error) {
            setError(detailFromResult(response, "That was not saved. Try again."));
            return;
        }
        onChanged();
    };
    return (
        <li className="flex flex-col gap-3 rounded-xl border border-border p-4" data-testid="medicine-row">
            <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                    <p className="break-words text-lg font-semibold">{medicine.label}</p>
                    <p className="text-sm text-muted-foreground">
                        {medicine.times.join(", ")} · {medicine.channel === "app" ? "in Decibyl" : `${medicine.language_name} · ${medicine.phone_masked ?? ""}`}
                    </p>
                </div>
                <span className="rounded-full border border-border px-3 py-1 text-sm">{MEDICINE_STATE_WORDS[medicine.state] ?? medicine.state}</span>
            </div>
            {(medicine.doses ?? []).length > 0 && (
                <ul className="flex flex-col gap-2" aria-label="Today's calls">
                    {(medicine.doses ?? []).map((dose) => (
                        <li key={dose.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted/40 px-3 py-2">
                            <span>
                                {localTime(dose.due_at, medicine.timezone)}: <strong>{medicine.channel === "app" && dose.state === "not_answered" ? "Not confirmed" : (DOSE_WORDS[dose.state] ?? dose.state)}</strong>
                                {dose.alerted ? " · family told" : ""}
                            </span>
                            {dose.state !== "taken" && (
                                <Button type="button" variant="outline" className="min-h-11" disabled={busy} onClick={() => void took(dose.due_at)}>
                                    I took it
                                </Button>
                            )}
                        </li>
                    ))}
                </ul>
            )}
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
            {editing ? (
                <EditReminder
                    medicine={medicine}
                    languages={languages}
                    onCancel={() => setEditing(false)}
                    onDone={(card) => {
                        setEditing(false);
                        onChanged(card);
                    }}
                />
            ) : confirmRemove ? (
                <div role="group" aria-label="Remove this reminder" className="flex flex-wrap items-center gap-2">
                    <span className="text-base">Remove {medicine.label}? {medicine.channel === "app" ? "The reminders" : "The calls"} stop now.</span>
                    <Button type="button" variant="destructive" className="min-h-11" disabled={busy} onClick={() => void remove()}>
                        Yes, remove
                    </Button>
                    <Button type="button" variant="ghost" className="min-h-11" disabled={busy} onClick={() => setConfirmRemove(false)}>
                        Keep it
                    </Button>
                </div>
            ) : (
                <div className="flex flex-wrap gap-2">
                    <Button type="button" variant="outline" className="min-h-11 gap-2" disabled={busy} onClick={() => setEditing(true)}>
                        <Pencil aria-hidden className="h-4 w-4" />
                        Edit
                    </Button>
                    <Button type="button" variant="ghost" className="min-h-11 gap-2" disabled={busy} onClick={() => setConfirmRemove(true)}>
                        <Trash2 aria-hidden className="h-4 w-4" />
                        Remove
                    </Button>
                </div>
            )}
            {!editing && !confirmRemove && medicine.state === "active" && (
                <Button type="button" variant="outline" className="min-h-11 self-start gap-2" disabled={busy} onClick={() => void act("pause")}>
                    <Pause aria-hidden className="h-4 w-4" />
                    {medicine.channel === "app" ? "Pause the reminders" : "Pause the calls"}
                </Button>
            )}
            {!editing && !confirmRemove && medicine.state === "paused" && (
                <Button type="button" variant="outline" className="min-h-11 self-start gap-2" disabled={busy} onClick={() => void act("resume")}>
                    <Play aria-hidden className="h-4 w-4" />
                    {medicine.channel === "app" ? "Start the reminders again" : "Start the calls again"}
                </Button>
            )}
        </li>
    );
}

export function MedicinesPanel() {
    const { user, loading: authLoading } = useAuth();
    const signedIn = Boolean(user);
    const circleOn = useFeature("care_family_circle");
    const [data, setData] = useState<MedicineList | null>(null);
    const [family, setFamily] = useState<CircleMember[]>([]);
    const [cards, setCards] = useState<TimelineEvent[]>([]);
    // Cards for starting a paused reminder again: the reminder stays paused
    // until confirmed, so the list above does not find them on its own.
    const [resumeCards, setResumeCards] = useState<TimelineEvent[]>([]);
    const [adding, setAdding] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        const response = await myMedicinesApiV1CareMedicinesGet();
        if (response.error || !response.data) {
            setError(detailFromResult(response, "Could not load your reminders."));
            return;
        }
        setError(null);
        setData(response.data);
        // Cards still waiting for the person's OK, shown here to answer.
        const waiting = response.data.medicines.filter((m) => m.state === "awaiting_approval" && m.card_event_id);
        const found: TimelineEvent[] = [];
        for (const medicine of waiting) {
            const card = await careCardApiV1CareCardsEventIdGet({ path: { event_id: medicine.card_event_id as number } });
            if (card.data) found.push(card.data);
        }
        setCards(found);
    }, []);

    useEffect(() => {
        if (authLoading || !signedIn) return;
        void load();
        if (circleOn) {
            void (async () => {
                const circle = await myCircleApiV1CareCircleGet();
                if (circle.data) setFamily(circle.data.members);
            })();
        }
    }, [authLoading, signedIn, circleOn, load]);

    if (error && !data) {
        return (
            <p role="alert" className="text-destructive">
                {error}
            </p>
        );
    }
    if (!data) return <p className="text-muted-foreground">Loading your reminders…</p>;

    const needsSetup = data.calls.state === "needs_setup";
    return (
        <section className="flex flex-col gap-5" data-testid="medicines">
            {needsSetup && (
                <div role="status" className="flex flex-col gap-2 rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-50" data-testid="calls-needs-setup">
                    <p className="font-semibold">Phone calls need setting up</p>
                    <p>{data.calls.reason}</p>
                    <p>
                        Until then: {data.app.reason} No phone number is needed.
                    </p>
                    <Link href="/settings/phone-number" className="min-h-11 self-start py-2 font-medium underline underline-offset-4">
                        Set up a phone line for calls
                    </Link>
                </div>
            )}
            {data.calls.state === "test_mode" && (
                <div role="status" className="rounded-xl border border-border bg-muted/40 p-4" data-testid="calls-test-mode">
                    <p className="font-semibold">Test mode</p>
                    <p>{data.calls.reason}</p>
                </div>
            )}
            {[...cards, ...resumeCards.filter((r) => !cards.some((c) => c.id === r.id))].map((card) => (
                <ConsentCard key={card.id} card={card} onChanged={() => void load()} />
            ))}
            {data.medicines.length === 0 ? (
                <p className="text-lg">No reminders yet.</p>
            ) : (
                <ul className="flex flex-col gap-3">
                    {data.medicines
                        .filter((m) => m.state !== "declined")
                        .map((medicine) => (
                            <MedicineRow
                                key={medicine.id}
                                medicine={medicine}
                                languages={data.languages}
                                onChanged={(card) => {
                                    if (card) setResumeCards((all) => [...all, card]);
                                    void load();
                                }}
                            />
                        ))}
                </ul>
            )}
            {adding ? (
                    <AddReminder
                        languages={data.languages}
                        family={family}
                        callsReady={!needsSetup}
                        callsReason={data.calls.reason}
                        onProposed={() => {
                            setAdding(false);
                            void load();
                        }}
                    />
                ) : (
                    <Button type="button" className="motion-m1 min-h-12 gap-2 self-start px-6 text-base" onClick={() => setAdding(true)}>
                        <Phone aria-hidden className="h-4 w-4" />
                        Add a reminder
                    </Button>
                )}
        </section>
    );
}

export default MedicinesPanel;
