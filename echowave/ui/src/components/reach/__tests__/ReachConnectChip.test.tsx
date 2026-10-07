/**
 * The connect chip (stream `reach`): an outside tool or ordering app is
 * connected here in the thread, never on another screen; an app Decibyl
 * cannot reach yet says "needs setup" with no button that pretends.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const connect = vi.hoisted(() => vi.fn());
const refresh = vi.hoisted(() => vi.fn());
vi.mock("@/client/sdk.gen", () => ({
    connectApiV1ReachConnectionsPost: connect,
    refreshConnectionApiV1ReachConnectionsConnectionIdRefreshPost: refresh,
}));

import { ReachConnectChip } from "../ReachConnectChip";

const event = (payload: Record<string, unknown>) =>
    ({
        id: 7,
        at: "2026-10-08T00:00:00Z",
        kind: "reach_connect_offered",
        actor: "agent",
        summary: "Connect Notes",
        payload,
        is_deliverable: false,
        workflow_id: null,
        workflow_run_id: null,
        folder_id: null,
    }) as never;

const connection = (over: Record<string, unknown> = {}) => ({
    id: "c-1",
    kind: "tool",
    provider: "notes",
    name: "Notes",
    server_url: "https://notes.example.com/mcp",
    auth: "token",
    status: "connected",
    tools: [
        { name: "search_notes", read: true },
        { name: "create_note", read: false },
    ],
    ...over,
});

beforeEach(() => {
    connect.mockReset();
    refresh.mockReset();
    vi.spyOn(window, "open").mockImplementation(() => null);
});

describe("an outside tool", () => {
    it("connects from the chip with an address and a token, and says what it brings", async () => {
        connect.mockResolvedValue({ data: { connection: connection(), authorize_url: null } });
        render(<ReachConnectChip event={event({ reach_kind: "tool", name: "Notes", state: "available", server_url: "https://notes.example.com/mcp" })} />);
        expect(screen.getByText("Connects your own account. Only you can see or use it.")).toBeTruthy();
        fireEvent.change(screen.getByLabelText(/Token/), { target: { value: "secret" } });
        fireEvent.click(screen.getByRole("button", { name: "Connect" }));
        await waitFor(() => expect(connect).toHaveBeenCalled());
        expect(connect.mock.calls[0][0].body).toEqual({
            kind: "tool",
            name: "Notes",
            server_url: "https://notes.example.com/mcp",
            token: "secret",
        });
        expect(await screen.findByText(/Notes is connected: 2 tools \(1 read-only\)/)).toBeTruthy();
        expect(screen.getByTestId("reach-connect-chip").getAttribute("data-state")).toBe("connected");
    });

    it("shows the server's refusal in words", async () => {
        connect.mockResolvedValue({ error: { detail: "That token was not accepted by the server." } });
        render(<ReachConnectChip event={event({ reach_kind: "tool", name: "Notes", state: "available", server_url: "https://x.example.com/mcp" })} />);
        fireEvent.click(screen.getByRole("button", { name: "Connect" }));
        expect((await screen.findByRole("alert")).textContent).toContain("not accepted");
    });
});

describe("an ordering app", () => {
    it("opens the app's own sign-in in a new tab and checks from here", async () => {
        connect.mockResolvedValue({
            data: { connection: connection({ kind: "ordering", name: "Zomato", status: "pending", tools: [] }), authorize_url: "https://auth.example.com/authorize?x=1" },
        });
        refresh.mockResolvedValue({ data: connection({ kind: "ordering", name: "Zomato" }) });
        render(<ReachConnectChip event={event({ reach_kind: "ordering", provider: "zomato", name: "Zomato", state: "available" })} />);
        fireEvent.click(screen.getByRole("button", { name: "Sign in to Zomato" }));
        await waitFor(() => expect(window.open).toHaveBeenCalledWith("https://auth.example.com/authorize?x=1", "_blank", "noopener,noreferrer"));
        expect(connect.mock.calls[0][0].body).toEqual({ kind: "ordering", provider: "zomato" });
        fireEvent.click(await screen.findByRole("button", { name: "I've signed in" }));
        expect(await screen.findByText("Zomato is connected to your account.")).toBeTruthy();
    });

    it("says needs setup, with the reason and no button", () => {
        render(
            <ReachConnectChip
                event={event({
                    reach_kind: "ordering",
                    provider: "swiggy",
                    name: "Swiggy",
                    state: "needs_setup",
                    reason: "Swiggy needs Builders Club access, which Decibyl is waiting for, so it cannot be connected yet.",
                })}
            />,
        );
        expect(screen.getByText("Needs setup")).toBeTruthy();
        expect(screen.getByText(/Builders Club access/)).toBeTruthy();
        expect(screen.queryByRole("button")).toBeNull();
    });
});
