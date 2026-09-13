import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrganizationSwitcher } from "../OrganizationSwitcher";

const api = vi.hoisted(() => ({ list: vi.fn(), switchTo: vi.fn(), rename: vi.fn() }));

vi.mock("@/client/sdk.gen", () => ({
    listMyOrganizationsApiV1OrganizationsMineGet: api.list,
    switchOrganizationApiV1OrganizationsSelectedPut: api.switchTo,
    renameOrganizationApiV1OrganizationsSelectedPatch: api.rename,
}));
vi.mock("@/components/charts/primitives", () => ({ useAuthReady: () => true }));

const kriti = (role: string) => ({ id: 1, name: "Kriti Labs", role, is_selected: true });
const narayani = { id: 2, name: "Narayani Dental", role: "member", is_selected: false };

beforeEach(() => {
    api.list.mockReset();
    api.switchTo.mockReset();
    api.rename.mockReset();
});

describe("organization switcher", () => {
    it("names the account you are looking at", async () => {
        api.list.mockResolvedValue({ data: [kriti("admin"), narayani] });
        render(<OrganizationSwitcher />);
        expect(await screen.findByText("Kriti Labs")).toBeTruthy();
    });

    it("is a plain label for a member of one account", async () => {
        // Nothing to switch to and no right to rename: a menu would open onto
        // nothing they can do.
        api.list.mockResolvedValue({ data: [kriti("member")] });
        render(<OrganizationSwitcher />);
        expect(await screen.findByText("Kriti Labs")).toBeTruthy();
        expect(screen.queryByLabelText("Workspace menu")).toBeNull();
    });

    it("is a menu once there are two", async () => {
        api.list.mockResolvedValue({ data: [kriti("member"), narayani] });
        render(<OrganizationSwitcher />);
        await waitFor(() => expect(screen.getByLabelText("Workspace menu")).toBeTruthy());
    });

    it("shows nothing at all when the list fails", async () => {
        // The sidebar is not the place to report that a lookup broke.
        api.list.mockResolvedValue({ error: { detail: "boom" } });
        const { container } = render(<OrganizationSwitcher />);
        await waitFor(() => expect(container.textContent).toBe(""));
    });

    it("shows nothing while the sidebar is collapsed", async () => {
        api.list.mockResolvedValue({ data: [kriti("admin")] });
        const { container } = render(<OrganizationSwitcher collapsed />);
        await waitFor(() => expect(container.textContent).toBe(""));
    });
});

describe("renaming", () => {
    it("is offered to an admin of a single account", async () => {
        api.list.mockResolvedValue({ data: [kriti("admin")] });
        render(<OrganizationSwitcher />);
        fireEvent.keyDown(await screen.findByLabelText("Workspace menu"), { key: "Enter" });
        expect(await screen.findByText("Rename organization")).toBeTruthy();
    });

    it("is not offered to a member", async () => {
        api.list.mockResolvedValue({ data: [kriti("member"), narayani] });
        render(<OrganizationSwitcher />);
        fireEvent.keyDown(await screen.findByLabelText("Workspace menu"), { key: "Enter" });
        expect(await screen.findByText("Narayani Dental")).toBeTruthy();
        expect(screen.queryByText("Rename organization")).toBeNull();
    });

    it("saves the new name and shows it without a reload", async () => {
        api.list.mockResolvedValue({ data: [kriti("owner")] });
        api.rename.mockResolvedValue({
            data: { id: 1, name: "Kriti Diagnostics", role: "owner", is_selected: true },
        });
        render(<OrganizationSwitcher />);
        fireEvent.keyDown(await screen.findByLabelText("Workspace menu"), { key: "Enter" });
        fireEvent.click(await screen.findByText("Rename organization"));
        const input = await screen.findByLabelText("Organization name");
        fireEvent.change(input, { target: { value: "Kriti Diagnostics" } });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        await waitFor(() =>
            expect(api.rename).toHaveBeenCalledWith({ body: { name: "Kriti Diagnostics" } }),
        );
        expect(await screen.findByText("Kriti Diagnostics")).toBeTruthy();
        expect(screen.queryByText("Kriti Labs")).toBeNull();
    });

    it("reports a refusal in the dialog", async () => {
        api.list.mockResolvedValue({ data: [kriti("admin")] });
        api.rename.mockResolvedValue({ error: { detail: "Admin role required in this organization." } });
        render(<OrganizationSwitcher />);
        fireEvent.keyDown(await screen.findByLabelText("Workspace menu"), { key: "Enter" });
        fireEvent.click(await screen.findByText("Rename organization"));
        fireEvent.change(await screen.findByLabelText("Organization name"), {
            target: { value: "Other" },
        });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect((await screen.findByRole("alert")).textContent).toContain("Admin role required");
        // The old name stands until the server accepts a new one.
        expect(screen.getAllByText("Kriti Labs").length).toBeGreaterThan(0);
    });
});
