/**
 * A bot's own numbers.
 *
 * Two things are load-bearing: the request names this bot, and the screen
 * says what it cost in the units a person reads. The rest of the figures are
 * the account's existing analytics, tested where they are computed.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const analytics = vi.hoisted(() => vi.fn());
vi.mock("@/client/sdk.gen", () => ({
    getCallAnalyticsApiV1OrganizationsUsageCallsGet: analytics,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
// Recharts measures a container that jsdom gives no size, so the chart body
// is not what this file is about; the figures around it are.
vi.mock("recharts", () => {
    const Passthrough = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
    return {
        Bar: () => null,
        BarChart: Passthrough,
        CartesianGrid: () => null,
        ResponsiveContainer: Passthrough,
        Tooltip: () => null,
        XAxis: () => null,
        YAxis: () => null,
    };
});

import { BotAnalytics } from "../BotAnalytics";

const BODY = {
    totals: {
        calls: 12,
        answered: 9,
        billable_seconds: 600,
        charged_paise: 45050,
        average_seconds: 50,
        answer_rate: 0.75,
    },
    daily_runs: [
        { day: "2026-09-01", runs: 5, answered: 4, billable_seconds: 300, charged_paise: 100 },
        { day: "2026-09-02", runs: 7, answered: 5, billable_seconds: 300, charged_paise: 100 },
    ],
    tokens: {
        total_tokens: 1500,
        prompt_tokens: 1200,
        completion_tokens: 300,
        by_model: [
            { model: "brain-a", total_tokens: 1000, prompt_tokens: 800, completion_tokens: 200 },
            { model: "brain-b", total_tokens: 500, prompt_tokens: 400, completion_tokens: 100 },
        ],
    },
};

beforeEach(() => {
    vi.clearAllMocks();
    // The charts resolve light or dark in JS, and jsdom has no matchMedia.
    window.matchMedia = vi.fn().mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
    });
    analytics.mockResolvedValue({ data: BODY });
});

describe("BotAnalytics", () => {
    it("asks for this bot's numbers, not the account's", async () => {
        render(<BotAnalytics workflowId={42} />);
        await waitFor(() => expect(analytics).toHaveBeenCalled());
        expect(analytics.mock.calls[0][0]).toEqual({
            query: { days: 30, workflow_id: 42 },
        });
    });

    it("reads money in rupees, not the paise the API speaks", async () => {
        render(<BotAnalytics workflowId={42} />);
        expect(await screen.findByText("₹450.5")).toBeTruthy();
    });

    it("shows the runs, the answer rate and the tokens", async () => {
        render(<BotAnalytics workflowId={42} />);
        expect(await screen.findByText("12")).toBeTruthy();
        expect(screen.getByText("75%")).toBeTruthy();
        expect(screen.getByText("1,500")).toBeTruthy();
        expect(screen.getByText("brain-a")).toBeTruthy();
    });

    it("re-asks for a different window when one is chosen", async () => {
        render(<BotAnalytics workflowId={42} />);
        await waitFor(() => expect(analytics).toHaveBeenCalledTimes(1));
        fireEvent.click(screen.getByRole("button", { name: "7 days" }));
        await waitFor(() =>
            expect(analytics).toHaveBeenLastCalledWith({
                query: { days: 7, workflow_id: 42 },
            }),
        );
    });

    it("says so rather than showing zeroes when the read fails", async () => {
        analytics.mockResolvedValue({ error: { detail: "nope" } });
        render(<BotAnalytics workflowId={42} />);
        expect(
            await screen.findAllByText(/could not be read just now/),
        ).toHaveLength(2);
    });
});
