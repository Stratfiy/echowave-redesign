/**
 * The channels in the rail.
 *
 * This component shipped with no test at all, which is how it also shipped
 * without the auth guard: it was written by copying SidebarBots, and that
 * component's tests never said when a fetch was allowed to happen. Every test
 * here is one that would have been red on the deployed build.
 */

import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listFolders = vi.hoisted(() => vi.fn());
const auth = vi.hoisted(() => ({ user: { id: 1 } as { id: number } | null, loading: false }));

vi.mock("next/navigation", () => ({ usePathname: () => "/overview" }));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));
vi.mock("@/client/sdk.gen", () => ({ listFoldersApiV1FolderGet: listFolders }));
vi.mock("@/components/ui/sidebar", () => ({
    SidebarGroup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    SidebarGroupLabel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    SidebarMenu: ({ children }: { children: React.ReactNode }) => <ul>{children}</ul>,
    SidebarMenuItem: ({ children }: { children: React.ReactNode }) => <li>{children}</li>,
    SidebarMenuButton: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { CHANNEL_LIMIT, SidebarChannels } from "../SidebarChannels";

const folder = (id: number, name: string) => ({ id, name, created_at: "2026-09-13T00:00:00Z" });

beforeEach(() => {
    listFolders.mockReset();
    listFolders.mockResolvedValue({ data: [folder(1, "operations"), folder(2, "sales")] });
    auth.user = { id: 1 };
    auth.loading = false;
});

describe("what the rail shows", () => {
    it("lists the channels as links", async () => {
        render(<SidebarChannels collapsed={false} />);
        await waitFor(() => expect(screen.getByText("operations")).toBeTruthy());
        expect(screen.getByText("operations").closest("a")?.getAttribute("href")).toBe("/channels/1");
        expect(screen.getByText("sales")).toBeTruthy();
    });

    it("still offers the plus with no channels", async () => {
        // An account with no channels needs the door to make one more than
        // one with eight does. The heading stays; only the rows are absent.
        listFolders.mockResolvedValue({ data: [] });
        render(<SidebarChannels collapsed={false} />);
        await waitFor(() => expect(listFolders).toHaveBeenCalled());
        expect(screen.getByLabelText("New chat")).toBeTruthy();
        expect(screen.queryByRole("link", { name: "operations" })).toBeNull();
    });

    it("renders nothing when collapsed", async () => {
        const { container } = render(<SidebarChannels collapsed={true} />);
        await waitFor(() => expect(listFolders).toHaveBeenCalled());
        expect(container.textContent).toBe("");
    });

    it("caps the list and says how many more there are", async () => {
        listFolders.mockResolvedValue({
            data: Array.from({ length: CHANNEL_LIMIT + 3 }, (_, i) => folder(i + 1, `c${i + 1}`)),
        });
        render(<SidebarChannels collapsed={false} />);
        await waitFor(() => expect(screen.getByText("3 more")).toBeTruthy());
        expect(screen.queryByText(`c${CHANNEL_LIMIT + 1}`)).toBeNull();
    });

    it("does not take the sidebar down when the request fails", async () => {
        listFolders.mockRejectedValue(new Error("down"));
        render(<SidebarChannels collapsed={false} />);
        await waitFor(() => expect(listFolders).toHaveBeenCalled());
        // No rows, no error, and the plus is still there: a failed read
        // costs the list, not the door.
        expect(screen.getByLabelText("New chat")).toBeTruthy();
    });
});

describe("it waits for auth", () => {
    it("does not fetch while auth is still loading", () => {
        auth.loading = true;
        render(<SidebarChannels collapsed={false} />);
        expect(listFolders).not.toHaveBeenCalled();
    });

    it("does not fetch with no user", () => {
        auth.user = null;
        render(<SidebarChannels collapsed={false} />);
        expect(listFolders).not.toHaveBeenCalled();
    });

    it("fetches exactly once when auth is ready", async () => {
        render(<SidebarChannels collapsed={false} />);
        await waitFor(() => expect(screen.getByText("operations")).toBeTruthy());
        expect(listFolders).toHaveBeenCalledTimes(1);
    });
});
