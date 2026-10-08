/**
 * Settings -> Notifications (screen 21). The browser is asked only after
 * Enable push and never nagged after a denial; both the app choice and the
 * browser state are shown; a channel that cannot work says why; a stale
 * save is a conflict, not an overwrite.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NotificationsView } from "@/client/types.gen";

const api = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn(), add: vi.fn(), remove: vi.fn(), test: vi.fn() }));
const push = vi.hoisted(() => ({ permission: "default" as string, enable: vi.fn() }));
const flags = vi.hoisted(() => ({ identity_notifications: true }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]) }));
vi.mock("@/lib/webPush", () => ({ pushPermission: () => push.permission, enablePush: push.enable }));
vi.mock("@/client/sdk.gen", () => ({
    myNotificationsApiV1MeNotificationsGet: api.get,
    saveNotificationsApiV1MeNotificationsPut: api.save,
    addPushSubscriptionApiV1MePushSubscriptionsPost: api.add,
    removePushSubscriptionApiV1MePushSubscriptionsSubscriptionIdDelete: api.remove,
    testNotificationApiV1MeNotificationsTestPost: api.test,
}));

import NotificationsPage from "../page";

const VIEW: NotificationsView = {
    channels: { push: false, email: false, channel: false },
    topics: {
        reminders: { on: true, snoozed_until: null },
        approvals: { on: true, snoozed_until: null },
        task_updates: { on: true, snoozed_until: null },
        mail: { on: true, snoozed_until: null },
        suggestions: { on: false, snoozed_until: null },
    },
    quiet_start: null,
    quiet_end: null,
    private_previews: true,
    revision: 0,
    topic_list: [
        { name: "reminders", label: "Reminders you asked for", requested: true },
        { name: "suggestions", label: "Suggestions from Decibyl", requested: false },
    ],
    availability: {
        in_app: { available: false, reason: "The bell in the app is shared by your workspace, so personal notices are not put there." },
        push: { available: true, reason: null },
        email: { available: false, reason: "Email is not set up on this deployment yet." },
        channel: { available: false, reason: "Link WhatsApp, Telegram, Slack or Teams first." },
    },
    devices: [],
    push_public_key: "BKey",
    suggestion_daily_cap: 2,
};

beforeEach(() => {
    api.get.mockResolvedValue({ data: VIEW });
    push.permission = "default";
});
afterEach(() => {
    cleanup();
    Object.values(api).forEach((fn) => fn.mockReset());
    push.enable.mockReset();
    flags.identity_notifications = true;
});

describe("Notifications", () => {
    it("shows nothing of it while switched off", () => {
        flags.identity_notifications = false;
        render(<NotificationsPage />);
        expect(screen.getByText("Not switched on yet")).toBeTruthy();
        expect(api.get).not.toHaveBeenCalled();
    });

    it("never asks the browser on load, and says why a channel cannot work", async () => {
        render(<NotificationsPage />);
        await screen.findByText("Where you are told");
        expect(push.enable).not.toHaveBeenCalled();
        expect(screen.getByText("Email is not set up on this deployment yet.")).toBeTruthy();
        expect(screen.getByText(/shared by your workspace/)).toBeTruthy();
        expect(screen.getByText("The browser has not been asked yet.")).toBeTruthy();
        expect(screen.getByText("Optional. At most 2 a day, and never in quiet hours.")).toBeTruthy();
        expect(screen.getByText("Keeps the time you asked for, even in quiet hours.")).toBeTruthy();
    });

    it("asks only after Enable push, then adds this browser", async () => {
        push.enable.mockResolvedValue({ endpoint: "https://fcm.googleapis.com/x", keys: { p256dh: "k", auth: "a" }, device_label: "Chrome on Android" });
        api.add.mockResolvedValue({ data: { ...VIEW, devices: [{ id: 3, label: "Chrome on Android", created_at: "2026-10-07T00:00:00Z", state: "active" }] } });
        render(<NotificationsPage />);
        fireEvent.click(await screen.findByRole("button", { name: /Enable push/ }));
        await waitFor(() => expect(api.add).toHaveBeenCalled());
        expect(push.enable).toHaveBeenCalledWith("BKey");
        expect(await screen.findByText("Chrome on Android")).toBeTruthy();
    });

    it("after a denial it explains the browser setting and does not offer to ask again", async () => {
        push.permission = "denied";
        render(<NotificationsPage />);
        await screen.findByText("Where you are told");
        expect(screen.queryByRole("button", { name: /Enable push/ })).toBeNull();
        expect(screen.getByText(/Blocked in this browser's settings/)).toBeTruthy();
    });

    it("a stale save is a conflict and keeps the draft", async () => {
        api.save.mockResolvedValue({ error: { detail: { message: "Changed in another window." } }, response: { status: 409 } });
        render(<NotificationsPage />);
        fireEvent.click(await screen.findByLabelText("Private previews"));
        fireEvent.click(await screen.findByRole("button", { name: "Save" }));
        await waitFor(() => expect(screen.getByText(/Someone else changed this/)).toBeTruthy());
        expect(api.save.mock.calls[0][0].body.private_previews).toBe(false);
    });
});
