/**
 * Seen live on a fresh account: the campaign form spoke of workflows, telephony
 * configurations and initial_context in workflow nodes. A customer has a bot,
 * a number and a list of people to call; the form now says so, and with no
 * number yet it points at the two ways to get one.
 */

import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listConfigs = vi.hoisted(() => vi.fn());
const listBots = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    createCampaignApiV1CampaignCreatePost: vi.fn(),
    getCampaignDefaultsApiV1OrganizationsCampaignDefaultsGet: vi.fn().mockResolvedValue({ data: null }),
    getWorkflowsSummaryApiV1WorkflowSummaryGet: listBots,
    listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet: listConfigs,
}));
vi.mock("next/navigation", () => ({
    useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
    usePathname: () => "/campaigns/new",
    useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/auth", () => ({
    useAuth: () => ({ user: { id: 1 }, loading: false, getAccessToken: async () => "t", redirectToLogin: vi.fn() }),
}));
vi.mock("../../CsvUploadSelector", () => ({ default: () => <div data-testid="csv" /> }));
vi.mock("../../CampaignAdvancedSettings", () => ({
    default: () => <div />,
    getTimezoneValue: (tz: unknown) => String(tz),
}));

import NewCampaignPage from "../page";

beforeEach(() => {
    vi.clearAllMocks();
    listBots.mockResolvedValue({ data: [{ id: 35, name: "QA Front Desk" }] });
});

describe("the campaign form", () => {
    it("asks for a bot, not a workflow", async () => {
        listConfigs.mockResolvedValue({ data: { configurations: [] } });
        render(<NewCampaignPage />);
        await waitFor(() => expect(screen.getByText("Get a number")).toBeTruthy());
        expect(screen.getByText("Bot")).toBeTruthy();
        expect(screen.queryByText(/workflow/i)).toBeNull();
        expect(screen.queryByText(/telephony configuration/i)).toBeNull();
    });

    it("with no number yet, points at getting one or connecting a carrier", async () => {
        listConfigs.mockResolvedValue({ data: { configurations: [] } });
        render(<NewCampaignPage />);
        const get = await screen.findByText("Get a number");
        expect(get.getAttribute("href")).toBe("/numbers");
        expect(screen.getByText("connect your own carrier").getAttribute("href")).toBe("/telephony-configurations");
    });
});
