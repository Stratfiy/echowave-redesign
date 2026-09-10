import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OverviewDashboard } from "../OverviewDashboard";

const capture = vi.hoisted(() => vi.fn());
const api = vi.hoisted(() => ({ calls: vi.fn(), spend: vi.fn(), runs: vi.fn() }));

vi.mock("posthog-js", () => ({ default: { capture } }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/client/sdk.gen", () => ({
    getCallAnalyticsApiV1OrganizationsUsageCallsGet: api.calls,
    getSpendBreakdownApiV1OrganizationsUsageSpendGet: api.spend,
    getDailyRunsDetailApiV1OrganizationsReportsDailyRunsGet: api.runs,
}));
vi.mock("@/components/agent-builder/AgentBuilderPanel", () => ({ AgentBuilderPanel: () => <div>builder</div> }));
// recharts measures the DOM; jsdom has no layout. The charts are not what
// these tests are about.
vi.mock("recharts", () => {
    const Empty = () => null;
    return {
        Area: Empty, AreaChart: Empty, Bar: Empty, CartesianGrid: Empty, ComposedChart: Empty,
        Legend: Empty, Line: Empty, ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
        Tooltip: Empty, XAxis: Empty, YAxis: Empty,
    };
});

const EMPTY = {
    daily: [], totals: { calls: 0, answered: 0, billable_seconds: 0, charged_paise: 0, average_seconds: null, answer_rate: null }, by_agent: [],
};
const BUSY = {
    daily: [{ day: "2026-09-01", calls: 12, billable_minutes: 30, charged_paise: 90000 }],
    totals: { calls: 120, answered: 96, billable_seconds: 18000, charged_paise: 4_500_00, average_seconds: 150, answer_rate: 0.8 },
    by_agent: [{ workflow_id: 7, name: "Asha", calls: 100, billable_seconds: 15000, charged_paise: 400000 }],
};

beforeEach(() => {
    capture.mockClear();
    window.matchMedia = vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() });
    api.spend.mockResolvedValue({ data: { series: [], balance_paise: 47400, spent_paise: 450000, burn: { daily_average_paise: 15000, days_remaining: 3 } } });
    api.runs.mockResolvedValue({ data: [] });
});

describe("overview dashboard", () => {
    it("is a door before the first call", async () => {
        api.calls.mockResolvedValue({ data: EMPTY });
        api.spend.mockResolvedValue({ data: { series: [], balance_paise: 0, spent_paise: 0, burn: { daily_average_paise: 0, days_remaining: null } } });
        render(<OverviewDashboard firstName="Nithish" />);
        expect(await screen.findByText(/numbers appear here after the first call/)).toBeTruthy();
        expect(screen.getByRole("link", { name: /build your first agent/i }).getAttribute("href")).toBe("/start");
        expect(screen.getByText("builder")).toBeTruthy();
    });

    it("is a dashboard after it, in credits", async () => {
        api.calls.mockResolvedValue({ data: BUSY });
        render(<OverviewDashboard />);
        expect(await screen.findByText("120")).toBeTruthy();
        expect(screen.getByText("80%")).toBeTruthy();
        expect(screen.getByText("4,500 credits")).toBeTruthy();
        expect(screen.getByText("474 credits")).toBeTruthy();
        expect(screen.queryByText(/₹/)).toBeNull();
        expect(screen.getByRole("link", { name: "Asha" }).getAttribute("href")).toBe("/workflow/7");
    });

    it("refetches on a range change and reports it", async () => {
        api.calls.mockResolvedValue({ data: BUSY });
        render(<OverviewDashboard />);
        await screen.findByText("120");
        fireEvent.click(screen.getByRole("button", { name: "7d" }));
        await waitFor(() => expect(api.calls).toHaveBeenLastCalledWith({ query: { days: 7 } }));
        expect(capture).toHaveBeenCalledWith("overview_range_changed", { days: 7 });
    });

    it("says so when there is nothing to export", async () => {
        api.calls.mockResolvedValue({ data: BUSY });
        render(<OverviewDashboard />);
        await screen.findByText("120");
        fireEvent.click(screen.getByRole("button", { name: /yesterday's calls/i }));
        expect(await screen.findByText(/No calls on/)).toBeTruthy();
        expect(capture.mock.calls.some(([name, props]) => name === "overview_export_clicked" && props.kind === "calls")).toBe(true);
    });
});
