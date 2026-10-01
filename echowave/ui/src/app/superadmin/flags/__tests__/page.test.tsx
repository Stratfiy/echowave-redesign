import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FlagsPage from "../page";

const api = vi.hoisted(() => ({
    list: vi.fn(),
    setOrg: vi.fn(),
    clearOrg: vi.fn(),
    setGlobal: vi.fn(),
    clearGlobal: vi.fn(),
    accounts: vi.fn(),
    orgFlags: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/client/sdk.gen", () => ({
    listFeaturesApiV1AdminFeaturesGet: api.list,
    setOrganizationOverrideApiV1AdminFeaturesNameOrganizationsOrganizationIdPut: api.setOrg,
    clearOrganizationOverrideApiV1AdminFeaturesNameOrganizationsOrganizationIdDelete: api.clearOrg,
    setGlobalOverrideApiV1AdminFeaturesNameGlobalPut: api.setGlobal,
    clearGlobalOverrideApiV1AdminFeaturesNameGlobalDelete: api.clearGlobal,
    listAccountsApiV1AdminBillingAccountsGet: api.accounts,
    organizationFeaturesApiV1AdminFeaturesOrganizationsOrganizationIdGet: api.orgFlags,
}));

const projects = {
    name: "projects",
    description: "Projects.",
    setting: "PROJECTS_ENABLED",
    global_enabled: false,
    global_source: "environment",
    environment_enabled: false,
    global_override: null,
    overrides: [
        {
            organization_id: 42,
            organization_name: "Asha Clinic",
            enabled: true,
            note: "pilot",
            expires_at: null,
            expired: false,
            set_by_user_id: 1,
            updated_at: null,
        },
    ],
    environment_organization_ids: [7],
};

const trial = {
    ...projects,
    name: "trial_plan",
    description: "The 14-day trial replaces the free plan.",
    setting: "TRIAL_PLAN_ENABLED",
    global_enabled: true,
    global_source: "console",
    overrides: [],
    environment_organization_ids: [],
};

beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    api.list.mockResolvedValue({ data: { flags: [projects, trial] } });
    api.setOrg.mockResolvedValue({ data: {} });
    api.clearOrg.mockResolvedValue({ data: {} });
    api.setGlobal.mockResolvedValue({ data: {} });
});

describe("the feature flags screen", () => {
    it("lists each flag with what everyone gets and the accounts it is on for", async () => {
        render(<FlagsPage />);
        const row = await screen.findByTestId("flag-projects");
        expect(within(row).getByText("Projects.")).toBeTruthy();
        expect(within(row).getByText("Off")).toBeTruthy();
        expect(within(row).getByText("from environment")).toBeTruthy();
        expect(within(row).getByText("Asha Clinic")).toBeTruthy();
        // The env list still decides, so it is shown rather than hidden.
        expect(within(row).getByText("Organization 7 (environment)")).toBeTruthy();

        const trialRow = screen.getByTestId("flag-trial_plan");
        expect(within(trialRow).getByText("On")).toBeTruthy();
        expect(within(trialRow).getByText("set here")).toBeTruthy();
    });

    it("never calls an agent a bot", async () => {
        const { container } = render(<FlagsPage />);
        await screen.findByTestId("flag-projects");
        expect(/\bbots?\b/i.test(container.textContent ?? "")).toBe(false);
    });

    it("says so when the flags cannot be loaded", async () => {
        api.list.mockResolvedValue({ error: { detail: "Access denied" }, response: { status: 403 } });
        render(<FlagsPage />);
        expect(await screen.findByText("Access denied")).toBeTruthy();
        expect(screen.getByText("Try again")).toBeTruthy();
    });

    it("removes an account from a flag", async () => {
        render(<FlagsPage />);
        const remove = await screen.findByLabelText("Remove Asha Clinic from projects");
        fireEvent.click(remove);
        await waitFor(() =>
            expect(api.clearOrg).toHaveBeenCalledWith({
                path: { name: "projects", organization_id: 42 },
            }),
        );
        expect(api.list).toHaveBeenCalledTimes(2);
    });

    it("turns a flag on for an account found by email", async () => {
        api.accounts.mockResolvedValue({
            data: {
                accounts: [{ organization_id: 9, name: "Ravi Traders", owner_email: "ravi@example.com" }],
            },
        });
        render(<FlagsPage />);
        const row = await screen.findByTestId("flag-projects");
        fireEvent.click(within(row).getByText("Turn on for an account"));

        fireEvent.change(screen.getByLabelText("Account"), { target: { value: "ravi@" } });
        const option = await screen.findByText("Ravi Traders");
        expect(api.accounts).toHaveBeenCalledWith({ query: { q: "ravi@" } });
        fireEvent.click(option);
        fireEvent.click(screen.getByText("Turn on for Ravi Traders"));

        await waitFor(() =>
            expect(api.setOrg).toHaveBeenCalledWith({
                path: { name: "projects", organization_id: 9 },
                body: { enabled: true, note: null, expires_at: null },
            }),
        );
    });

    it("needs the flag's name typed before changing it for everyone", async () => {
        render(<FlagsPage />);
        await screen.findByTestId("flag-projects");
        fireEvent.click(screen.getByLabelText("projects for everyone"));

        const confirm = screen.getAllByText("Turn on for everyone").find((el) => el.closest("button"))!;
        const button = confirm.closest("button")!;
        expect(button.hasAttribute("disabled")).toBe(true);

        fireEvent.change(screen.getByLabelText(/to confirm/), { target: { value: "project" } });
        expect(button.hasAttribute("disabled")).toBe(true);
        fireEvent.change(screen.getByLabelText(/to confirm/), { target: { value: "projects" } });
        expect(button.hasAttribute("disabled")).toBe(false);

        fireEvent.click(button);
        await waitFor(() =>
            expect(api.setGlobal).toHaveBeenCalledWith({
                path: { name: "projects" },
                body: { enabled: true, confirm: "projects" },
            }),
        );
    });
});
