/**
 * Screens 10 and 20: the reminder editor saves only the schedule it showed,
 * says the next occurrence above Save and names a past date; the brief
 * settings are off until accepted, say which channels need setup, save the
 * revision they read and show both versions on a conflict. Screen 09:
 * Activity keeps its filters in the URL and shows evidence.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const list = vi.fn();
const preview = vi.fn();
const create = vi.fn();
const getSettings = vi.fn();
const saveSettings = vi.fn();
const testBrief = vi.fn();
const listActivity = vi.fn();
const push = vi.fn();
const replace = vi.fn();
let search = "";

vi.mock("@/client/sdk.gen", () => ({
    listRemindersApiV1TodayRemindersGet: (...a: unknown[]) => list(...a),
    previewReminderApiV1TodayRemindersPreviewPost: (...a: unknown[]) => preview(...a),
    createReminderApiV1TodayRemindersPost: (...a: unknown[]) => create(...a),
    getReminderApiV1TodayRemindersReminderIdGet: vi.fn(),
    updateReminderApiV1TodayRemindersReminderIdPut: vi.fn(),
    setReminderStatusApiV1TodayRemindersReminderIdStatusPost: vi.fn(),
    testReminderApiV1TodayRemindersReminderIdTestPost: vi.fn(),
    createEventApiV1TodayEventsPost: vi.fn(),
    resolveDateApiV1TodayResolveDatePost: vi.fn(),
    getBriefSettingsApiV1TodayBriefSettingsGet: (...a: unknown[]) => getSettings(...a),
    saveBriefSettingsApiV1TodayBriefSettingsPut: (...a: unknown[]) => saveSettings(...a),
    testBriefApiV1TodayBriefTestPost: (...a: unknown[]) => testBrief(...a),
    listActivityApiV1TodayActivityGet: (...a: unknown[]) => listActivity(...a),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: () => false }));
vi.mock("next/navigation", () => ({
    useRouter: () => ({ push, replace }),
    usePathname: () => "/tasks/activity",
    useSearchParams: () => new URLSearchParams(search),
}));

import { ActivityList } from "../ActivityList";
import { BriefSettings } from "../BriefSettings";
import { ReminderEditor } from "../ReminderEditor";

const STATES = [
    { channel: "in_app", state: "available", reason: null },
    { channel: "whatsapp", state: "needs_setup", reason: "Link your WhatsApp to Decibyl to get this there." },
    { channel: "push", state: "needs_setup", reason: "Phone notifications are not set up yet." },
];

describe("The reminder editor", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        list.mockResolvedValue({ data: { reminders: [], events: [], timezone: "Asia/Kolkata", channel_states: STATES } });
        preview.mockResolvedValue({
            data: { next_at: "2026-10-09T03:30:00+00:00", sentence: "Once. Next: Fri 9 Oct 2026, 09:00 IST (Asia/Kolkata).", schedule_key: "k-123", timezone: "Asia/Kolkata", problems: [], event: null },
        });
        create.mockResolvedValue({ data: { id: 8 } });
    });

    it("says the next occurrence above Save and saves that exact schedule", async () => {
        render(<ReminderEditor />);
        fireEvent.change(await screen.findByLabelText("Remind me to"), { target: { value: "Pay rent" } });
        expect(await screen.findByText("Once. Next: Fri 9 Oct 2026, 09:00 IST (Asia/Kolkata).", undefined, { timeout: 2000 })).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save this schedule" }));
        await waitFor(() => expect(create).toHaveBeenCalled());
        expect(create.mock.calls[0][0].body).toMatchObject({ title: "Pay rent", date: "tomorrow", local_time: "09:00", schedule_key: "k-123" });
        expect(push).toHaveBeenCalledWith("/tasks/reminders/8");
    });

    it("names a past date and will not save it", async () => {
        preview.mockResolvedValue({
            data: { next_at: "2026-10-08T02:30:00+00:00", sentence: "Once. Next: Thu 8 Oct 2026, 08:00 IST (Asia/Kolkata).", schedule_key: "k", timezone: "Asia/Kolkata", problems: [{ code: "invalid_past", message: "Thu 8 Oct 2026, 08:00 IST (Asia/Kolkata) has already passed." }], event: null },
        });
        render(<ReminderEditor />);
        fireEvent.change(await screen.findByLabelText("Remind me to"), { target: { value: "Pay rent" } });
        expect(await screen.findByText(/has already passed/, undefined, { timeout: 2000 })).toBeTruthy();
        expect((screen.getByRole("button", { name: "Save this schedule" }) as HTMLButtonElement).disabled).toBe(true);
    });

    it("says which channels need setup", async () => {
        render(<ReminderEditor />);
        expect(await screen.findByText("Needs setup: Phone notifications are not set up yet.")).toBeTruthy();
    });
});

const SETTINGS = {
    enabled: false,
    paused: false,
    local_time: "09:00",
    timezone: "Asia/Kolkata",
    days: [0, 1, 2, 3, 4, 5, 6],
    channels: ["in_app"],
    quiet_start: "21:00",
    quiet_end: "08:00",
    end_of_day_enabled: false,
    end_of_day_time: "18:00",
    revision: 0,
    saved: false,
    channel_states: STATES,
    next_at: null,
    next_sentence: "Off. Turn it on to get one short brief a day.",
    end_of_day_next: null,
    sources: [
        { kind: "approvals", label: "Approvals" },
        { kind: "calendar", label: "Calendar" },
    ],
};

describe("Daily brief settings", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        getSettings.mockResolvedValue({ data: SETTINGS });
    });

    it("is off until accepted, with 09:00 suggested and honest channels", async () => {
        render(<BriefSettings />);
        expect(await screen.findByText("Off. Turn it on to get one short brief a day.")).toBeTruthy();
        expect((screen.getByLabelText("Time") as HTMLInputElement).value).toBe("09:00");
        expect(screen.getByTestId("channel-push-setup").textContent).toContain("not set up yet");
        expect(screen.getByText("Calendar")).toBeTruthy();
    });

    it("saves only what changed, at the revision it read", async () => {
        saveSettings.mockResolvedValue({ data: { ...SETTINGS, enabled: true, revision: 1, next_sentence: "Next brief: Fri 9 Oct 2026, 09:00 IST (Asia/Kolkata)." } });
        render(<BriefSettings />);
        fireEvent.click(await screen.findByRole("switch", { name: "Send me a daily brief" }));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        await waitFor(() => expect(saveSettings).toHaveBeenCalled());
        expect(saveSettings.mock.calls[0][0].body).toEqual({ revision: 0, enabled: true });
        expect(await screen.findByText("Next brief: Fri 9 Oct 2026, 09:00 IST (Asia/Kolkata).")).toBeTruthy();
    });

    it("a conflict shows both versions and overwrites nothing", async () => {
        saveSettings.mockResolvedValue({ error: { detail: { message: "This changed since you opened it.", stored: { ...SETTINGS, enabled: true, local_time: "07:00", revision: 2 } } } });
        render(<BriefSettings />);
        fireEvent.click(await screen.findByRole("switch", { name: "Send me a daily brief" }));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(await screen.findByText(/Saved now: on at 07:00/)).toBeTruthy();
        expect(screen.getByRole("button", { name: "Keep mine" })).toBeTruthy();
        expect(saveSettings).toHaveBeenCalledTimes(1);
    });
});

describe("Activity", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        search = "state=failed";
        listActivity.mockResolvedValue({
            data: {
                items: [{ kind: "card", id: 3, title: "Turn Front desk on", state: "failed", at: "2026-10-08T04:30:00Z", helper: "Decibyl", evidence: "That agent no longer exists." }],
                helpers: ["Decibyl"],
            },
        });
    });

    it("reads its filters from the URL and shows evidence", async () => {
        render(<ActivityList />);
        expect(await screen.findByText("That agent no longer exists.")).toBeTruthy();
        expect(listActivity.mock.calls[0][0]).toEqual({ query: { state: "failed" } });
        expect(screen.getByRole("link", { name: /Turn Front desk on/ }).getAttribute("href")).toBe("/tasks/activity/card/3?state=failed");
    });

    it("writes a filter back to the URL", async () => {
        render(<ActivityList />);
        await screen.findByText("That agent no longer exists.");
        fireEvent.change(screen.getByLabelText("Helper"), { target: { value: "Decibyl" } });
        expect(replace).toHaveBeenCalledWith("/tasks/activity?state=failed&helper=Decibyl", { scroll: false });
    });
});
