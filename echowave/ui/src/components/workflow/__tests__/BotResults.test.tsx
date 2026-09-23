/**
 * The board that reads the outcome taxonomy back.
 *
 * Guarded: a zero is shown rather than dropped (the zero is usually the
 * finding); the rate is over runs the classifier actually reached, not over
 * every run; a default taxonomy says so instead of passing for a decision;
 * and a 4xx becomes a message, because the generated client resolves on one
 * rather than throwing and an unchecked result renders an empty board for a
 * request that failed.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const board = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    getOutcomeBoardApiV1WorkflowOutcomesGet: board,
}));

import { BotResults } from "../BotResults";

function entry(over: Record<string, unknown> = {}) {
    return {
        workflow_id: 7,
        name: "Clinic",
        outcomes: [
            { code: "booked", label: "Booked", count: 4 },
            { code: "callback", label: "Call back", count: 0 },
        ],
        runs: 20,
        classified: 16,
        truncated: false,
        configured: true,
        ...over,
    };
}

beforeEach(() => {
    board.mockReset();
    board.mockResolvedValue({ data: [entry()], error: undefined });
});

describe("BotResults", () => {
    it("counts each outcome the classifier filed", async () => {
        render(<BotResults workflowId={7} />);
        expect(await screen.findByText("Booked")).toBeTruthy();
        expect(screen.getByText("4")).toBeTruthy();
    });

    it("shows an outcome that never happened rather than dropping the row", async () => {
        render(<BotResults workflowId={7} />);
        expect(await screen.findByText("Call back")).toBeTruthy();
        expect(screen.getByText("0")).toBeTruthy();
    });

    it("takes the rate over the runs that were actually sorted", async () => {
        // 4 of 16 classified is 25%. Over all 20 runs it would read 20%, which
        // would quietly charge the bot for calls the classifier never reached.
        render(<BotResults workflowId={7} />);
        expect(await screen.findByText("25%")).toBeTruthy();
    });

    it("says how many runs were sorted, so the denominator is visible", async () => {
        render(<BotResults workflowId={7} />);
        expect(await screen.findByText(/16 of 20 runs sorted/)).toBeTruthy();
    });

    it("says when the outcomes are the default set rather than a decision", async () => {
        board.mockResolvedValue({ data: [entry({ configured: false })], error: undefined });
        render(<BotResults workflowId={7} />);
        expect(
            await screen.findByText(/nobody has said what a win is for this agent yet/),
        ).toBeTruthy();
    });

    it("does not say that when the business chose them", async () => {
        render(<BotResults workflowId={7} />);
        await screen.findByText("Booked");
        expect(screen.queryByText(/nobody has said what a win is/)).toBeNull();
    });

    it("says the count is partial when the row cap bit", async () => {
        board.mockResolvedValue({ data: [entry({ truncated: true })], error: undefined });
        render(<BotResults workflowId={7} />);
        expect(
            await screen.findByText(/counting the most recent runs only/),
        ).toBeTruthy();
    });

    it("says there were no runs instead of drawing a board of zeroes", async () => {
        board.mockResolvedValue({
            data: [entry({ runs: 0, classified: 0 })],
            error: undefined,
        });
        render(<BotResults workflowId={7} />);
        expect(await screen.findByText(/No runs in the last 30 days/)).toBeTruthy();
    });

    it("surfaces a 4xx rather than rendering an empty board", async () => {
        board.mockResolvedValue({ data: undefined, error: { detail: "Nope" } });
        render(<BotResults workflowId={7} />);
        expect(await screen.findByText("Nope")).toBeTruthy();
    });

    it("asks for the window the reader picked", async () => {
        render(<BotResults workflowId={7} />);
        await screen.findByText("Booked");
        fireEvent.click(screen.getByText("7d"));
        await waitFor(() =>
            expect(board).toHaveBeenLastCalledWith({
                query: { days: 7, workflow_id: 7 },
            }),
        );
    });

    it("offers the same three windows as the analytics range picker", async () => {
        render(<BotResults workflowId={7} />);
        await screen.findByText("Booked");
        for (const window of ["7d", "30d", "90d"]) {
            expect(screen.getByText(window)).toBeTruthy();
        }
    });
});
