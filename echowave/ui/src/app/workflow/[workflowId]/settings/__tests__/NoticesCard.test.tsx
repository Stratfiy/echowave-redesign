/**
 * The runtime has read a per-bot notification choice for a while and no
 * screen let anybody make it, so every bot ran on the defaults and nobody
 * could say so. What these hold: the offer comes from the server, a tick
 * saves on its own, and a refused save puts the switch back rather than
 * leaving a lie on screen.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const read = vi.hoisted(() => vi.fn());
const write = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    getBotNoticesApiV1WorkflowWorkflowIdNoticesGet: read,
    setBotNoticesApiV1WorkflowWorkflowIdNoticesPut: write,
}));

import { NoticesCard } from "../NoticesCard";

const OFFER = {
    offered: [
        {
            kind: "needs_attention",
            label: "It needs attention",
            when: "The bot has stopped and cannot carry on by itself.",
            default: true,
        },
        {
            kind: "deliverable",
            label: "It produced something",
            when: "A file, a report, a booking.",
            default: false,
        },
    ],
    selected: ["needs_attention"],
};

beforeEach(() => {
    read.mockReset();
    write.mockReset();
    read.mockResolvedValue({ data: OFFER });
    write.mockResolvedValue({ data: OFFER });
});

describe("what a bot tells you", () => {
    it("offers what the server says it can emit, not a list written here", async () => {
        render(<NoticesCard workflowId={35} />);
        expect(await screen.findByText("It needs attention")).toBeTruthy();
        expect(screen.getByText("It produced something")).toBeTruthy();
        expect(read).toHaveBeenCalledWith({ path: { workflow_id: 35 } });
    });

    it("shows what is already chosen", async () => {
        render(<NoticesCard workflowId={35} />);
        const on = await screen.findByRole("switch", { name: "It needs attention" });
        expect(on.getAttribute("data-state")).toBe("checked");
        expect(
            screen.getByRole("switch", { name: "It produced something" }).getAttribute("data-state"),
        ).toBe("unchecked");
    });

    it("saves on the tick, sending the whole selection", async () => {
        render(<NoticesCard workflowId={35} />);
        const off = await screen.findByRole("switch", { name: "It produced something" });
        fireEvent.click(off);
        await waitFor(() => expect(write).toHaveBeenCalled());
        expect(write.mock.calls[0][0].body.kinds.sort()).toEqual([
            "deliverable",
            "needs_attention",
        ]);
    });

    it("puts the switch back when the save is refused", async () => {
        write.mockResolvedValue({ error: { detail: "nope" } });
        render(<NoticesCard workflowId={35} />);
        const on = await screen.findByRole("switch", { name: "It needs attention" });
        fireEvent.click(on);
        await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
        expect(
            screen.getByRole("switch", { name: "It needs attention" }).getAttribute("data-state"),
        ).toBe("checked");
    });

    it("says so, calmly, when the offer cannot be read", async () => {
        read.mockResolvedValue({ error: { detail: "down" } });
        render(<NoticesCard workflowId={35} />);
        expect(await screen.findByRole("alert")).toBeTruthy();
    });
});
