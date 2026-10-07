/**
 * Settings -> Connections (screen 22). Apps and channels are two sections;
 * each row shows whose it is and its state; a failed read is an error, not
 * "no apps"; access is explained before Connect; disconnecting puts an
 * approval card on the page; a channel says it does not read other messages.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConnectionsResponse } from "@/client/types.gen";

const api = vi.hoisted(() => ({
    list: vi.fn(),
    preview: vi.fn(),
    start: vi.fn(),
    complete: vi.fn(),
    disconnect: vi.fn(),
    cards: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: () => true }));
vi.mock("next/navigation", () => ({
    usePathname: () => "/settings/connections",
    useRouter: () => ({ push: vi.fn() }),
    useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/DecibylAppsSection", () => ({ DecibylAppsSection: () => <div>link an app</div> }));
vi.mock("@/client/sdk.gen", () => ({
    myConnectionsApiV1MeConnectionsGet: api.list,
    previewConnectionApiV1MeConnectionsPreviewGet: api.preview,
    startConnectionApiV1MeConnectionsStartPost: api.start,
    completeConnectionApiV1MeConnectionsConsentsConsentIdCompletePost: api.complete,
    proposeDisconnectApiV1MeConnectionsDisconnectPost: api.disconnect,
    myIdentityCardsApiV1MeIdentityCardsGet: api.cards,
    settleActionApiV1TimelineActionsSettlePost: vi.fn(),
    sayWhetherItArrivedApiV1MeOutcomesEventIdPost: vi.fn(),
}));

import ConnectionsPage from "../page";

const DATA: ConnectionsResponse = {
    is_admin: false,
    apps: {
        state: "ok",
        per_person: true,
        items: [
            {
                id: "ca_1",
                toolkit: "gmail",
                app_name: "Gmail",
                account: "gmail-ca1",
                owner: "you",
                scope: "mine",
                state: "ready",
                access: ["Read Gmail when a task you ask for needs it."],
                can_disconnect: true,
            },
            {
                id: "ca_2",
                toolkit: "slack",
                app_name: "Slack",
                owner: "workspace",
                scope: "workspace",
                state: "expired",
                reason: "The sign-in expired. Reconnect to keep using it.",
                access: [],
                can_disconnect: false,
            },
        ],
    },
    channels: [
        {
            channel: "whatsapp",
            name: "WhatsApp",
            capability: "needs_setup",
            reason: "Set up, and not yet proven with a real message. Linking it sends the first one.",
            delivery_failing: false,
            proactive: "Replies freely for 24 hours after your last message.",
            reads_other_messages: false,
            state: "disconnected",
            linked: [],
        },
    ],
};

beforeEach(() => {
    api.list.mockResolvedValue({ data: DATA });
    api.cards.mockResolvedValue({ data: { cards: [] } });
    window.sessionStorage.clear();
});
afterEach(() => {
    cleanup();
    Object.values(api).forEach((fn) => fn.mockReset());
});

describe("Connections", () => {
    it("shows both sections with whose each is and its state", async () => {
        render(<ConnectionsPage />);
        expect(await screen.findByText("Apps Decibyl may read and act in")).toBeTruthy();
        expect(screen.getByText("Channels you message Decibyl on")).toBeTruthy();
        expect(screen.getByText(/gmail-ca1 · Yours/)).toBeTruthy();
        expect(screen.getByText(/Expired\. The sign-in expired/)).toBeTruthy();
        expect(screen.getByText(/not yet proven with a real message/)).toBeTruthy();
        expect(screen.getByText(/Decibyl sees only what you send it/)).toBeTruthy();
    });

    it("a failed read is an error with Retry, never an empty list", async () => {
        api.list.mockResolvedValue({ error: { detail: "down" } });
        render(<ConnectionsPage />);
        expect(await screen.findByText("Could not load your connections")).toBeTruthy();
        expect(screen.queryByText("No apps connected")).toBeNull();
    });

    it("explains the access before Connect", async () => {
        api.preview.mockResolvedValue({
            data: { toolkit: "notion", app_name: "Notion", access: ["You sign in on Notion's own page. Decibyl never sees your password."], per_person: true, can_connect_for_workspace: false },
        });
        render(<ConnectionsPage />);
        fireEvent.click(await screen.findByRole("button", { name: "notion" }));
        expect(await screen.findByText("Before you connect Notion")).toBeTruthy();
        expect(screen.getByText(/never sees your password/)).toBeTruthy();
        expect(api.start).not.toHaveBeenCalled();
    });

    it("disconnecting puts an approval card on this page", async () => {
        api.disconnect.mockResolvedValue({ data: {} });
        api.cards
            .mockResolvedValueOnce({ data: { cards: [] } })
            .mockResolvedValue({
                data: {
                    cards: [
                        {
                            event_id: 9,
                            action: "disconnect_app",
                            label: "Disconnect Gmail (yours)",
                            effect: "Decibyl stops reading and acting in Gmail (yours).",
                            state: "proposed",
                            version: "v1",
                            revisions: 1,
                            args: { toolkit: "gmail", scope: "mine" },
                            affected: [],
                            needs_person: false,
                        },
                    ],
                },
            });
        render(<ConnectionsPage />);
        fireEvent.click((await screen.findAllByRole("button", { name: /Details/ }))[0]);
        fireEvent.click(screen.getByRole("button", { name: "Disconnect…" }));
        await waitFor(() => expect(api.disconnect).toHaveBeenCalled());
        expect(api.disconnect.mock.calls[0][0].body).toEqual({ scope: "mine", connected_account_id: "ca_1" });
        expect(await screen.findByRole("region", { name: "Review: Disconnect Gmail (yours)" })).toBeTruthy();
    });

    it("a workspace connection is managed by the workspace for a member", async () => {
        render(<ConnectionsPage />);
        fireEvent.click((await screen.findAllByRole("button", { name: /Details/ }))[1]);
        expect(screen.getByText("Managed by your workspace.")).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Disconnect…" })).toBeNull();
    });
});
