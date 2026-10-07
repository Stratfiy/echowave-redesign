"use client";

/**
 * Settings -> Notifications (screen 21; launch stream identity).
 *
 * Rows by delivery channel, then timing and privacy. Each channel shows
 * both your choice and what the browser or deployment allows. The browser
 * is asked for permission only when you press Enable push, and never again
 * after you said no. Reminders you asked for keep their time through quiet
 * hours; optional suggestions are off until you choose them, and capped.
 * Lock-screen text stays generic while private previews are on.
 */

import { BellRing } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
    addPushSubscriptionApiV1MePushSubscriptionsPost,
    myNotificationsApiV1MeNotificationsGet,
    removePushSubscriptionApiV1MePushSubscriptionsSubscriptionIdDelete,
    saveNotificationsApiV1MeNotificationsPut,
    testNotificationApiV1MeNotificationsTestPost,
} from "@/client/sdk.gen";
import type { NotificationsView } from "@/client/types.gen";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState, ErrorState, SaveBar, type SaveState, SettingsSection } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import { enablePush, type PushPermission, pushPermission } from "@/lib/webPush";

type Draft = Pick<NotificationsView, "channels" | "topics" | "quiet_start" | "quiet_end" | "private_previews">;

const PERMISSION_LABEL: Record<PushPermission, string> = {
    unsupported: "This browser cannot receive push. On an iPhone, add Decibyl to the Home Screen first.",
    default: "The browser has not been asked yet.",
    granted: "Allowed in this browser.",
    denied: "Blocked in this browser's settings. Allow notifications for this site there to turn it back on.",
};

const CHANNEL_LABEL: Record<string, string> = {
    push: "Push to this browser or phone",
    email: "Email",
    channel: "The app you message Decibyl on",
};

function draftOf(view: NotificationsView): Draft {
    return {
        channels: { ...view.channels },
        topics: Object.fromEntries(Object.entries(view.topics).map(([k, v]) => [k, { ...v }])),
        quiet_start: view.quiet_start ?? null,
        quiet_end: view.quiet_end ?? null,
        private_previews: view.private_previews,
    };
}

function ToggleRow({
    id,
    label,
    help,
    checked,
    disabled,
    onChange,
}: {
    id: string;
    label: string;
    help?: string | null;
    checked: boolean;
    disabled?: boolean;
    onChange: (value: boolean) => void;
}) {
    return (
        <div className="flex min-h-11 items-center justify-between gap-3 border-b border-border py-2 last:border-b-0">
            <label htmlFor={id} className="min-w-0 flex-1 cursor-pointer text-sm">
                <span className="block">{label}</span>
                {help && <span className="block text-xs text-muted-foreground">{help}</span>}
            </label>
            {/* The track is small; its hit area is 44px (and the whole row's label toggles it). */}
            <Switch
                id={id}
                checked={checked}
                disabled={disabled}
                onCheckedChange={onChange}
                aria-label={label}
                className="relative before:absolute before:-inset-x-3 before:-inset-y-[13px] before:content-['']"
            />
        </div>
    );
}

function NotificationsScreen() {
    const { user, loading: authLoading } = useAuth();
    const [view, setView] = useState<NotificationsView | null>(null);
    const [draft, setDraft] = useState<Draft | null>(null);
    const [failed, setFailed] = useState(false);
    const [save, setSave] = useState<SaveState>("clean");
    const [message, setMessage] = useState<string | null>(null);
    const [permission, setPermission] = useState<PushPermission>("unsupported");
    const [pushNote, setPushNote] = useState<string | null>(null);
    const started = useRef(false);

    const load = useCallback(async () => {
        const res = await myNotificationsApiV1MeNotificationsGet();
        if (res.error || !res.data) {
            setFailed(true);
            return;
        }
        setFailed(false);
        setView(res.data);
        setDraft(draftOf(res.data));
        setSave("clean");
    }, []);

    useEffect(() => {
        setPermission(pushPermission());
        if (authLoading || !user || started.current) return;
        started.current = true;
        void load();
    }, [authLoading, user, load]);

    const dirty = useMemo(() => !!view && !!draft && JSON.stringify(draftOf(view)) !== JSON.stringify(draft), [view, draft]);
    useEffect(() => {
        if (dirty && (save === "clean" || save === "saved")) setSave("dirty");
        if (!dirty && save === "dirty") setSave("clean");
    }, [dirty, save]);

    const submit = async () => {
        if (!view || !draft) return;
        setSave("saving");
        setMessage(null);
        const res = await saveNotificationsApiV1MeNotificationsPut({ body: { revision: view.revision, ...draft } });
        if (res.error || !res.data) {
            const status = (res.response as Response | undefined)?.status;
            setSave(status === 409 ? "conflict" : "rejected");
            // A conflict reads in the shared words; Discard reloads both.
            setMessage(status === 409 ? null : detailFromError(res.error, "Your change was not saved. Try again."));
            return;
        }
        setView(res.data);
        setDraft(draftOf(res.data));
        setSave("saved");
    };

    const turnOnPush = async () => {
        if (!view?.push_public_key) return;
        setPushNote(null);
        const result = await enablePush(view.push_public_key);
        setPermission(pushPermission());
        if (typeof result === "string") {
            setPushNote(PERMISSION_LABEL[result]);
            return;
        }
        const res = await addPushSubscriptionApiV1MePushSubscriptionsPost({ body: result });
        if (res.error || !res.data) {
            setPushNote(detailFromError(res.error, "This browser could not be added. Try again."));
            return;
        }
        setView(res.data);
        setDraft((d) => (d ? { ...d, channels: { ...d.channels, push: true } } : d));
    };

    const test = async () => {
        const res = await testNotificationApiV1MeNotificationsTestPost();
        setPushNote(
            res.error || !res.data
                ? detailFromError(res.error, "The test could not be sent.")
                : res.data.push === "sent"
                  ? "Test sent. It should appear on your devices now."
                  : res.data.push === "partial"
                    ? "Test sent to some devices; others did not take it."
                    : res.data.push === "no_device"
                      ? "No device is set up for push yet."
                      : "The test did not go through.",
        );
        void load();
    };

    const removeDevice = async (id: number) => {
        const res = await removePushSubscriptionApiV1MePushSubscriptionsSubscriptionIdDelete({ path: { subscription_id: id } });
        if (!res.error && res.data) setView(res.data);
    };

    if (failed && !view) return <ErrorState title="Could not load your notification settings" onRetry={() => void load()} />;
    if (!view || !draft) return <Skeleton className="h-48 w-full" />;

    const setChannel = (name: string, value: boolean) => setDraft({ ...draft, channels: { ...draft.channels, [name]: value } });
    const setTopic = (name: string, change: Partial<{ on: boolean; snoozed_until: string | null }>) =>
        setDraft({ ...draft, topics: { ...draft.topics, [name]: { ...draft.topics[name], ...change } } });
    const activeDevices = view.devices.filter((d) => d.state !== "revoked");

    return (
        <div className="flex flex-col gap-6 pb-24">
            <SettingsSection id="channels" title="Where you are told" description="Your choice, and what this browser or deployment allows." scope="Just you">
                <div className="mb-2 border-b border-border pb-2 text-sm">
                    <p>In the app</p>
                    <p className="text-xs text-muted-foreground">{view.availability.in_app?.reason}</p>
                </div>
                {(["push", "email", "channel"] as const).map((name) => {
                    const available = view.availability[name];
                    return (
                        <ToggleRow
                            key={name}
                            id={`channel-${name}`}
                            label={CHANNEL_LABEL[name]}
                            help={available?.available ? (name === "push" ? PERMISSION_LABEL[permission] : null) : available?.reason}
                            checked={!!draft.channels[name]}
                            disabled={!available?.available || (name === "push" && activeDevices.length === 0 && !draft.channels.push)}
                            onChange={(value) => setChannel(name, value)}
                        />
                    );
                })}
                {view.availability.push?.available && (
                    <div className="mt-3 flex flex-wrap gap-2">
                        {permission !== "denied" && permission !== "unsupported" && (
                            <Button type="button" variant="outline" className="min-h-11 md:min-h-9" onClick={() => void turnOnPush()}>
                                <BellRing aria-hidden className="h-4 w-4" /> {activeDevices.length ? "Add this browser" : "Enable push"}
                            </Button>
                        )}
                        {activeDevices.length > 0 && (
                            <Button type="button" variant="ghost" className="min-h-11 md:min-h-9" onClick={() => void test()}>
                                Send a test
                            </Button>
                        )}
                    </div>
                )}
                {pushNote && (
                    <p role="status" className="mt-2 text-sm">
                        {pushNote}
                    </p>
                )}
                {view.devices.length > 0 && (
                    <ul className="mt-3 divide-y divide-border text-sm" aria-label="Devices">
                        {view.devices.map((device) => (
                            <li key={device.id} className="flex min-h-11 flex-wrap items-center justify-between gap-2 py-2">
                                <span>
                                    {device.label}
                                    <span className="block text-xs text-muted-foreground">
                                        {device.state === "revoked"
                                            ? "Permission removed in the browser, or removed here"
                                            : device.state === "failing"
                                              ? "Recent notices did not reach it"
                                              : "Receiving"}
                                    </span>
                                </span>
                                {device.state !== "revoked" && (
                                    <Button type="button" variant="ghost" className="min-h-11 md:min-h-9" onClick={() => void removeDevice(device.id)}>
                                        Remove
                                    </Button>
                                )}
                            </li>
                        ))}
                    </ul>
                )}
            </SettingsSection>

            <SettingsSection id="topics" title="What you are told about" description="Turn a topic off, or snooze it for a day." scope="Just you">
                {view.topic_list.map((topic) => {
                    const setting = draft.topics[topic.name];
                    const snoozed = setting?.snoozed_until && new Date(setting.snoozed_until).getTime() > Date.now();
                    const help =
                        topic.name === "suggestions"
                            ? `Optional. At most ${view.suggestion_daily_cap} a day, and never in quiet hours.`
                            : topic.requested
                              ? "Keeps the time you asked for, even in quiet hours."
                              : null;
                    return (
                        <div key={topic.name}>
                            <ToggleRow id={`topic-${topic.name}`} label={topic.label} help={help} checked={!!setting?.on} onChange={(on) => setTopic(topic.name, { on })} />
                            {setting?.on && (
                                <div className="-mt-1 mb-2 flex items-center gap-2 text-xs text-muted-foreground">
                                    {snoozed ? (
                                        <>
                                            <span>Snoozed until {new Date(setting.snoozed_until as string).toLocaleString()}</span>
                                            <Button type="button" variant="ghost" className="min-h-11 px-2 text-xs md:min-h-8" onClick={() => setTopic(topic.name, { snoozed_until: null })}>
                                                Unsnooze
                                            </Button>
                                        </>
                                    ) : (
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            className="min-h-11 px-2 text-xs md:min-h-8"
                                            onClick={() => setTopic(topic.name, { snoozed_until: new Date(Date.now() + 86_400_000).toISOString() })}
                                        >
                                            Snooze for a day
                                        </Button>
                                    )}
                                </div>
                            )}
                        </div>
                    );
                })}
            </SettingsSection>

            <SettingsSection id="timing" title="Timing and privacy" scope="Just you">
                <fieldset className="mb-3">
                    <legend className="mb-2 text-sm">Quiet hours</legend>
                    <div className="flex flex-wrap items-center gap-2">
                        <Label htmlFor="quiet-start" className="sr-only">
                            Quiet from
                        </Label>
                        <Input
                            id="quiet-start"
                            type="time"
                            className="w-32 min-h-11 text-base md:min-h-9 md:text-sm"
                            value={draft.quiet_start ?? ""}
                            onChange={(e) => setDraft({ ...draft, quiet_start: e.target.value || null })}
                        />
                        <span className="text-sm">to</span>
                        <Label htmlFor="quiet-end" className="sr-only">
                            Quiet until
                        </Label>
                        <Input
                            id="quiet-end"
                            type="time"
                            className="w-32 min-h-11 text-base md:min-h-9 md:text-sm"
                            value={draft.quiet_end ?? ""}
                            onChange={(e) => setDraft({ ...draft, quiet_end: e.target.value || null })}
                        />
                        {(draft.quiet_start || draft.quiet_end) && (
                            <Button type="button" variant="ghost" className="min-h-11 md:min-h-9" onClick={() => setDraft({ ...draft, quiet_start: null, quiet_end: null })}>
                                No quiet hours
                            </Button>
                        )}
                    </div>
                </fieldset>
                <ToggleRow
                    id="private-previews"
                    label="Private previews"
                    help="Lock screens show only “You have an update”, never what it is about."
                    checked={draft.private_previews}
                    onChange={(value) => setDraft({ ...draft, private_previews: value })}
                />
            </SettingsSection>

            <div className="sticky bottom-0 z-10 bg-background pb-[env(safe-area-inset-bottom)]">
                <SaveBar
                    state={save}
                    message={message}
                    onSave={() => void submit()}
                    onDiscard={() => {
                        setDraft(draftOf(view));
                        setSave("clean");
                        if (save === "conflict") void load();
                    }}
                />
            </div>
        </div>
    );
}

export default function NotificationsPage() {
    const on = useFeature("identity_notifications");
    return (
        <>
            <PageHeader title="Notifications" description="Where and when Decibyl tells you things." />
            <PageBody className="max-w-[640px] px-4 md:px-6">
                {on ? <NotificationsScreen /> : <EmptyState title="Not switched on yet" description="This will appear when it is turned on for your workspace." />}
            </PageBody>
        </>
    );
}
