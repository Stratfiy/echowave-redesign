/**
 * The timeline has marked deliverable rows since it was built and the route
 * has taken `deliverables_only` for as long; nothing ever asked for it. What
 * these hold: the screen asks for that filter and not the whole feed, the
 * rows carry the bot that produced them, and a day is a heading rather than
 * a date on every line.
 */

import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const timeline = vi.hoisted(() => vi.fn());
const workflows = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    timelineApiV1TimelineGet: timeline,
    getWorkflowsApiV1WorkflowFetchGet: workflows,
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/deliverables" }));

import DeliverablesPage, { byDay } from "../page";

const event = (over: Record<string, unknown> = {}) => ({
    id: 1,
    at: new Date().toISOString(),
    kind: "deliverable",
    actor: "agent",
    summary: "Quote for Meera, ₹4,200",
    payload: {},
    is_deliverable: true,
    workflow_id: 3,
    workflow_run_id: 77,
    folder_id: null,
    ...over,
});

beforeEach(() => {
    timeline.mockReset();
    workflows.mockReset();
    timeline.mockResolvedValue({ data: { events: [event()] } });
    workflows.mockResolvedValue({ data: [{ id: 3, name: "Quote desk", handle: null }] });
});

describe("what the bots handed over", () => {
    it("asks for the deliverables, not the whole feed", async () => {
        render(<DeliverablesPage />);
        await waitFor(() => expect(timeline).toHaveBeenCalled());
        expect(timeline.mock.calls[0][0].query.deliverables_only).toBe(true);
    });

    it("names the bot that produced it, and opens the call it came from", async () => {
        render(<DeliverablesPage />);
        expect(await screen.findByText("Quote desk")).toBeTruthy();
        const link = screen.getByText("Quote for Meera, ₹4,200");
        expect(link.closest("a")?.getAttribute("href")).toBe("/workflow/3/run/77");
    });

    it("lists the files a row carries", async () => {
        timeline.mockResolvedValue({
            data: {
                events: [
                    event({
                        payload: {
                            attachments: [{ document_uuid: "d1", filename: "quote.pdf" }],
                        },
                    }),
                ],
            },
        });
        render(<DeliverablesPage />);
        expect(await screen.findByText("quote.pdf")).toBeTruthy();
    });

    it("says so when a bot has handed over nothing yet", async () => {
        timeline.mockResolvedValue({ data: { events: [] } });
        render(<DeliverablesPage />);
        expect(await screen.findByText("Nothing handed over yet.")).toBeTruthy();
    });

    it("says so, calmly, when the feed cannot be read", async () => {
        timeline.mockResolvedValue({ error: { detail: "down" } });
        render(<DeliverablesPage />);
        expect(await screen.findByRole("alert")).toBeTruthy();
    });
});

describe("cutting the list into days", () => {
    it("keeps one heading per day, in the order the rows arrived", () => {
        const today = new Date();
        const yesterday = new Date(today.getTime() - 86_400_000);
        const groups = byDay([
            event({ id: 1, at: today.toISOString() }),
            event({ id: 2, at: today.toISOString() }),
            event({ id: 3, at: yesterday.toISOString() }),
        ]);
        expect(groups.map((g) => g.day)).toEqual(["Today", "Yesterday"]);
        expect(groups[0].events).toHaveLength(2);
    });
});
