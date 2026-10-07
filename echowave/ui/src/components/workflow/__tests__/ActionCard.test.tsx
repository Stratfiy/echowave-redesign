/**
 * The card that confirms an action, with time to take it back.
 *
 * Guarded: a proposal has Confirm and Not now; confirming sends the verb and
 * the card shows the countdown with Undo; a done switch offers Put it back
 * while a placed call says it cannot be undone; a refusal shows on the card.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const settle = vi.hoisted(() => vi.fn());
vi.mock("@/client/sdk.gen", () => ({ settleActionApiV1TimelineActionsSettlePost: settle }));

import { ActionCard } from "../ActionCard";

const event = (payload: Record<string, unknown>) => ({
    id: 99,
    at: "2026-09-14T00:00:00Z",
    kind: "action_proposed",
    actor: "agent",
    summary: "Turn Front desk on",
    payload: {
        action: "turn_bot_on",
        label: "Turn Front desk on",
        why: "It has been off since Monday",
        reversible: true,
        state: "proposed",
        ...payload,
    },
    is_deliverable: false,
    workflow_id: 3,
    workflow_run_id: null,
    folder_id: 5,
});

beforeEach(() => settle.mockReset());
afterEach(() => vi.useRealTimers());

describe("a proposal", () => {
    it("has Confirm and Not now, and confirming sends the verb", async () => {
        settle.mockResolvedValue({
            data: event({ state: "armed", fires_at: new Date(Date.now() + 10_000).toISOString() }),
        });
        const onSettled = vi.fn();
        render(<ActionCard event={event({})} onSettled={onSettled} />);
        expect(screen.getByText("It has been off since Monday")).toBeTruthy();
        expect(screen.getByRole("button", { name: "Not now" })).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
        await waitFor(() => expect(settle).toHaveBeenCalled());
        expect(settle.mock.calls[0][0].body).toEqual({ event_id: 99, verb: "confirm" });
        await waitFor(() => expect(onSettled).toHaveBeenCalled());
    });

    it("says what confirming does, where the buttons are", () => {
        // The card knew it could not be undone and said so nowhere: the
        // operator saw a label and `why`, which the model writes. Asked for
        // a draft, it proposed a send and called it a draft in the chat.
        render(<ActionCard event={event({ effect: "Runs in gmail and reaches people there. It cannot be undone." })} />);
        expect(screen.getByText(/cannot be undone/i)).toBeTruthy();
    });

    it("is gone once the decision has been made", () => {
        // It answers "what happens if I press this", so it belongs to the
        // moment the buttons are there and nowhere after.
        render(<ActionCard event={event({ state: "done", effect: "Runs in gmail and reaches people there. It cannot be undone." })} />);
        expect(screen.queryByText(/cannot be undone/i)).toBeNull();
    });

    it("shows a refusal on the card", async () => {
        settle.mockResolvedValue({ error: { detail: "Already settled." } });
        render(<ActionCard event={event({})} />);
        fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
        expect(await screen.findByRole("alert")).toBeTruthy();
        expect(screen.getByRole("alert").textContent).toContain("Already settled.");
    });
});

describe("once confirmed", () => {
    it("counts down with Undo, and asks for a refetch when the window closes", async () => {
        vi.useFakeTimers();
        const onFired = vi.fn();
        render(
            <ActionCard
                event={event({ state: "armed", fires_at: new Date(Date.now() + 2_000).toISOString() })}
                onFired={onFired}
            />,
        );
        expect(screen.getByText("Doing this in 2s")).toBeTruthy();
        expect(screen.getByRole("button", { name: "Undo" })).toBeTruthy();
        await act(async () => {
            vi.advanceTimersByTime(2_100);
        });
        expect(screen.getByText("Doing this now…")).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
        await act(async () => {
            vi.advanceTimersByTime(1_600);
        });
        expect(onFired).toHaveBeenCalled();
    });
});

describe("once done", () => {
    it("a switch offers Put it back", async () => {
        settle.mockResolvedValue({ data: event({ state: "undone" }) });
        render(<ActionCard event={event({ state: "done", done: { note: "Front desk is now on." } })} />);
        expect(screen.getByText("Front desk is now on.")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Put it back" }));
        await waitFor(() => expect(settle).toHaveBeenCalled());
        expect(settle.mock.calls[0][0].body).toEqual({ event_id: 99, verb: "undo" });
    });

    it("a built agent offers Hear it and Try it into the tester", () => {
        render(
            <ActionCard
                event={event({
                    action: "create_bot",
                    label: "Create Narayani front desk",
                    reversible: false,
                    state: "done",
                    done: { note: "Created Narayani front desk (@narayani-front-desk)." },
                    result: { workflow_id: 99, handle: "narayani-front-desk", open_url: "/workflow/99" },
                })}
            />,
        );
        expect(screen.getByRole("link", { name: /Hear it/ }).getAttribute("href")).toBe(
            "/workflow/99?test=call",
        );
        expect(screen.getByRole("link", { name: /Try it/ }).getAttribute("href")).toBe(
            "/workflow/99?test=text",
        );
        expect(screen.getByText("@narayani-front-desk")).toBeTruthy();
        expect(screen.queryByText(/Cannot be undone/)).toBeNull();
    });

    it("a placed call says it cannot be undone and offers nothing", () => {
        render(
            <ActionCard
                event={event({
                    action: "return_missed_call",
                    label: "Call +919876543210 back",
                    reversible: false,
                    state: "done",
                    done: { note: "Calling +919876543210 back now." },
                })}
            />,
        );
        expect(screen.getByText("Cannot be undone")).toBeTruthy();
        expect(screen.queryByRole("button")).toBeNull();
    });

    it("a refused action says why", () => {
        render(<ActionCard event={event({ state: "failed", error: "Outside calling hours" })} />);
        expect(screen.getByText("Could not")).toBeTruthy();
        expect(screen.getByText(/Outside calling hours/)).toBeTruthy();
    });
});

describe("approval bound to a version (task ledger)", () => {
    it("confirms the exact version on screen", async () => {
        settle.mockResolvedValue({ data: event({ state: "armed" }) });
        render(<ActionCard event={event({ version: "a1b2c3d4e5f60718" })} />);
        fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
        await waitFor(() => expect(settle).toHaveBeenCalled());
        expect(settle.mock.calls[0][0].body).toEqual({
            event_id: 99,
            verb: "confirm",
            version: "a1b2c3d4e5f60718",
        });
    });

    it("never sends a version with Not now", async () => {
        settle.mockResolvedValue({ data: event({ state: "declined" }) });
        render(<ActionCard event={event({ version: "a1b2c3d4e5f60718" })} />);
        fireEvent.click(screen.getByRole("button", { name: "Not now" }));
        await waitFor(() => expect(settle).toHaveBeenCalled());
        expect(settle.mock.calls[0][0].body).toEqual({ event_id: 99, verb: "decline" });
    });

    it("says a lost send is being checked, offers nothing to press, and is not a failure", () => {
        render(
            <ActionCard
                event={event({
                    state: "outcome_unknown",
                    error: "We are checking whether this was delivered. Please do not send it again.",
                })}
            />,
        );
        expect(screen.getByRole("status").textContent).toContain("Please do not send it again");
        expect(screen.queryByRole("button")).toBeNull();
        expect(screen.queryByText("Could not")).toBeNull();
    });
});

describe("a step on the person's own computer", () => {
    const desktop = (payload: Record<string, unknown>) =>
        event({
            action: "desktop_step",
            label: "Send the reply to Asha Rao",
            why: "",
            preview: 'To asha@example.com: "Thanks, see you Monday."',
            effect: "Send in Mail on your computer, once. Nothing else is done until you answer.",
            reversible: false,
            ...payload,
        });

    it("shows the exact detail while it waits, beside the buttons", () => {
        render(<ActionCard event={desktop({})} />);
        expect(screen.getByTestId("action-preview").textContent).toBe('To asha@example.com: "Thanks, see you Monday."');
        expect(screen.getByText(/Send in Mail on your computer, once/)).toBeTruthy();
        expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
    });

    it("says released, not done, once the window has passed", () => {
        render(<ActionCard event={desktop({ state: "released" })} />);
        expect(screen.getByText("Approved. Your computer will do this once.")).toBeTruthy();
        expect(screen.queryByRole("button")).toBeNull();
        expect(screen.queryByTestId("action-preview")).toBeNull();
    });

    it("confirms the version on screen, like every card", async () => {
        settle.mockResolvedValue({ data: desktop({ state: "armed" }) });
        render(<ActionCard event={desktop({ version: "0123456789abcdef" })} />);
        fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
        await waitFor(() => expect(settle).toHaveBeenCalled());
        expect(settle.mock.calls[0][0].body).toEqual({ event_id: 99, verb: "confirm", version: "0123456789abcdef" });
    });

    it("says the computer is doing it, then unknown if it never reported", () => {
        const { unmount } = render(<ActionCard event={desktop({ state: "running" })} />);
        expect(screen.getByText("Your computer is doing this now…")).toBeTruthy();
        unmount();
        render(
            <ActionCard
                event={desktop({
                    state: "outcome_unknown",
                    error: "We are checking whether this was delivered. Please do not send it again.",
                })}
            />,
        );
        expect(screen.getByRole("status").textContent).toContain("Please do not send it again");
        expect(screen.queryByRole("button")).toBeNull();
    });

    it("says why when no computer took it", () => {
        render(
            <ActionCard
                event={desktop({ state: "cancelled", error: "Your computer did not take this in time. Nothing was done." })}
            />,
        );
        expect(screen.getByText(/did not take this in time/)).toBeTruthy();
    });
});
