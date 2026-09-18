/**
 * Seen live on a fresh Indian account: "Showing data for America/New_York
 * timezone". The page started in New York, asked for that day's report,
 * then swapped to the account's zone if it had one. An account with none
 * stayed in New York, where "today" is a different day.
 */

import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const daily = vi.hoisted(() => vi.fn());
const preferences = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    getDailyReportApiV1OrganizationsReportsDailyGet: daily,
    getDailyRunsDetailApiV1OrganizationsReportsDailyRunsGet: vi.fn().mockResolvedValue({ data: [] }),
    getPreferencesApiV1OrganizationsPreferencesGet: preferences,
    getWorkflowOptionsApiV1OrganizationsReportsWorkflowsGet: vi.fn().mockResolvedValue({ data: [] }),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ isAuthenticated: true, user: { id: 1 }, loading: false }) }));
vi.mock("../components/DispositionChart", () => ({ DispositionChart: () => <div /> }));
vi.mock("../components/DurationChart", () => ({ DurationChart: () => <div /> }));
vi.mock("../components/MetricsCards", () => ({ MetricsCards: () => <div /> }));

import ReportsPage from "../page";

const here = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kolkata";

beforeEach(() => {
    vi.clearAllMocks();
    daily.mockResolvedValue({ data: { date: "2026-09-18", timezone: "x", workflow_id: null, metrics: { total_runs: 0, xfer_count: 0 }, disposition_distribution: [], call_duration_distribution: [] } });
});

describe("the daily report's timezone", () => {
    it("is the account's when it has one, and the report is asked for only in that zone", async () => {
        preferences.mockResolvedValue({ data: { timezone: "Asia/Kolkata" } });
        render(<ReportsPage />);
        await waitFor(() => expect(daily).toHaveBeenCalled());
        expect(daily.mock.calls.map((c) => c[0].query.timezone)).toEqual(["Asia/Kolkata"]);
        expect(screen.getByText(/Showing data for Asia\/Kolkata/)).toBeTruthy();
    });

    it("is the browser's when the account has none, never New York", async () => {
        preferences.mockResolvedValue({ data: {} });
        render(<ReportsPage />);
        await waitFor(() => expect(daily).toHaveBeenCalled());
        expect(daily.mock.calls.map((c) => c[0].query.timezone)).toEqual([here]);
        expect(screen.queryByText(/America\/New_York/)).toBeNull();
    });

    it("still gets a zone when preferences cannot be read", async () => {
        preferences.mockRejectedValue(new Error("down"));
        vi.spyOn(console, "error").mockImplementation(() => {});
        render(<ReportsPage />);
        await waitFor(() => expect(daily).toHaveBeenCalled());
        expect(daily.mock.calls[0][0].query.timezone).toBe(here);
    });
});
