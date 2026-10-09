"use client";

/**
 * Screen 10, the reminder and event editor: purpose first, schedule next,
 * destination last, and the live next-occurrence sentence directly above
 * Save. Save sends back the exact schedule the person was shown (the
 * preview's key); a schedule that changed in between is refused, not
 * stored. "Tomorrow" is resolved in the person's timezone and shown as a
 * full date. An event's time and its reminders' offsets are separate
 * fields; moving the event later recalculates them (Today, Upcoming).
 *
 * With `reminder_calls` on, a new reminder can also be "Call me": the same
 * card Decibyl shows in chat (the number card first, with "I am 18 or
 * over", then the reminder card), answered right here. Nothing is set
 * until the card is confirmed.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
    askForReminderCallApiV1ReminderCallsAskPost,
    createEventApiV1TodayEventsPost,
    createReminderApiV1TodayRemindersPost,
    getReminderApiV1TodayRemindersReminderIdGet,
    listRemindersApiV1TodayRemindersGet,
    previewReminderApiV1TodayRemindersPreviewPost,
    reminderCallCardApiV1ReminderCallsCardsEventIdGet,
    resolveDateApiV1TodayResolveDatePost,
    setReminderStatusApiV1TodayRemindersReminderIdStatusPost,
    testReminderApiV1TodayRemindersReminderIdTestPost,
    updateReminderApiV1TodayRemindersReminderIdPut,
} from "@/client/sdk.gen";
import type { TimelineEvent } from "@/client/types.gen";
import { PageHeader } from "@/components/layout/PageHeader";
import { ErrorState, type SaveState } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ActionCard, actionOf } from "@/components/workflow/ActionCard";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import { TODAY_TABS } from "@/lib/today/tabs";
import {
    type Channel,
    CHANNEL_LABEL,
    type ChannelState,
    type DeliveryView,
    type ReminderPreview,
    type ReminderView,
    WEEKDAYS,
} from "@/lib/today/types";

type Draft = {
    title: string;
    note: string;
    recurrence: "once" | "daily" | "weekdays" | "weekly";
    date: string;
    local_time: string;
    weekday: number;
    timezone: string;
    /** "call": Decibyl rings the person (reminder calls), set by its card. */
    channel: Channel | "call";
};

const INPUT = "min-h-11 text-base md:min-h-9 md:text-sm";
const FIELD = "flex flex-col gap-1 text-sm";
const DEBOUNCE_MS = 300;
const REPEATS: { value: Draft["recurrence"]; label: string }[] = [
    { value: "once", label: "Once" },
    { value: "daily", label: "Every day" },
    { value: "weekdays", label: "Weekdays" },
    { value: "weekly", label: "Every week" },
];

function draftBody(d: Draft) {
    return {
        title: d.title,
        note: d.note,
        recurrence: d.recurrence,
        local_time: d.local_time,
        timezone: d.timezone || null,
        channel: d.channel,
        ...(d.recurrence === "once" ? { date: d.date } : {}),
        ...(d.recurrence === "weekly" ? { weekday: d.weekday } : {}),
    };
}

function ChannelPicker({
    value,
    states,
    onChange,
    allowCall = false,
}: {
    value: Draft["channel"];
    states: ChannelState[];
    onChange: (c: Draft["channel"]) => void;
    /** Offer "Call me" (reminder calls, a new reminder only). */
    allowCall?: boolean;
}) {
    return (
        <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">Where to remind you</legend>
            {(["in_app", "whatsapp", "push"] as Channel[]).map((channel) => {
                const state = states.find((s) => s.channel === channel);
                return (
                    <label key={channel} className="flex min-h-11 items-start gap-2 text-sm md:min-h-8">
                        <input type="radio" name="channel" className="mt-1 h-4 w-4" checked={value === channel} onChange={() => onChange(channel)} />
                        <span>
                            {CHANNEL_LABEL[channel]}
                            {state && state.state !== "available" && (
                                <span className="block text-xs text-[#705500] dark:text-amber-300">Needs setup: {state.reason}</span>
                            )}
                        </span>
                    </label>
                );
            })}
            {allowCall && (
                <label className="flex min-h-11 items-start gap-2 text-sm md:min-h-8">
                    <input type="radio" name="channel" className="mt-1 h-4 w-4" checked={value === "call"} onChange={() => onChange("call")} />
                    <span>
                        Call me
                        <span className="block text-xs text-muted-foreground">Decibyl rings you and reads it out. You confirm the call on a card first.</span>
                    </span>
                </label>
            )}
        </fieldset>
    );
}

/**
 * "Call me": the reminder-call card for what is in the form, answered here.
 * A number card comes first when no number is confirmed for calls; once it
 * is done, the reminder's own card is asked for.
 */
function CallMe({ draft }: { draft: Draft }) {
    const [card, setCard] = useState<TimelineEvent | null>(null);
    const [message, setMessage] = useState<string | null>(null);
    const [needsNumber, setNeedsNumber] = useState(false);
    const [phone, setPhone] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    const ask = useCallback(
        async (phoneNumber?: string) => {
            setBusy(true);
            setError(null);
            const result = await askForReminderCallApiV1ReminderCallsAskPost({
                body: {
                    title: draft.title,
                    time: draft.local_time,
                    recurrence: draft.recurrence,
                    timezone: draft.timezone || null,
                    ...(draft.recurrence === "once" ? { date: draft.date } : {}),
                    ...(draft.recurrence === "weekly" ? { weekday: draft.weekday } : {}),
                    ...(phoneNumber ? { phone_number: phoneNumber } : {}),
                },
            });
            setBusy(false);
            if (result.error) {
                setError(detailFromError(result.error, "The call could not be set up."));
                return;
            }
            const told = result.data;
            setMessage(told?.message ?? null);
            setNeedsNumber(told?.status === "needs_number");
            setCard(told?.card ?? null);
        },
        [draft.title, draft.local_time, draft.recurrence, draft.timezone, draft.date, draft.weekday],
    );

    // What the form says changed: a card shown for the old words is not
    // this reminder's any more.
    useEffect(() => {
        setCard(null);
        setMessage(null);
        setNeedsNumber(false);
    }, [ask]);

    const refresh = useCallback(async () => {
        if (!card) return;
        const result = await reminderCallCardApiV1ReminderCallsCardsEventIdGet({ path: { event_id: card.id } });
        if (!result.error && result.data) setCard(result.data);
    }, [card]);

    // The number is confirmed: now the reminder's own card.
    const cardAction = card ? actionOf(card) : null;
    const numberDone = cardAction?.action === "reminder_call_number" && cardAction.state === "done";
    useEffect(() => {
        if (numberDone) void ask();
    }, [numberDone, ask]);

    return (
        <div className="flex flex-col gap-3" data-testid="call-me">
            {!card && (
                <Button type="button" className="motion-m1 min-h-11 self-start md:min-h-9" disabled={busy || !draft.title.trim()} onClick={() => void ask()}>
                    {busy ? "Getting the call ready…" : "Show the call to confirm"}
                </Button>
            )}
            {message && <p className="text-sm text-[#705500] dark:text-amber-300">{message}</p>}
            {needsNumber && (
                <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                    <label className={FIELD}>
                        Number to ring
                        <Input type="tel" inputMode="tel" value={phone} onChange={(e) => setPhone(e.target.value)} className={INPUT} placeholder="+91 98765 43210" />
                    </label>
                    <Button type="button" className="motion-m1 min-h-11 md:min-h-9" disabled={busy || !phone.trim()} onClick={() => void ask(phone.trim())}>
                        Use this number
                    </Button>
                </div>
            )}
            {card && <ActionCard event={card} onSettled={setCard} onFired={() => void refresh()} />}
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}

function EventForm({ timezone, states }: { timezone: string; states: ChannelState[] }) {
    const router = useRouter();
    const [title, setTitle] = useState("");
    const [day, setDay] = useState("tomorrow");
    const [time, setTime] = useState("");
    const [zone, setZone] = useState(timezone);
    const [offsets, setOffsets] = useState<number[]>([0]);
    const [channel, setChannel] = useState<Channel>("in_app");
    const [resolved, setResolved] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!time) {
            setResolved(null);
            return;
        }
        const handle = setTimeout(async () => {
            const result = await resolveDateApiV1TodayResolveDatePost({ body: { words: day, local_time: time, timezone: zone } });
            if (result.error) setResolved(null);
            else setResolved((result.data as { full: string }).full);
        }, DEBOUNCE_MS);
        return () => clearTimeout(handle);
    }, [day, time, zone]);

    async function save() {
        setBusy(true);
        setError(null);
        const result = await createEventApiV1TodayEventsPost({
            body: { title, date: day, local_time: time, timezone: zone, reminders: offsets, channel },
        });
        setBusy(false);
        if (result.error) {
            setError(detailFromError(result.error, "The event was not saved."));
            return;
        }
        router.push("/tasks");
    }

    return (
        <form
            className="flex flex-col gap-5"
            onSubmit={(e) => {
                e.preventDefault();
                void save();
            }}
            aria-label="New event"
        >
            <label className={FIELD}>
                What is it
                <Input value={title} onChange={(e) => setTitle(e.target.value)} className={INPUT} required maxLength={200} />
            </label>
            <div className="flex flex-col gap-3 sm:flex-row">
                <label className={FIELD}>
                    Day
                    <div className="flex flex-wrap gap-2">
                        {["today", "tomorrow"].map((w) => (
                            <Button key={w} type="button" variant={day === w ? "default" : "outline"} className="motion-m1 min-h-11 md:min-h-9" onClick={() => setDay(w)}>
                                {w === "today" ? "Today" : "Tomorrow"}
                            </Button>
                        ))}
                        <Input type="date" aria-label="Or a date" value={/^\d/.test(day) ? day : ""} onChange={(e) => setDay(e.target.value || "tomorrow")} className={INPUT} />
                    </div>
                </label>
                <label className={FIELD}>
                    Time it starts
                    <Input type="time" value={time} onChange={(e) => setTime(e.target.value)} className={INPUT} required />
                </label>
            </div>
            <label className={FIELD}>
                Timezone
                <Input value={zone} onChange={(e) => setZone(e.target.value)} className={INPUT} />
            </label>
            <fieldset className="flex flex-col gap-2">
                <legend className="mb-1 text-sm font-medium">Remind me</legend>
                {[
                    { value: 0, label: "At event time" },
                    { value: -1440, label: "One day before" },
                ].map((o) => (
                    <label key={o.value} className="flex min-h-11 items-center gap-2 text-sm md:min-h-8">
                        <input
                            type="checkbox"
                            className="h-4 w-4"
                            checked={offsets.includes(o.value)}
                            onChange={(e) => setOffsets(e.target.checked ? [...offsets, o.value] : offsets.filter((x) => x !== o.value))}
                        />
                        {o.label}
                    </label>
                ))}
            </fieldset>
            <ChannelPicker value={channel} states={states} onChange={(c) => c !== "call" && setChannel(c)} />
            <div className="sticky bottom-0 flex flex-col gap-2 border-t border-border bg-background py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
                <p className="motion-m2 text-sm" aria-live="polite" data-testid="event-when">
                    {resolved ? `${title || "The event"}: ${resolved}.` : "Say the day and the time it starts. We never guess one."}
                </p>
                {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
                <Button type="submit" className="motion-m1 min-h-11 self-start md:min-h-9" disabled={busy || !resolved || !title}>
                    Save event
                </Button>
            </div>
        </form>
    );
}

export function ReminderEditor({ reminderId }: { reminderId?: number }) {
    const { user, loading: authLoading } = useAuth();
    // Keyed on signed-in, not on the user object, so a re-render never
    // refetches.
    const signedIn = Boolean(user);
    const router = useRouter();
    const callsOn = useFeature("reminder_calls");
    const [mode, setMode] = useState<"reminder" | "event">("reminder");
    const [states, setStates] = useState<ChannelState[]>([]);
    const [zone, setZone] = useState<string>("");
    const [stored, setStored] = useState<ReminderView | null>(null);
    const [draft, setDraft] = useState<Draft | null>(null);
    const [preview, setPreview] = useState<ReminderPreview | null>(null);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const [save, setSave] = useState<SaveState>("clean");
    const [message, setMessage] = useState<string | null>(null);
    const [conflict, setConflict] = useState<ReminderView | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [test, setTest] = useState<DeliveryView | null>(null);

    const load = useCallback(async () => {
        const list = await listRemindersApiV1TodayRemindersGet();
        if (list.error) {
            setLoadError(detailFromError(list.error, "Reminders could not load."));
            return;
        }
        const data = list.data as unknown as { timezone: string; channel_states: ChannelState[] };
        setZone(data.timezone);
        setStates(data.channel_states);
        if (reminderId) {
            const one = await getReminderApiV1TodayRemindersReminderIdGet({ path: { reminder_id: reminderId } });
            if (one.error) {
                setLoadError(detailFromError(one.error, "This reminder could not load."));
                return;
            }
            const r = one.data as unknown as ReminderView;
            setStored(r);
            setDraft({
                title: r.title,
                note: r.note,
                recurrence: r.recurrence,
                date: r.remind_at ? new Date(r.remind_at).toLocaleDateString("en-CA", { timeZone: r.timezone }) : "tomorrow",
                local_time:
                    r.local_time ??
                    (r.remind_at ? new Date(r.remind_at).toLocaleTimeString("en-GB", { timeZone: r.timezone, hour: "2-digit", minute: "2-digit" }) : "09:00"),
                weekday: r.weekday ?? 0,
                timezone: r.timezone,
                channel: r.channel,
            });
        } else {
            setDraft({ title: "", note: "", recurrence: "once", date: "tomorrow", local_time: "09:00", weekday: 0, timezone: data.timezone, channel: "in_app" });
        }
        setLoadError(null);
    }, [reminderId]);

    useEffect(() => {
        if (authLoading || !signedIn) return;
        void load();
    }, [authLoading, signedIn, load]);

    // The live next-occurrence sentence. The key it returns is what Save
    // must send: the schedule the person saw, nothing else.
    useEffect(() => {
        if (!draft || stored?.event_id) return;
        // A call is previewed by its own card, not by the app-reminder preview.
        if (draft.channel === "call") {
            setPreview(null);
            setPreviewError(null);
            return;
        }
        if (!draft.title.trim()) {
            setPreview(null);
            return;
        }
        const handle = setTimeout(async () => {
            const result = await previewReminderApiV1TodayRemindersPreviewPost({ body: draftBody(draft) });
            if (result.error) {
                setPreview(null);
                setPreviewError(detailFromError(result.error, "That schedule cannot be read."));
                return;
            }
            setPreviewError(null);
            setPreview(result.data as unknown as ReminderPreview);
        }, DEBOUNCE_MS);
        return () => clearTimeout(handle);
    }, [draft, stored?.event_id]);

    function change(patch: Partial<Draft>) {
        if (!draft) return;
        setDraft({ ...draft, ...patch });
        setSave("dirty");
        setConflict(null);
    }

    async function saveIt() {
        if (!draft || !preview || draft.channel === "call") return;
        setSave("saving");
        const body = { ...draftBody(draft), schedule_key: preview.schedule_key };
        const result = stored
            ? await updateReminderApiV1TodayRemindersReminderIdPut({ path: { reminder_id: stored.id }, body: { ...body, revision: stored.revision } })
            : await createReminderApiV1TodayRemindersPost({ body });
        if (result.error) {
            const detail = (result.error as { detail?: { stored?: ReminderView } }).detail;
            if (detail && typeof detail === "object" && detail.stored) {
                setConflict(detail.stored);
                setSave("conflict");
                return;
            }
            setMessage(detailFromError(result.error, "Your change was not saved. Try again."));
            setSave("rejected");
            return;
        }
        setSave("saved");
        const saved = result.data as unknown as ReminderView;
        if (!stored) router.push(`/tasks/reminders/${saved.id}`);
        else setStored(saved);
    }

    async function status(verb: "pause" | "resume" | "cancel") {
        if (!stored) return;
        const result = await setReminderStatusApiV1TodayRemindersReminderIdStatusPost({ path: { reminder_id: stored.id }, body: { verb } });
        if (result.error) {
            setMessage(detailFromError(result.error, "That did not go through."));
            return;
        }
        setStored({ ...stored, ...(result.data as unknown as ReminderView) });
    }

    async function testIt() {
        if (!stored) return;
        const result = await testReminderApiV1TodayRemindersReminderIdTestPost({ path: { reminder_id: stored.id } });
        if (!result.error) setTest(result.data as unknown as DeliveryView);
    }

    const title = reminderId ? "Reminder" : "New reminder";
    if (loadError) {
        return (
            <>
                <PageHeader title={title} tabs={TODAY_TABS} />
                <div className="mx-auto w-full max-w-[640px] px-4 py-4">
                    <ErrorState title={loadError} onRetry={() => void load()} />
                </div>
            </>
        );
    }
    if (!draft) {
        return (
            <>
                <PageHeader title={title} tabs={TODAY_TABS} />
                <div className="mx-auto w-full max-w-[640px] px-4 py-4" aria-busy="true">
                    <Skeleton className="h-40 w-full" />
                </div>
            </>
        );
    }

    const blocking = preview?.problems.find((p) => p.code === "invalid_past");
    const canSave = Boolean(preview && !blocking && draft.title.trim()) && save !== "saving";
    return (
        <>
            <PageHeader title={title} tabs={TODAY_TABS} />
            <div className="mx-auto flex w-full max-w-[640px] flex-col gap-5 px-4 py-4 sm:px-6" data-testid="reminder-editor">
                <Link href="/tasks" className="inline-flex min-h-11 items-center text-sm text-muted-foreground hover:text-foreground md:min-h-8">
                    ← Back to Today
                </Link>
                {!reminderId && (
                    <div role="tablist" aria-label="What to add" className="flex gap-2">
                        {(["reminder", "event"] as const).map((m) => (
                            <Button key={m} role="tab" aria-selected={mode === m} variant={mode === m ? "default" : "outline"} className="motion-m1 min-h-11 md:min-h-9" onClick={() => setMode(m)}>
                                {m === "reminder" ? "Reminder" : "Event with reminders"}
                            </Button>
                        ))}
                    </div>
                )}

                {mode === "event" && !reminderId ? (
                    <EventForm timezone={zone} states={states} />
                ) : (
                    <form
                        className="flex flex-col gap-5"
                        onSubmit={(e) => {
                            e.preventDefault();
                            void saveIt();
                        }}
                        aria-label={title}
                    >
                        {stored && (
                            <p className="text-sm text-muted-foreground" data-testid="reminder-status">
                                {stored.status === "paused"
                                    ? "Paused. Nothing new is sent; a reminder already being sent finishes."
                                    : stored.status === "missed"
                                      ? "Missed: its time passed before it could be sent. Change the time to set it again."
                                      : stored.status === "cancelled"
                                        ? "Cancelled."
                                        : stored.status === "done"
                                          ? "Done."
                                          : `Next: ${stored.when ?? "—"}`}
                            </p>
                        )}
                        <section className="flex flex-col gap-3" aria-label="What">
                            <label className={FIELD}>
                                Remind me to
                                <Input value={draft.title} onChange={(e) => change({ title: e.target.value })} className={INPUT} required maxLength={200} />
                            </label>
                            <label className={FIELD}>
                                Note (optional)
                                <Textarea value={draft.note} onChange={(e) => change({ note: e.target.value })} className="text-base md:text-sm" maxLength={2000} />
                            </label>
                        </section>

                        {stored?.event_id ? (
                            <p className="text-sm text-muted-foreground">
                                {stored.offset_words} its event. Move the event in Today and this follows it.
                            </p>
                        ) : (
                            <section className="flex flex-col gap-3" aria-label="When">
                                <fieldset className="flex flex-wrap gap-2">
                                    <legend className="mb-1 w-full text-sm font-medium">Repeat</legend>
                                    {REPEATS.map((r) => (
                                        <Button key={r.value} type="button" aria-pressed={draft.recurrence === r.value} variant={draft.recurrence === r.value ? "default" : "outline"} className="motion-m1 min-h-11 md:min-h-9" onClick={() => change({ recurrence: r.value })}>
                                            {r.label}
                                        </Button>
                                    ))}
                                </fieldset>
                                {draft.recurrence === "once" && (
                                    <div className={FIELD}>
                                        <span>Day</span>
                                        <div className="flex flex-wrap gap-2">
                                            {["today", "tomorrow"].map((w) => (
                                                <Button key={w} type="button" aria-pressed={draft.date === w} variant={draft.date === w ? "default" : "outline"} className="motion-m1 min-h-11 md:min-h-9" onClick={() => change({ date: w })}>
                                                    {w === "today" ? "Today" : "Tomorrow"}
                                                </Button>
                                            ))}
                                            <Input type="date" aria-label="Or a date" value={/^\d/.test(draft.date) ? draft.date : ""} onChange={(e) => change({ date: e.target.value || "tomorrow" })} className={INPUT} />
                                        </div>
                                    </div>
                                )}
                                {draft.recurrence === "weekly" && (
                                    <fieldset className="flex flex-wrap gap-2">
                                        <legend className="mb-1 w-full text-sm">On</legend>
                                        {WEEKDAYS.map((d, i) => (
                                            <Button key={d} type="button" aria-pressed={draft.weekday === i} variant={draft.weekday === i ? "default" : "outline"} className="motion-m1 min-h-11 min-w-11 md:min-h-9" onClick={() => change({ weekday: i })}>
                                                {d}
                                            </Button>
                                        ))}
                                    </fieldset>
                                )}
                                <div className="flex flex-col gap-3 sm:flex-row">
                                    <label className={FIELD}>
                                        Time
                                        <Input type="time" value={draft.local_time} onChange={(e) => change({ local_time: e.target.value })} className={INPUT} required />
                                    </label>
                                    <label className={FIELD}>
                                        Timezone
                                        <Input value={draft.timezone} onChange={(e) => change({ timezone: e.target.value })} className={INPUT} />
                                    </label>
                                </div>
                            </section>
                        )}

                        <ChannelPicker value={draft.channel} states={states} onChange={(c) => change({ channel: c })} allowCall={callsOn && !stored} />

                        {draft.channel === "call" && !stored ? (
                            <CallMe draft={draft} />
                        ) : (
                            <div className="sticky bottom-0 flex flex-col gap-2 border-t border-border bg-background py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
                                <p className="motion-m2 text-sm" aria-live="polite" data-testid="reminder-next">
                                    {previewError ?? preview?.sentence ?? (stored?.event_id ? stored.when : "Fill in what and when to see the next reminder.")}
                                </p>
                                {preview?.problems.map((p) => (
                                    <p key={p.code} className="text-sm text-[#705500] dark:text-amber-300">
                                        {p.message}
                                    </p>
                                ))}
                                {save === "conflict" && conflict && (
                                    <div role="alert" className="text-sm text-destructive">
                                        This reminder changed elsewhere: it now says “{conflict.title}”, {conflict.when}. Your draft is kept.
                                        <Button type="button" variant="outline" className="motion-m1 ml-2 min-h-11 md:min-h-8" onClick={() => void load().then(() => setSave("clean"))}>
                                            Use the saved one
                                        </Button>
                                    </div>
                                )}
                                {save === "rejected" && <p role="alert" className="text-sm text-destructive">{message}</p>}
                                {save === "saved" && <p role="status" className="text-sm text-[#075A39]">Saved.</p>}
                                <div className="flex flex-wrap gap-2">
                                    {!stored?.event_id && (
                                        <Button type="submit" className="motion-m1 min-h-11 md:min-h-9" disabled={!canSave}>
                                            {save === "saving" ? "Saving…" : "Save this schedule"}
                                        </Button>
                                    )}
                                    {stored && stored.status === "active" && (
                                        <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void status("pause")}>
                                            Pause
                                        </Button>
                                    )}
                                    {stored && stored.status === "paused" && (
                                        <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void status("resume")}>
                                            Resume
                                        </Button>
                                    )}
                                    {stored && stored.status !== "cancelled" && (
                                        <Button type="button" variant="ghost" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void status("cancel")}>
                                            Cancel reminder
                                        </Button>
                                    )}
                                    {stored && (
                                        <Button type="button" variant="ghost" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void testIt()}>
                                            Send a test (labelled)
                                        </Button>
                                    )}
                                </div>
                                {test && (
                                    <p role="status" className="text-sm text-muted-foreground" data-testid="reminder-test">
                                        Test {test.duplicate ? "already sent this minute" : test.status.replace("_", " ")}
                                        {test.detail ? `: ${test.detail}` : "."} It does not change the schedule.
                                    </p>
                                )}
                            </div>
                        )}
                    </form>
                )}

                {stored && stored.history.length > 0 && (
                    <section aria-label="Changes" className="text-sm">
                        <h2 className="mb-1 font-medium">Changes</h2>
                        <ul className="flex flex-col gap-1 text-muted-foreground">
                            {stored.history.map((h, i) => (
                                <li key={i} className="break-words">
                                    {h.why === "event_moved" ? "Event moved" : h.why === "snoozed" ? "Snoozed" : "Edited"}:{" "}
                                    {h.old ? new Date(h.old).toLocaleString() : "—"} → {new Date(h.new).toLocaleString()}
                                </li>
                            ))}
                        </ul>
                    </section>
                )}
            </div>
        </>
    );
}

export default ReminderEditor;
