/**
 * A shared role's link (MP-3): it shows the role and what this workspace
 * would have to connect, installs into the signed-in workspace, and says
 * plainly when a link has been turned off.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ preview: vi.fn(), install: vi.fn(), push: vi.fn(), token: "role_abc" }));

vi.mock("@/client/sdk.gen", () => ({
    previewSharedRoleApiV1WorkspaceRolesSharedTokenGet: api.preview,
    installSharedRoleApiV1WorkspaceRolesSharedTokenInstallPost: api.install,
}));
vi.mock("next/navigation", () => ({
    useRouter: () => ({ push: api.push }),
    useSearchParams: () => new URLSearchParams(`token=${api.token}`),
    usePathname: () => "/roles/install",
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import InstallSharedRolePage from "../page";

beforeEach(() => {
    vi.clearAllMocks();
    api.preview.mockResolvedValue({
        data: { name: "Front desk, tuned", summary: null, steps: 3, needs: [{ step: "Book", kind: "tools" }] },
        error: undefined,
    });
    api.install.mockResolvedValue({ data: { role: {} }, error: undefined });
});

it("shows the role and what to connect, then adds it to this workspace", async () => {
    render(<InstallSharedRolePage />);
    expect(await screen.findByText("Front desk, tuned")).toBeTruthy();
    expect(screen.getByText("Book: tools")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Add to my workspace" }));
    await waitFor(() => expect(api.install).toHaveBeenCalledWith({ path: { token: "role_abc" } }));
    expect(api.push).toHaveBeenCalledWith("/marketplace");
});

it("says so when a link has been turned off", async () => {
    api.preview.mockResolvedValue({
        data: undefined,
        error: { detail: "That link has been turned off, or was never valid." },
    });
    render(<InstallSharedRolePage />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/turned off/);
    expect(screen.queryByRole("button", { name: "Add to my workspace" })).toBeNull();
});
