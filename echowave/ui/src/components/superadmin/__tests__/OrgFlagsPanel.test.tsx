import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrgFlagsPanel } from "../OrgFlagsPanel";

const api = vi.hoisted(() => ({ orgFlags: vi.fn(), setOrg: vi.fn(), clearOrg: vi.fn() }));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/client/sdk.gen", () => ({
    organizationFeaturesApiV1AdminFeaturesOrganizationsOrganizationIdGet: api.orgFlags,
    setOrganizationOverrideApiV1AdminFeaturesNameOrganizationsOrganizationIdPut: api.setOrg,
    clearOrganizationOverrideApiV1AdminFeaturesNameOrganizationsOrganizationIdDelete: api.clearOrg,
}));

const row = (over: Record<string, unknown>) => ({
    name: "projects",
    description: "Projects.",
    enabled: false,
    global_enabled: false,
    global_source: "environment",
    override: null,
    environment_listed: false,
    ...over,
});

beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    api.setOrg.mockResolvedValue({ data: {} });
    api.clearOrg.mockResolvedValue({ data: {} });
});

describe("the flags section on an account's page", () => {
    it("toggles a flag for this account only", async () => {
        api.orgFlags.mockResolvedValue({ data: { flags: [row({})] } });
        render(<OrgFlagsPanel organizationId={42} />);
        const item = await screen.findByTestId("org-flag-projects");
        expect(within(item).getByText("everyone: off")).toBeTruthy();
        fireEvent.click(within(item).getByLabelText("projects for this account"));
        await waitFor(() =>
            expect(api.setOrg).toHaveBeenCalledWith({
                path: { name: "projects", organization_id: 42 },
                body: { enabled: true, note: null, expires_at: null },
            }),
        );
    });

    it("resets an override back to what everyone gets", async () => {
        api.orgFlags.mockResolvedValue({
            data: {
                flags: [
                    row({
                        enabled: true,
                        override: { organization_id: 42, enabled: true, expired: false },
                    }),
                ],
            },
        });
        render(<OrgFlagsPanel organizationId={42} />);
        const item = await screen.findByTestId("org-flag-projects");
        expect(within(item).getByText("this account")).toBeTruthy();
        fireEvent.click(within(item).getByText("Reset"));
        await waitFor(() =>
            expect(api.clearOrg).toHaveBeenCalledWith({
                path: { name: "projects", organization_id: 42 },
            }),
        );
    });

    it("shows an error instead of an empty list", async () => {
        api.orgFlags.mockResolvedValue({ error: { detail: "No such organization" }, response: { status: 404 } });
        render(<OrgFlagsPanel organizationId={42} />);
        expect(await screen.findByText(/No such organization/)).toBeTruthy();
        expect(screen.queryByTestId("org-flag-projects")).toBeNull();
    });
});
