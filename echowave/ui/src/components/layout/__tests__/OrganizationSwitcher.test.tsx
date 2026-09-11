import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrganizationSwitcher } from "../OrganizationSwitcher";

const api = vi.hoisted(() => ({ list: vi.fn(), switchTo: vi.fn() }));

vi.mock("@/client/sdk.gen", () => ({
    listMyOrganizationsApiV1OrganizationsMineGet: api.list,
    switchOrganizationApiV1OrganizationsSelectedPut: api.switchTo,
}));
vi.mock("@/components/charts/primitives", () => ({ useAuthReady: () => true }));

beforeEach(() => {
    api.list.mockReset();
    api.switchTo.mockReset();
});

describe("organization switcher", () => {
    it("names the account you are looking at", async () => {
        api.list.mockResolvedValue({
            data: [
                { id: 1, name: "Kriti Labs", role: "admin", is_selected: true },
                { id: 2, name: "Narayani Dental", role: "member", is_selected: false },
            ],
        });
        render(<OrganizationSwitcher />);
        expect(await screen.findByText("Kriti Labs")).toBeTruthy();
    });

    it("offers no menu when there is only one account", async () => {
        // A control that opens onto the thing already on screen is a control
        // that does nothing.
        api.list.mockResolvedValue({
            data: [{ id: 1, name: "Kriti Labs", role: "admin", is_selected: true }],
        });
        render(<OrganizationSwitcher />);
        expect(await screen.findByText("Kriti Labs")).toBeTruthy();
        expect(screen.queryByRole("button", { name: /switch organization/i })).toBeNull();
    });

    it("is a menu once there are two", async () => {
        api.list.mockResolvedValue({
            data: [
                { id: 1, name: "Kriti Labs", role: "admin", is_selected: true },
                { id: 2, name: "Narayani Dental", role: "member", is_selected: false },
            ],
        });
        render(<OrganizationSwitcher />);
        await waitFor(() =>
            expect(screen.getByLabelText("Switch organization")).toBeTruthy(),
        );
    });

    it("shows nothing at all when the list fails", async () => {
        // The sidebar is not the place to report that a lookup broke.
        api.list.mockResolvedValue({ error: { detail: "boom" } });
        const { container } = render(<OrganizationSwitcher />);
        await waitFor(() => expect(container.textContent).toBe(""));
    });

    it("shows nothing while the sidebar is collapsed", async () => {
        api.list.mockResolvedValue({
            data: [{ id: 1, name: "Kriti Labs", role: "admin", is_selected: true }],
        });
        const { container } = render(<OrganizationSwitcher collapsed />);
        await waitFor(() => expect(container.textContent).toBe(""));
    });
});
