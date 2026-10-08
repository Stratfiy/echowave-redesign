/**
 * The home's Today rows: today's Activity, newest first, at most five, and
 * nothing at all on a day with nothing in it.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listActivity = vi.fn();
vi.mock("@/client/sdk.gen", () => ({
    listActivityApiV1TodayActivityGet: (...a: unknown[]) => listActivity(...a),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import type { ActivityItem } from "@/lib/today/types";

import { HomeToday, todaysItems } from "../HomeToday";

const now = new Date();
const at = (minutesAgo: number) => new Date(now.getTime() - minutesAgo * 60000).toISOString();
const row = (id: number, title: string, helper: string, minutesAgo: number, state: ActivityItem["state"] = "completed"): ActivityItem => ({
    kind: "card",
    id,
    title,
    state,
    at: at(minutesAgo),
    helper,
    evidence: null,
});

afterEach(cleanup);
beforeEach(() => listActivity.mockReset());

describe("todaysItems", () => {
    it("keeps today's, newest first, at most five", () => {
        const items = [1, 2, 3, 4, 5, 6].map((n) => row(n, `Item ${n}`, "Riya", n));
        items.push(row(9, "Old", "Riya", 60 * 48));
        const shown = todaysItems(items, now);
        expect(shown.map((i) => i.id)).toEqual([1, 2, 3, 4, 5]);
    });
});

describe("HomeToday", () => {
    it("draws a row per item with the agent's blob, and links to its detail", async () => {
        listActivity.mockResolvedValue({ data: { items: [row(7, "Book a cleaning", "Clinic front desk", 1), row(8, "Daily brief in the app", "Daily brief", 2, "failed")] } });
        const { container } = render(<HomeToday bots={[{ id: 12, name: "Clinic front desk" }]} />);
        expect(await screen.findByText("Today")).toBeTruthy();
        expect(screen.getByText(/Book a cleaning/).closest("a")?.getAttribute("href")).toBe("/tasks/activity/card/7");
        expect(screen.getByText("· Daily brief · Failed")).toBeTruthy();
        // One blob for the agent; Decibyl's own work wears the mark.
        expect(container.querySelectorAll('[data-testid="blob-face"]')).toHaveLength(1);
    });

    it("draws nothing when nothing happened today", async () => {
        listActivity.mockResolvedValue({ data: { items: [] } });
        const { container } = render(<HomeToday bots={[]} />);
        await waitFor(() => expect(listActivity).toHaveBeenCalled());
        expect(container.innerHTML).toBe("");
    });
});
