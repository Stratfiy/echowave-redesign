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
const query = vi.hoisted(() => ({ value: "" }));
vi.mock("next/navigation", () => ({
    useSearchParams: () => new URLSearchParams(query.value),
    useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
    usePathname: () => "/analytics",
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ isAuthenticated: true, user: { id: 1 }, loading: false }) }));
vi.mock("../day/DispositionChart", () => ({ DispositionChart: () => <div /> }));
vi.mock("../day/DurationChart", () => ({ DurationChart: () => <div /> }));
vi.mock("../day/MetricsCards", () => ({ MetricsCards: () => <div /> }));

import { DayReport as ReportsPage } from "../DayReport";

const here = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kolkata";

beforeEach(() => {
    vi.clearAllMocks();
    query.value = "";
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

describe("the day Analytics sent it to", () => {
    it("opens the date in the URL, not today", async () => {
        query.value = "date=2026-09-14";
        preferences.mockResolvedValue({ data: { timezone: "Asia/Kolkata" } });
        render(<ReportsPage />);
        await waitFor(() => expect(daily).toHaveBeenCalled());
        expect(daily.mock.calls[0][0].query.date).toBe("2026-09-14");
    });

    it("opens the agent in the URL too", async () => {
        query.value = "date=2026-09-14&workflow_id=7";
        preferences.mockResolvedValue({ data: { timezone: "Asia/Kolkata" } });
        render(<ReportsPage />);
        await waitFor(() => expect(daily).toHaveBeenCalled());
        expect(daily.mock.calls[0][0].query.workflow_id).toBe(7);
    });

    it("falls back to today when the date is not a date", async () => {
        query.value = "date=yesterday";
        preferences.mockResolvedValue({ data: { timezone: "Asia/Kolkata" } });
        render(<ReportsPage />);
        await waitFor(() => expect(daily).toHaveBeenCalled());
        const today = new Date();
        const pad = (n: number) => String(n).padStart(2, "0");
        expect(daily.mock.calls[0][0].query.date).toBe(
            `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`,
        );
    });

    it("points back at Analytics for the shape over weeks", async () => {
        preferences.mockResolvedValue({ data: { timezone: "Asia/Kolkata" } });
        render(<ReportsPage />);
        // The tab strip links there too; this is the one in the sentence
        // under the title, which is what tells somebody why they would.
        const links = await screen.findAllByText("Analytics");
        expect(
            links.some((el) => el.closest("a")?.getAttribute("href") === "/analytics"),
        ).toBe(true);
    });
});
