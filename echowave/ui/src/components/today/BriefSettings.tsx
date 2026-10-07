"use client";

/**
 * Screen 20, daily brief settings: an enable row, then schedule,
 * destination and coverage, with Save / Discard (the shared SaveBar). Off
 * until the person turns it on; 09:00 in their timezone is the suggestion.
 * Each channel says whether it can deliver now or needs setup -- never a
 * channel that pretends to work. Test delivery is one labelled occurrence.
 * The sample shows the brief's structure, not invented events.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { getBriefSettingsApiV1TodayBriefSettingsGet, saveBriefSettingsApiV1TodayBriefSettingsPut, testBriefApiV1TodayBriefTestPost } from "@/client/sdk.gen";
import { ErrorState, SaveBar, type SaveState, SettingsSection } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import { type BriefSettings as Settings, type Channel, CHANNEL_LABEL, type DeliveryView, WEEKDAYS } from "@/lib/today/types";

type Editable = Pick<
    Settings,
    "enabled" | "paused" | "local_time" | "timezone" | "days" | "channels" | "quiet_start" | "quiet_end" | "end_of_day_enabled" | "end_of_day_time"
>;

const KEYS: (keyof Editable)[] = ["enabled", "paused", "local_time", "timezone", "days", "channels", "quiet_start", "quiet_end", "end_of_day_enabled", "end_of_day_time"];
const INPUT = "min-h-11 text-base md:min-h-9 md:text-sm";

function pick(s: Settings): Editable {
    return Object.fromEntries(KEYS.map((k) => [k, s[k]])) as Editable;
}

function changes(draft: Editable, saved: Editable): Partial<Editable> {
    const out: Partial<Editable> = {};
    for (const key of KEYS) {
        if (JSON.stringify(draft[key]) !== JSON.stringify(saved[key])) (out as Record<string, unknown>)[key] = draft[key];
    }
    return out;
}

export function BriefSettings() {
    const { user, loading: authLoading } = useAuth();
    // Keyed on signed-in, not on the user object, so a re-render never
    // refetches.
    const signedIn = Boolean(user);
    const endOfDayOn = useFeature("end_of_day_note");
    const [settings, setSettings] = useState<Settings | null>(null);
    const [draft, setDraft] = useState<Editable | null>(null);
    const [state, setState] = useState<SaveState>("clean");
    const [message, setMessage] = useState<string | null>(null);
    const [stored, setStored] = useState<Settings | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [tests, setTests] = useState<DeliveryView[] | null>(null);
    const [testing, setTesting] = useState(false);

    const load = useCallback(async () => {
        const result = await getBriefSettingsApiV1TodayBriefSettingsGet();
        if (result.error) {
            setLoadError(detailFromError(result.error, "Brief settings could not load."));
            return;
        }
        const data = result.data as unknown as Settings;
        setSettings(data);
        setDraft(pick(data));
        setLoadError(null);
    }, []);

    useEffect(() => {
        if (authLoading || !signedIn) return;
        void load();
    }, [authLoading, signedIn, load]);

    function edit(patch: Partial<Editable>) {
        if (!draft) return;
        setDraft({ ...draft, ...patch });
        setState("dirty");
    }

    async function save(over?: Settings) {
        if (!draft || !settings) return;
        setState("saving");
        // Keeping mine over a conflict sends every field, at the stored
        // revision: a choice the person made, not a silent overwrite.
        const body = over ? { revision: over.revision, ...draft } : { revision: settings.revision, ...changes(draft, pick(settings)) };
        const result = await saveBriefSettingsApiV1TodayBriefSettingsPut({ body });
        if (result.error) {
            const detail = (result.error as { detail?: { stored?: Settings } }).detail;
            if (detail && typeof detail === "object" && detail.stored) {
                setStored(detail.stored);
                setState("conflict");
                return;
            }
            setMessage(detailFromError(result.error, "Your change was not saved. Try again."));
            setState("rejected");
            return;
        }
        const data = result.data as unknown as Settings;
        setSettings(data);
        setDraft(pick(data));
        setStored(null);
        setState("saved");
    }

    async function test() {
        setTesting(true);
        const result = await testBriefApiV1TodayBriefTestPost();
        setTesting(false);
        if (result.error) {
            setTests([]);
            setMessage(detailFromError(result.error, "The test could not be sent."));
            return;
        }
        setTests((result.data as { deliveries: DeliveryView[] }).deliveries);
    }

    if (loadError) return <ErrorState title={loadError} onRetry={() => void load()} />;
    if (!settings || !draft) {
        return (
            <div className="flex flex-col gap-3" aria-busy="true">
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-40 w-full" />
            </div>
        );
    }

    return (
        <div className="flex max-w-[640px] flex-col gap-4" data-testid="brief-settings">
            <SettingsSection id="brief" title="Daily brief" scope="Just you" description="One short summary of what needs you: approvals, what is due, appointments and missed calls.">
                <label className="flex min-h-11 items-center justify-between gap-3">
                    <span className="text-sm font-medium">Send me a daily brief</span>
                    <Switch checked={draft.enabled} onCheckedChange={(v) => edit({ enabled: Boolean(v) })} aria-label="Send me a daily brief" />
                </label>
                <p className="mt-2 text-sm text-muted-foreground" data-testid="brief-next">
                    {state === "clean" || state === "saved" ? settings.next_sentence : "Save to see when the next brief comes."}
                </p>
                {settings.enabled && (
                    <label className="mt-2 flex min-h-11 items-center justify-between gap-3">
                        <span className="text-sm">
                            Pause
                            <span className="block text-xs text-muted-foreground">Keeps these settings. A brief already being prepared finishes; nothing new starts.</span>
                        </span>
                        <Switch checked={draft.paused} onCheckedChange={(v) => edit({ paused: Boolean(v) })} aria-label="Pause the daily brief" />
                    </label>
                )}
            </SettingsSection>

            <SettingsSection id="brief-schedule" title="When" description="At this time on the days you choose, in your timezone.">
                <div className="flex flex-col gap-3">
                    <div className="flex flex-col gap-3 sm:flex-row">
                        <label className="flex flex-col gap-1 text-sm">
                            Time
                            <Input type="time" value={draft.local_time} onChange={(e) => edit({ local_time: e.target.value })} className={INPUT} />
                        </label>
                        <label className="flex flex-col gap-1 text-sm">
                            Timezone
                            <Input value={draft.timezone} onChange={(e) => edit({ timezone: e.target.value })} className={INPUT} />
                        </label>
                    </div>
                    <fieldset className="flex flex-wrap gap-2">
                        <legend className="mb-1 w-full text-sm">Days</legend>
                        {WEEKDAYS.map((day, i) => {
                            const on = draft.days.includes(i);
                            return (
                                <Button
                                    key={day}
                                    type="button"
                                    aria-pressed={on}
                                    variant={on ? "default" : "outline"}
                                    className="motion-m1 min-h-11 min-w-11 md:min-h-9"
                                    onClick={() => edit({ days: on ? draft.days.filter((d) => d !== i) : [...draft.days, i].sort() })}
                                >
                                    {day}
                                </Button>
                            );
                        })}
                    </fieldset>
                    <div className="flex flex-col gap-3 sm:flex-row">
                        <label className="flex flex-col gap-1 text-sm">
                            Quiet from
                            <Input type="time" value={draft.quiet_start} onChange={(e) => edit({ quiet_start: e.target.value })} className={INPUT} />
                        </label>
                        <label className="flex flex-col gap-1 text-sm">
                            Quiet until
                            <Input type="time" value={draft.quiet_end} onChange={(e) => edit({ quiet_end: e.target.value })} className={INPUT} />
                        </label>
                    </div>
                    <p className="text-xs text-muted-foreground">Quiet hours hold back optional suggestions. Your brief and the reminders you set keep their times.</p>
                </div>
            </SettingsSection>

            <SettingsSection id="brief-destination" title="Where">
                <fieldset className="flex flex-col gap-2">
                    <legend className="sr-only">Where to send it</legend>
                    {(["in_app", "whatsapp", "push"] as Channel[]).map((channel) => {
                        const cs = settings.channel_states.find((c) => c.channel === channel);
                        const on = draft.channels.includes(channel);
                        return (
                            <label key={channel} className="flex min-h-11 items-start gap-2 text-sm md:min-h-8">
                                <input
                                    type="checkbox"
                                    className="mt-1 h-4 w-4"
                                    checked={on}
                                    onChange={() => edit({ channels: on ? draft.channels.filter((c) => c !== channel) : [...draft.channels, channel] })}
                                />
                                <span>
                                    {CHANNEL_LABEL[channel]}
                                    {cs && cs.state !== "available" && (
                                        <span className="block text-xs text-[#705500] dark:text-amber-300" data-testid={`channel-${channel}-setup`}>
                                            Needs setup: {cs.reason}
                                        </span>
                                    )}
                                </span>
                            </label>
                        );
                    })}
                </fieldset>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                    <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" disabled={testing || state !== "clean" && state !== "saved"} onClick={() => void test()}>
                        Send a test brief (labelled)
                    </Button>
                    {state !== "clean" && state !== "saved" && <span className="text-xs text-muted-foreground">Save first, then test.</span>}
                </div>
                {tests && (
                    <ul role="status" className="mt-2 flex flex-col gap-1 text-sm" data-testid="brief-test-results">
                        {tests.map((t) => (
                            <li key={t.channel}>
                                {CHANNEL_LABEL[t.channel]}: {t.duplicate ? "already tested this minute" : t.status.replace("_", " ")}
                                {t.detail ? ` — ${t.detail}` : ""}
                            </li>
                        ))}
                    </ul>
                )}
            </SettingsSection>

            <SettingsSection id="brief-coverage" title="What it checks" description="Every source is listed in each brief with what happened to it. A source that could not be read is named, never shown as empty.">
                <ul className="flex flex-col gap-1 text-sm">
                    {settings.sources.map((s) => (
                        <li key={s.kind}>{s.label}</li>
                    ))}
                </ul>
                <div className="mt-3 rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground" aria-label="What a brief looks like">
                    <p className="font-medium text-foreground">What a brief looks like</p>
                    <p>One line with numbers (approvals waiting, things due, appointments, missed calls), the period it covers, when it was refreshed, and which sources it checked.</p>
                </div>
                <p className="mt-2 text-sm">
                    Individual reminders are in{" "}
                    <Link href="/tasks" className="inline-flex min-h-11 items-center underline underline-offset-2 md:min-h-0">
                        Today
                    </Link>
                    .
                </p>
            </SettingsSection>

            {endOfDayOn && (
                <SettingsSection id="end-of-day" title="End-of-day note" description="What was done, what is still open, and missed calls handled.">
                    <label className="flex min-h-11 items-center justify-between gap-3">
                        <span className="text-sm font-medium">Send me an end-of-day note</span>
                        <Switch checked={draft.end_of_day_enabled} onCheckedChange={(v) => edit({ end_of_day_enabled: Boolean(v) })} aria-label="Send me an end-of-day note" />
                    </label>
                    <label className="mt-2 flex flex-col gap-1 text-sm">
                        At
                        <Input type="time" value={draft.end_of_day_time} onChange={(e) => edit({ end_of_day_time: e.target.value })} className={INPUT} />
                    </label>
                    {settings.end_of_day_next && <p className="mt-2 text-xs text-muted-foreground">Next: {settings.end_of_day_next}</p>}
                </SettingsSection>
            )}

            {state === "conflict" && stored && (
                <div role="alert" className="rounded-md border border-destructive/40 p-3 text-sm">
                    <p className="font-medium">These settings changed elsewhere.</p>
                    <p className="text-muted-foreground">
                        Saved now: {stored.enabled ? `on at ${stored.local_time} (${stored.timezone})` : "off"}. Yours: {draft.enabled ? `on at ${draft.local_time} (${draft.timezone})` : "off"}.
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                        <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void load().then(() => setState("clean"))}>
                            Use the saved settings
                        </Button>
                        <Button type="button" variant="ghost" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void save(stored)}>
                            Keep mine
                        </Button>
                    </div>
                </div>
            )}
            <SaveBar
                state={state}
                message={message}
                onSave={() => void save()}
                onDiscard={() => {
                    setDraft(pick(settings));
                    setState("clean");
                }}
            />
        </div>
    );
}

export default BriefSettings;
