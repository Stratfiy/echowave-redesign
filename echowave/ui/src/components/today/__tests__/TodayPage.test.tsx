/**
 * Screen 07: Today as one ordered list. What must appear (sections in
 * order, the server's approval count, full local times, the calendar setup
 * line, suggestions with "Why this?") and what must not ("Nothing due" over
 * a section that failed).
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getToday = vi.fn();
const callBack = vi.fn();
const dismiss = vi.fn();
const setStatus = vi.fn();
const replace = vi.fn();
const flags: Record<string, boolean> = { today_reminders: true };

vi.mock("@/client/sdk.gen", () => ({
    getTodayApiV1TodayGet: (...a: unknown[]) => getToday(...a),
    proposeCallBackApiV1TodayMissedCallsMissedCallIdCallBackPost: (...a: unknown[]) => callBack(...a),
    dismissSuggestionApiV1TodaySuggestionsKeyDismissPost: (...a: unknown[]) => dismiss(...a),
    setReminderStatusApiV1TodayRemindersReminderIdStatusPost: (...a: unknown[]) => setStatus(...a),
    snoozeReminderApiV1TodayRemindersReminderIdSnoozePost: vi.fn(),
    refreshBriefApiV1TodayBriefRefreshPost: vi.fn(),
    refreshEndOfDayApiV1TodayEndOfDayRefreshPost: vi.fn(),
    approvalPreviewApiV1TodayApprovalsEventIdGet: vi.fn(() => new Promise(() => {})),
    activityDetailApiV1TodayActivityKindItemIdGet: vi.fn(() => new Promise(() => {})),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false, getAccessToken: async () => "t" }) }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(flags[name]) }));
let search = "";
vi.mock("next/navigation", () => ({
    useRouter: () => ({ push: vi.fn(), replace }),
    usePathname: () => "/tasks",
    useSearchParams: () => new URLSearchParams(search),
}));
vi.mock("@/components/integrations/GoogleCalendarConnect", () => ({
    GoogleCalendarConnect: () => <div data-testid="calendar-connect" />,
}));

import { TodayPage } from "../TodayPage";

function view(overrides: Record<string, unknown> = {}) {
    return {
        date: "2026-10-08",
        date_label: "Thursday 8 October 2026",
        timezone: "Asia/Kolkata",
        refreshed_at: "2026-10-08T04:30:00Z",
        organization_id: 1,
        order: ["approvals", "due", "brief", "upcoming", "suggestions", "end_of_day"],
        sections: {
            approvals: { state: "ok", count: 0, items: [] },
            due: { state: "ok", items: [], active: [] },
            brief: null,
            upcoming: { state: "ok", items: [] },
            end_of_day: null,
            suggestions: { state: "ok", items: [] },
        },
        missing_sources: [],
        empty: true,
        empty_copy: "Nothing due in Decibyl",
        ...overrides,
    };
}

describe("Today", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        search = "";
    });

    it("says Nothing due in Decibyl only when the server says every section answered", async () => {
        getToday.mockResolvedValue({ data: view() });
        render(<TodayPage />);
        expect(await screen.findByText("Nothing due in Decibyl")).toBeTruthy();
        expect(screen.getByTestId("today-scope").textContent).toContain("Asia/Kolkata");
    });

    it("a failed section is an error with Try again, not an empty Today", async () => {
        getToday.mockResolvedValue({
            data: view({
                empty: false,
                empty_copy: null,
                sections: { ...view().sections, approvals: { state: "failed", message: "Approvals could not load. Try again." } },
            }),
        });
        render(<TodayPage />);
        expect(await screen.findByText("Approvals could not load. Try again.")).toBeTruthy();
        expect(screen.queryByText("Nothing due in Decibyl")).toBeNull();
    });

    it("lists sections in the handoff's order with the server's count", async () => {
        getToday.mockResolvedValue({
            data: view({
                empty: false,
                empty_copy: null,
                sections: {
                    ...view().sections,
                    approvals: {
                        state: "ok",
                        count: 2,
                        items: [{ id: 9, sentence: "Decibyl wants to: Call +919800000001 back", detail: "To +919800000001", label: "Call back", version: "v", state: "proposed" }],
                    },
                    due: {
                        state: "ok",
                        items: [{ kind: "task", id: 3, title: "Send the GST filing", at: "2026-10-08T06:30:00Z", when: "Thu 8 Oct 2026, 12:00 IST (Asia/Kolkata)" }],
                        active: [],
                    },
                    upcoming: { state: "ok", items: [{ kind: "event", id: 4, title: "Dentist", at: "2026-10-12T09:30:00Z", when: "Mon 12 Oct 2026, 15:00 IST (Asia/Kolkata)", timezone: "Asia/Kolkata", reminders: [{ id: 1, offset_minutes: -1440, status: "active" }], revision: 1 }] },
                },
            }),
        });
        render(<TodayPage />);
        await screen.findByText("Send the GST filing");
        const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
        expect(headings.indexOf("Waiting for your approval2")).toBeLessThan(headings.indexOf("Due"));
        expect(headings.indexOf("Due")).toBeLessThan(headings.indexOf("Upcoming"));
        expect(screen.getByLabelText("2 waiting")).toBeTruthy();
        expect(screen.getByText("Thu 8 Oct 2026, 12:00 IST (Asia/Kolkata)")).toBeTruthy();
        expect(screen.getByText("Reminders: One day before")).toBeTruthy();
    });

    it("a missing calendar is its own line, connected right here", async () => {
        getToday.mockResolvedValue({ data: view({ missing_sources: [{ kind: "calendar", message: "Connect your calendar to include appointments" }] }) });
        render(<TodayPage />);
        const line = await screen.findByTestId("missing-calendar");
        expect(line.textContent).toContain("Connect your calendar to include appointments");
        fireEvent.click(within(line).getByRole("button", { name: "Connect calendar" }));
        expect(screen.getByTestId("calendar-connect")).toBeTruthy();
    });

    it("a missed call is called back through an approval, opened in the drawer", async () => {
        getToday.mockResolvedValue({
            data: view({
                empty: false,
                empty_copy: null,
                sections: { ...view().sections, due: { state: "ok", items: [{ kind: "missed_call", id: 41, title: "Missed call from +919800000001", at: null, when: null }], active: [] } },
            }),
        });
        callBack.mockResolvedValue({ data: { event_id: 77, status: "proposed" } });
        render(<TodayPage />);
        fireEvent.click(await screen.findByRole("button", { name: "Call back" }));
        await waitFor(() => expect(replace).toHaveBeenCalledWith("/tasks?open=approval%3A77", { scroll: false }));
        expect(callBack.mock.calls[0][0]).toEqual({ path: { missed_call_id: 41 } });
    });

    it("opens the approval drawer from its deep link", async () => {
        search = "open=approval:77";
        getToday.mockResolvedValue({ data: view() });
        render(<TodayPage />);
        expect(await screen.findByTestId("today-drawer")).toBeTruthy();
    });

    it("suggestions say why and can be dismissed", async () => {
        getToday.mockResolvedValue({
            data: view({
                sections: {
                    ...view().sections,
                    suggestions: { state: "ok", items: [{ key: "offer_daily_brief", title: "Would a daily summary help?", why: "One short brief at 09:00 in your timezone. Off until you say yes.", action: { kind: "open_brief_settings" } }] },
                },
            }),
        });
        dismiss.mockResolvedValue({ data: {} });
        render(<TodayPage />);
        expect(await screen.findByText("Would a daily summary help?")).toBeTruthy();
        expect(screen.getByText("Why this?")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
        await waitFor(() => expect(dismiss).toHaveBeenCalledWith({ path: { key: "offer_daily_brief" }, body: {} }));
    });

    it("marks a reminder done only after the server confirmed it", async () => {
        getToday.mockResolvedValue({
            data: view({
                empty: false,
                empty_copy: null,
                sections: { ...view().sections, due: { state: "ok", items: [{ kind: "reminder", id: 5, title: "Pay rent", at: null, when: "Fri 9 Oct 2026, 09:00 IST (Asia/Kolkata)" }], active: [] } },
            }),
        });
        setStatus.mockResolvedValue({ error: { detail: "Only an active reminder can be paused." } });
        render(<TodayPage />);
        fireEvent.click(await screen.findByRole("button", { name: "Done" }));
        expect(await screen.findByText("Only an active reminder can be paused.")).toBeTruthy();
        expect(screen.getByText("Pay rent")).toBeTruthy();
    });
});
