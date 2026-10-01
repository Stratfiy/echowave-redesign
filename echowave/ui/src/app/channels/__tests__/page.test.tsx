/**
 * /channels was a 404: only /channels/[folderId] existed, so the rail's
 * "Channels" heading and its "N more" row led nowhere. These pin the list
 * page's four states and its door to a new channel.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listFolders = vi.hoisted(() => vi.fn());
const listWorkflows = vi.hoisted(() => vi.fn());
const auth = vi.hoisted(() => ({ user: { id: 1 } as { id: number } | null, loading: false }));

vi.mock("next/navigation", () => ({ usePathname: () => "/channels" }));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));
vi.mock("@/client/sdk.gen", () => ({
    listFoldersApiV1FolderGet: listFolders,
    getWorkflowsApiV1WorkflowFetchGet: listWorkflows,
}));
vi.mock("@/components/layout/NewChatDialog", () => ({
    NewChatDialog: () => <div role="dialog">new channel dialog</div>,
}));

import ChannelsPage from "../page";

const folder = (id: number, name: string) => ({ id, name, created_at: "2026-09-13T00:00:00Z" });

beforeEach(() => {
    listFolders.mockReset();
    listWorkflows.mockReset();
    listFolders.mockResolvedValue({ data: [folder(1, "Logicorp quote desk"), folder(2, "sales")] });
    listWorkflows.mockResolvedValue({
        data: [
            { id: 10, name: "Quoter", folder_id: 1 },
            { id: 11, name: "Checker", folder_id: 1 },
            { id: 12, name: "Loner", folder_id: null },
        ],
    });
    auth.user = { id: 1 };
    auth.loading = false;
});

describe("/channels", () => {
    it("lists every channel as a link to its page, with its agent count", async () => {
        render(<ChannelsPage />);
        await waitFor(() => expect(screen.getByText("Logicorp quote desk")).toBeTruthy());
        expect(screen.getByText("Logicorp quote desk").closest("a")?.getAttribute("href")).toBe("/channels/1");
        expect(screen.getByText("sales").closest("a")?.getAttribute("href")).toBe("/channels/2");
        expect(screen.getByText("2 agents")).toBeTruthy();
        expect(screen.getByText("0 agents")).toBeTruthy();
    });

    it("shows an empty state that still offers a new channel", async () => {
        listFolders.mockResolvedValue({ data: [] });
        render(<ChannelsPage />);
        await waitFor(() => expect(screen.getByText("No channels yet")).toBeTruthy());
        // One in the header, one in the empty state.
        expect(screen.getAllByRole("button", { name: /new channel/i }).length).toBe(2);
    });

    it("says so when the channels cannot be loaded", async () => {
        listFolders.mockResolvedValue({ error: { detail: "nope" } });
        render(<ChannelsPage />);
        await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
        expect(screen.queryByText("No channels yet")).toBeNull();
    });

    it("still lists channels when the agent counts fail", async () => {
        listWorkflows.mockResolvedValue({ error: { detail: "nope" } });
        render(<ChannelsPage />);
        await waitFor(() => expect(screen.getByText("sales")).toBeTruthy());
        expect(screen.queryByText(/agents?$/)).toBeNull();
    });

    it("opens the new-channel dialog from the header action", async () => {
        render(<ChannelsPage />);
        await waitFor(() => expect(screen.getByText("sales")).toBeTruthy());
        expect(screen.queryByRole("dialog")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: /new channel/i }));
        expect(screen.getByRole("dialog")).toBeTruthy();
    });

    it("waits for auth before fetching", () => {
        auth.loading = true;
        render(<ChannelsPage />);
        expect(listFolders).not.toHaveBeenCalled();
        expect(screen.getByText(/loading channels/i)).toBeTruthy();
    });
});
