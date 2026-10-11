/**
 * Connect Instagram, here in the thread: the button opens Instagram's own
 * sign-in in a new tab, "I've signed in" checks from here, and an account
 * already connected says so instead of offering the button again.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const connect = vi.hoisted(() => vi.fn());
const account = vi.hoisted(() => vi.fn());
vi.mock("@/client/sdk.gen", () => ({
    connectApiV1InstagramConnectPost: connect,
    getAccountApiV1InstagramAccountGet: account,
}));
vi.mock("@/lib/auth", () => ({
    useAuth: () => ({ user: { id: 1 }, loading: false }),
}));

import { InstagramConnectChip } from "../InstagramConnectChip";

const event = {
    id: 9,
    at: "2026-10-11T00:00:00Z",
    kind: "instagram_connect_offered",
    actor: "agent",
    summary: "Connect Instagram",
    payload: { why: "" },
    is_deliverable: false,
    workflow_id: null,
    workflow_run_id: null,
    folder_id: null,
} as never;

beforeEach(() => {
    connect.mockReset();
    account.mockReset();
    vi.restoreAllMocks();
    vi.spyOn(window, "open").mockImplementation(() => null);
});

describe("connecting Instagram", () => {
    it("opens Instagram's sign-in and then checks from the thread", async () => {
        account.mockResolvedValueOnce({ data: { configured: true, connected: false } });
        connect.mockResolvedValue({ data: { authorize_url: "https://www.instagram.com/oauth/authorize?x=1" } });
        render(<InstagramConnectChip event={event} />);
        fireEvent.click(screen.getByRole("button", { name: "Connect Instagram" }));
        await waitFor(() =>
            expect(window.open).toHaveBeenCalledWith(
                "https://www.instagram.com/oauth/authorize?x=1",
                "_blank",
                "noopener,noreferrer",
            ),
        );
        account.mockResolvedValueOnce({
            data: { configured: true, connected: true, username: "lotusdental", answered_by: null },
        });
        fireEvent.click(await screen.findByRole("button", { name: "I've signed in" }));
        expect(await screen.findByText(/@lotusdental is connected/)).toBeTruthy();
    });

    it("says it is not connected yet rather than pretending", async () => {
        account.mockResolvedValue({ data: { configured: true, connected: false } });
        connect.mockResolvedValue({ data: { authorize_url: "https://www.instagram.com/oauth/authorize" } });
        render(<InstagramConnectChip event={event} />);
        fireEvent.click(screen.getByRole("button", { name: "Connect Instagram" }));
        fireEvent.click(await screen.findByRole("button", { name: "I've signed in" }));
        expect(await screen.findByRole("alert")).toBeTruthy();
    });

    it("shows a refusal in words", async () => {
        account.mockResolvedValue({ data: { configured: true, connected: false } });
        connect.mockResolvedValue({ error: { detail: "Only a workspace admin can connect or disconnect Instagram." } });
        render(<InstagramConnectChip event={event} />);
        fireEvent.click(screen.getByRole("button", { name: "Connect Instagram" }));
        expect(await screen.findByText(/Only a workspace admin/)).toBeTruthy();
        expect(window.open).not.toHaveBeenCalled();
    });

    it("an account already connected shows who answers", async () => {
        account.mockResolvedValue({
            data: { configured: true, connected: true, username: "lotusdental", answered_by: "Front desk" },
        });
        render(<InstagramConnectChip event={event} />);
        expect(await screen.findByText(/Front desk answers its messages/)).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Connect Instagram" })).toBeNull();
    });
});
