/**
 * Reminder calls in Today (behind `reminder_calls`): the upcoming calls
 * with Cancel, and "Call me" in the reminder editor, which shows the same
 * card as chat (number card first, then the reminder's own) in place.
 * With the flag off, neither appears.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listCalls = vi.fn();
const cancelCall = vi.fn();
const markDone = vi.fn();
const askCall = vi.fn();
const getCard = vi.fn();
const listReminders = vi.fn();
const preview = vi.fn();
const flags: Record<string, boolean> = { reminder_calls: true };

vi.mock("@/client/sdk.gen", () => ({
    listReminderCallsApiV1ReminderCallsGet: (...a: unknown[]) => listCalls(...a),
    cancelReminderCallApiV1ReminderCallsScheduleIdCancelPost: (...a: unknown[]) => cancelCall(...a),
    markReminderDoneApiV1ReminderCallsOccurrencesOccurrenceIdDonePost: (...a: unknown[]) => markDone(...a),
    askForReminderCallApiV1ReminderCallsAskPost: (...a: unknown[]) => askCall(...a),
    reminderCallCardApiV1ReminderCallsCardsEventIdGet: (...a: unknown[]) => getCard(...a),
    listRemindersApiV1TodayRemindersGet: (...a: unknown[]) => listReminders(...a),
    previewReminderApiV1TodayRemindersPreviewPost: (...a: unknown[]) => preview(...a),
    createReminderApiV1TodayRemindersPost: vi.fn(),
    getReminderApiV1TodayRemindersReminderIdGet: vi.fn(),
    updateReminderApiV1TodayRemindersReminderIdPut: vi.fn(),
    setReminderStatusApiV1TodayRemindersReminderIdStatusPost: vi.fn(),
    testReminderApiV1TodayRemindersReminderIdTestPost: vi.fn(),
    createEventApiV1TodayEventsPost: vi.fn(),
    resolveDateApiV1TodayResolveDatePost: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(flags[name]) }));
vi.mock("next/navigation", () => ({
    useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
    usePathname: () => "/tasks/reminders/new",
    useSearchParams: () => new URLSearchParams(""),
}));
// The card itself is ActionCard's own (tested there); here, what the editor
// hands it and does when it settles.
vi.mock("@/components/workflow/ActionCard", () => ({
    actionOf: (event: { payload?: Record<string, unknown> }) => event.payload ?? {},
    ActionCard: ({ event, onFired }: { event: { payload: { label: string; why?: string } }; onFired?: () => void }) => (
        <div data-testid="action-card">
            <p>{event.payload.label}</p>
            {event.payload.why && <p>{event.payload.why}</p>}
            <button type="button" onClick={() => onFired?.()}>
                Window closed
            </button>
        </div>
    ),
}));

import { ReminderCallsSection } from "../ReminderCallsSection";
import { ReminderEditor } from "../ReminderEditor";

const UPCOMING = {
    id: 3,
    title: "Send the proposal",
    language: "ta",
    number: "+91 98••••3210",
    timezone: "Asia/Kolkata",
    local_time: "09:00",
    recurrence: "once",
    state: "active",
    next_due_at: "2026-10-13T03:30:00+00:00",
    next_due_label: "Tue 13 Oct 2026, 09:00 IST (Asia/Kolkata)",
    recent: [],
};

const STATES = [{ channel: "in_app", state: "available", reason: null }];

function card(id: number, action: string, state: string, label: string, why?: string) {
    return { id, at: "2026-10-12T04:30:00Z", kind: "action_proposed", actor: "agent", summary: label, payload: { action, state, label, why } };
}

describe("Reminder calls in Today", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        flags.reminder_calls = true;
        listCalls.mockResolvedValue({ data: { reminders: [UPCOMING, { ...UPCOMING, id: 4, title: "Old one", state: "cancelled", next_due_at: null, next_due_label: null }] } });
        cancelCall.mockResolvedValue({ data: { changed: true } });
    });

    it("lists the upcoming call with its full local time, and cancels it in place", async () => {
        const announce = vi.fn();
        render(<ReminderCallsSection onAnnounce={announce} />);
        expect(await screen.findByText("Send the proposal")).toBeTruthy();
        expect(screen.getByText("Next call: Tue 13 Oct 2026, 09:00 IST (Asia/Kolkata) · +91 98••••3210 · Once")).toBeTruthy();
        expect(screen.queryByText("Old one")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Cancel call" }));
        await waitFor(() => expect(cancelCall).toHaveBeenCalledWith({ path: { schedule_id: 3 } }));
        expect(announce).toHaveBeenCalledWith("Cancelled: Send the proposal. Decibyl won't call you about it.");
        expect(listCalls).toHaveBeenCalledTimes(2);
    });

    it("offers Done for a call that rang and is still open", async () => {
        listCalls.mockResolvedValue({
            data: {
                reminders: [{ ...UPCOMING, recurrence: "daily", recent: [{ id: 9, due_at: "x", task_state: "open", calls: [{ attempt: 1, state: "no_answer", reason: "not_answered" }] }] }],
            },
        });
        markDone.mockResolvedValue({ data: { changed: true } });
        render(<ReminderCallsSection />);
        expect(await screen.findByText("Last call: not answered.")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Done" }));
        await waitFor(() => expect(markDone).toHaveBeenCalledWith({ path: { occurrence_id: 9 } }));
    });

    it("draws nothing with the flag off", () => {
        flags.reminder_calls = false;
        const { container } = render(<ReminderCallsSection />);
        expect(container.innerHTML).toBe("");
        expect(listCalls).not.toHaveBeenCalled();
    });
});

describe("Call me in the reminder editor", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        flags.reminder_calls = true;
        listReminders.mockResolvedValue({ data: { reminders: [], events: [], timezone: "Asia/Kolkata", channel_states: STATES } });
        preview.mockResolvedValue({ data: { sentence: "Once.", schedule_key: "k", problems: [], timezone: "Asia/Kolkata" } });
    });

    it("is not offered with the flag off", async () => {
        flags.reminder_calls = false;
        render(<ReminderEditor />);
        await screen.findByLabelText("Remind me to");
        expect(screen.queryByLabelText(/Call me/)).toBeNull();
    });

    it("shows the number card, then the reminder card, in the editor", async () => {
        askCall
            .mockResolvedValueOnce({ data: { status: "proposed", message: null, card: card(11, "reminder_call_number", "proposed", "Ring me on +91 98••••3210 for reminder calls I ask for") } })
            .mockResolvedValueOnce({
                data: {
                    status: "proposed",
                    message: null,
                    card: card(
                        12,
                        "reminder_call",
                        "proposed",
                        "Tue 13 Oct 2026, 09:00 IST (Asia/Kolkata) · +91 98••••3210 · English",
                        "You asked for 08:30, which is outside calling hours: reminder calls ring only between 9:00 and 21:00 your time, so 08:30 can't ring. This card is for 09:00, the nearest time that can. Confirm it, or say another time.",
                    ),
                },
            });
        getCard.mockResolvedValue({ data: card(11, "reminder_call_number", "done", "Ring me on +91 98••••3210 for reminder calls I ask for") });
        render(<ReminderEditor />);
        fireEvent.change(await screen.findByLabelText("Remind me to"), { target: { value: "Send the proposal" } });
        fireEvent.click(screen.getByLabelText(/Call me/));
        expect(screen.queryByRole("button", { name: "Save this schedule" })).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Show the call to confirm" }));
        await waitFor(() => expect(askCall).toHaveBeenCalledTimes(1));
        expect(askCall.mock.calls[0][0].body).toEqual({ title: "Send the proposal", time: "09:00", recurrence: "once", timezone: "Asia/Kolkata", date: "tomorrow" });
        expect(await screen.findByText("Ring me on +91 98••••3210 for reminder calls I ask for")).toBeTruthy();
        // The number card's window closes: it is re-read, and once done the
        // reminder's own card is asked for and shown in its place.
        fireEvent.click(screen.getByRole("button", { name: "Window closed" }));
        await waitFor(() => expect(getCard).toHaveBeenCalledWith({ path: { event_id: 11 } }));
        await waitFor(() => expect(askCall).toHaveBeenCalledTimes(2));
        expect(await screen.findByText(/08:30 can't ring\. This card is for 09:00/)).toBeTruthy();
    });

    it("says why when the workspace cannot call, and asks for a number when none is known", async () => {
        askCall.mockResolvedValueOnce({ data: { status: "no_line", message: "This workspace has no phone line for calling out, so Decibyl can't ring you. Choose In the app or Push instead: they need no number.", card: null } });
        render(<ReminderEditor />);
        fireEvent.change(await screen.findByLabelText("Remind me to"), { target: { value: "Call mum" } });
        fireEvent.click(screen.getByLabelText(/Call me/));
        fireEvent.click(screen.getByRole("button", { name: "Show the call to confirm" }));
        expect(await screen.findByText(/no phone line for calling out/)).toBeTruthy();

        askCall.mockResolvedValueOnce({ data: { status: "needs_number", message: "Which number should Decibyl ring? Type it below.", card: null } });
        fireEvent.click(screen.getByRole("button", { name: "Show the call to confirm" }));
        fireEvent.change(await screen.findByLabelText("Number to ring"), { target: { value: "+91 98765 43210" } });
        askCall.mockResolvedValueOnce({ data: { status: "proposed", message: null, card: card(13, "reminder_call_number", "proposed", "Ring me on +91 98••••3210 for reminder calls I ask for") } });
        fireEvent.click(screen.getByRole("button", { name: "Use this number" }));
        await waitFor(() => expect(askCall.mock.calls[2][0].body.phone_number).toBe("+91 98765 43210"));
        expect(await screen.findByTestId("action-card")).toBeTruthy();
    });
});
