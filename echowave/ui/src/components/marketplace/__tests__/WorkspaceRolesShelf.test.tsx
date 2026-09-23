/**
 * The workspace's own roles on the shelf (MP-2, MP-3): hired again with
 * nothing asked, what a copied role still needs shown before hiring, a
 * share link shown once and able to be turned off, a copy offered only into
 * other workspaces this person belongs to -- and nothing at all while the
 * feature is switched off.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
    list: vi.fn(),
    hire: vi.fn(),
    share: vi.fn(),
    unshare: vi.fn(),
    copy: vi.fn(),
    remove: vi.fn(),
    orgs: vi.fn(),
    push: vi.fn(),
    confirm: vi.fn(),
}));

vi.mock("@/client/sdk.gen", () => ({
    listWorkspaceRolesApiV1WorkspaceRolesGet: api.list,
    hireWorkspaceRoleApiV1WorkspaceRolesRoleIdHirePost: api.hire,
    shareWorkspaceRoleApiV1WorkspaceRolesRoleIdSharePost: api.share,
    unshareWorkspaceRoleApiV1WorkspaceRolesRoleIdShareDelete: api.unshare,
    copyWorkspaceRoleApiV1WorkspaceRolesRoleIdCopyToPost: api.copy,
    deleteWorkspaceRoleApiV1WorkspaceRolesRoleIdDelete: api.remove,
    listMyOrganizationsApiV1OrganizationsMineGet: api.orgs,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: api.push }) }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/components/ConfirmDialog", () => ({ useConfirm: () => ({ confirm: api.confirm, dialog: null }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));
const flag = vi.hoisted(() => ({ on: true }));
vi.mock("@/lib/features", () => ({ useFeature: () => flag.on }));

import { WorkspaceRolesShelf } from "../WorkspaceRolesShelf";

const ok = (data: unknown) => ({ data, error: undefined, response: { status: 200 } });
const ROLE = {
    id: 7,
    name: "Front desk, tuned",
    summary: "Our Adyar clinic's desk",
    template_id: "clinic_appointment",
    steps: 3,
    needs: [],
    shared: false,
    copied: false,
};

beforeEach(() => {
    vi.clearAllMocks();
    flag.on = true;
    api.list.mockResolvedValue(ok({ roles: [ROLE] }));
    api.orgs.mockResolvedValue(
        ok([
            { id: 1, name: "Our agency", is_selected: true },
            { id: 2, name: "Client: Kriti Labs", is_selected: false },
        ]),
    );
    api.hire.mockResolvedValue(ok({ id: 501 }));
    api.share.mockResolvedValue(ok({ url: "https://app/roles/install?token=role_abc" }));
    api.unshare.mockResolvedValue(ok({ shared: false }));
    api.copy.mockResolvedValue(ok({ role: {} }));
    api.confirm.mockResolvedValue(true);
});

describe("the workspace's own roles", () => {
    it("hires a role straight into a new agent", async () => {
        render(<WorkspaceRolesShelf />);
        fireEvent.click(await screen.findByRole("button", { name: "Hire" }));
        await waitFor(() => expect(api.push).toHaveBeenCalledWith("/workflow/501"));
        expect(api.hire.mock.calls[0][0].path).toEqual({ role_id: 7 });
    });

    it("shows what a copied role still has to connect", async () => {
        api.list.mockResolvedValue(
            ok({
                roles: [
                    {
                        ...ROLE,
                        copied: true,
                        needs: [
                            { step: "Answer", kind: "tools" },
                            { step: "Answer", kind: "documents" },
                        ],
                    },
                ],
            }),
        );
        render(<WorkspaceRolesShelf />);
        expect(await screen.findByText("To connect after hiring")).toBeTruthy();
        expect(screen.getByText("Answer: tools, documents")).toBeTruthy();
        expect(screen.getByText("Copied in")).toBeTruthy();
    });

    it("shows a share link once, and can turn it off", async () => {
        render(<WorkspaceRolesShelf />);
        api.list.mockResolvedValue(ok({ roles: [{ ...ROLE, shared: true }] }));
        fireEvent.click(await screen.findByRole("button", { name: "Share" }));
        expect(await screen.findByText("https://app/roles/install?token=role_abc")).toBeTruthy();
        fireEvent.click(await screen.findByRole("button", { name: "Turn link off" }));
        await waitFor(() => expect(api.unshare).toHaveBeenCalledWith({ path: { role_id: 7 } }));
        await waitFor(() => expect(screen.queryByText("https://app/roles/install?token=role_abc")).toBeNull());
    });

    it("copies only into another workspace this person belongs to", async () => {
        render(<WorkspaceRolesShelf />);
        const select = await screen.findByLabelText("Copy Front desk, tuned to another workspace");
        const options = Array.from((select as HTMLSelectElement).options).map((o) => o.textContent);
        expect(options).toEqual(["Copy to…", "Client: Kriti Labs"]);
        fireEvent.change(select, { target: { value: "2" } });
        await waitFor(() =>
            expect(api.copy).toHaveBeenCalledWith({ path: { role_id: 7 }, body: { organization_id: 2 } }),
        );
    });

    it("says how to save one when there are none", async () => {
        api.list.mockResolvedValue(ok({ roles: [] }));
        render(<WorkspaceRolesShelf />);
        expect(await screen.findByText(/Save it from its ⋮ menu/)).toBeTruthy();
    });
});

it("renders nothing, and asks for nothing, while workspace roles are switched off", async () => {
    flag.on = false;
    const { container } = render(<WorkspaceRolesShelf />);
    await Promise.resolve();
    expect(container.textContent).toBe("");
    expect(api.list).not.toHaveBeenCalled();
    expect(api.orgs).not.toHaveBeenCalled();
});
