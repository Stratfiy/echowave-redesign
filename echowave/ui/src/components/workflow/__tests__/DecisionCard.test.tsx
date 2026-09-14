/**
 * The card that asks, and then shows the answer.
 *
 * What is guarded: each mode renders the control it promises, the answer goes
 * to the server as the option text, a refusal is shown on the card, and an
 * answered card is a record rather than a control.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const decide = vi.hoisted(() => vi.fn());
vi.mock("@/client/sdk.gen", () => ({ decideApiV1TimelineDecidePost: decide }));

import { DecisionCard } from "../DecisionCard";

const event = (payload: Record<string, unknown>) => ({
    id: 99,
    at: "2026-09-14T00:00:00Z",
    kind: "needs_decision",
    actor: "agent",
    summary: "Book the 10am or the 4pm?",
    payload: { question: "Book the 10am or the 4pm?", options: ["10am", "4pm"], mode: "single", ...payload },
    is_deliverable: false,
    workflow_id: 3,
    workflow_run_id: null,
    folder_id: 5,
});

beforeEach(() => decide.mockReset());

describe("asking", () => {
    it("offers the options as a single choice and sends the one picked", async () => {
        decide.mockResolvedValue({ data: event({ decided: { choice: ["4pm"] } }) });
        const onDecided = vi.fn();
        render(<DecisionCard event={event({ why: "Both are free." })} onDecided={onDecided} />);
        expect(screen.getByText("Both are free.")).toBeTruthy();
        expect(screen.getByRole("button", { name: "Choose" }).hasAttribute("disabled")).toBe(true);
        fireEvent.click(screen.getByLabelText("4pm"));
        fireEvent.click(screen.getByRole("button", { name: "Choose" }));
        await waitFor(() =>
            expect(decide).toHaveBeenCalledWith({ body: { event_id: 99, choice: ["4pm"], other: null } }),
        );
        expect(onDecided).toHaveBeenCalled();
    });

    it("lets multi mode take several", async () => {
        decide.mockResolvedValue({ data: event({ mode: "multi", decided: { choice: ["10am", "4pm"] } }) });
        render(<DecisionCard event={event({ mode: "multi" })} />);
        fireEvent.click(screen.getByLabelText("10am"));
        fireEvent.click(screen.getByLabelText("4pm"));
        fireEvent.click(screen.getByRole("button", { name: "Choose these" }));
        await waitFor(() =>
            expect(decide.mock.calls[0][0].body.choice).toEqual(["10am", "4pm"]),
        );
    });

    it("approve mode is two buttons, no list", async () => {
        decide.mockResolvedValue({ data: event({ mode: "approve", decided: { choice: ["Approve"] } }) });
        render(<DecisionCard event={event({ mode: "approve", options: ["Approve", "Reject"] })} />);
        expect(screen.queryByRole("radio")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Reject" }));
        await waitFor(() => expect(decide.mock.calls[0][0].body.choice).toEqual(["Reject"]));
    });

    it("takes a written answer when the question allows one", async () => {
        decide.mockResolvedValue({ data: event({ decided: { choice: [], other: "2pm" } }) });
        render(<DecisionCard event={event({ allow_other: true })} />);
        fireEvent.change(screen.getByLabelText("Something else"), { target: { value: "2pm" } });
        fireEvent.click(screen.getByRole("button", { name: "Choose" }));
        await waitFor(() => expect(decide.mock.calls[0][0].body.other).toBe("2pm"));
    });

    it("shows the server's refusal on the card", async () => {
        decide.mockResolvedValue({ error: { detail: "Already answered." } });
        render(<DecisionCard event={event({})} />);
        fireEvent.click(screen.getByLabelText("10am"));
        fireEvent.click(screen.getByRole("button", { name: "Choose" }));
        expect((await screen.findByRole("alert")).textContent).toContain("Already answered");
    });
});

describe("answered", () => {
    it("is a stamp, not a control", () => {
        render(<DecisionCard event={event({ decided: { choice: ["4pm"], by: 1 } })} />);
        expect(screen.getByText("4pm")).toBeTruthy();
        expect(screen.queryByRole("button")).toBeNull();
        expect(screen.queryByRole("radio")).toBeNull();
    });
});
