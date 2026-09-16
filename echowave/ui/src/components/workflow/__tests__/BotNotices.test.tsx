/**
 * Choosing what a bot interrupts you about.
 *
 * Guarded: the offer comes from the server, so this screen can never show a
 * checkbox the runtime cannot honour; an empty selection is said out loud,
 * because "tell me nothing" and "this failed to load" look identical
 * otherwise; and a 4xx becomes a message rather than an empty list, which
 * would read as "this bot can tell you nothing".
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());
const put = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    getBotNoticesApiV1WorkflowWorkflowIdNoticesGet: get,
    setBotNoticesApiV1WorkflowWorkflowIdNoticesPut: put,
}));

import { BotNotices } from "../BotNotices";

const OFFER = {
    offered: [
        {
            kind: "needs_attention",
            label: "It needs attention",
            when: "The bot has stopped and cannot carry on by itself.",
            default: true,
        },
        {
            kind: "escalated",
            label: "It handed over to a person",
            when: "A conversation was passed to a human.",
            default: false,
        },
    ],
    selected: ["needs_attention"],
};

beforeEach(() => {
    get.mockReset();
    put.mockReset();
    get.mockResolvedValue({ data: OFFER, error: undefined });
    put.mockResolvedValue({ data: { ...OFFER, selected: ["needs_attention", "escalated"] }, error: undefined });
});

describe("BotNotices", () => {
    it("says what each notice means, not just what it is called", async () => {
        render(<BotNotices workflowId={7} />);
        expect(await screen.findByText("It needs attention")).toBeTruthy();
        expect(
            screen.getByText("The bot has stopped and cannot carry on by itself."),
        ).toBeTruthy();
    });

    it("shows what the bot is already set to", async () => {
        render(<BotNotices workflowId={7} />);
        await screen.findByText("It needs attention");
        const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
        expect(boxes[0].checked).toBe(true);
        expect(boxes[1].checked).toBe(false);
    });

    it("sends what was ticked", async () => {
        render(<BotNotices workflowId={7} />);
        await screen.findByText("It handed over to a person");
        fireEvent.click(screen.getAllByRole("checkbox")[1]);
        fireEvent.click(screen.getByRole("button", { name: /Save/ }));
        await waitFor(() => expect(put).toHaveBeenCalled());
        expect(put.mock.calls[0][0].body.kinds).toContain("escalated");
    });

    it("takes the saved state from the server, not from what it sent", async () => {
        // The server drops anything it will not honour. Showing the request
        // would claim a setting that was refused.
        put.mockResolvedValue({ data: { ...OFFER, selected: [] }, error: undefined });
        render(<BotNotices workflowId={7} />);
        await screen.findByText("It needs attention");
        fireEvent.click(screen.getByRole("button", { name: /Save/ }));
        await waitFor(() =>
            expect(
                screen.getByText("This bot will not notify you about anything."),
            ).toBeTruthy(),
        );
    });

    it("says out loud when nothing is selected", async () => {
        // "Tell me nothing" is a real choice and looks exactly like a screen
        // that failed to load one.
        get.mockResolvedValue({ data: { ...OFFER, selected: [] }, error: undefined });
        render(<BotNotices workflowId={7} />);
        expect(
            await screen.findByText("This bot will not notify you about anything."),
        ).toBeTruthy();
    });

    it("surfaces a 4xx rather than showing an empty offer", async () => {
        get.mockResolvedValue({ data: undefined, error: { detail: "Nope" } });
        render(<BotNotices workflowId={7} />);
        expect(await screen.findByText("Nope")).toBeTruthy();
    });

    it("surfaces a refusal on save and keeps the ticks", async () => {
        put.mockResolvedValue({ data: undefined, error: { detail: "Not allowed" } });
        render(<BotNotices workflowId={7} />);
        await screen.findByText("It handed over to a person");
        fireEvent.click(screen.getAllByRole("checkbox")[1]);
        fireEvent.click(screen.getByRole("button", { name: /Save/ }));
        expect(await screen.findByText("Not allowed")).toBeTruthy();
        expect((screen.getAllByRole("checkbox")[1] as HTMLInputElement).checked).toBe(true);
    });

    it("renders only what the server offered", async () => {
        render(<BotNotices workflowId={7} />);
        await screen.findByText("It needs attention");
        expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    });
});
