/**
 * Settings → Decibyl in your apps.
 *
 * What matters: nothing shows while the workspace has the feature off; an app
 * that is not set up offers no Connect; Connect shows the one-time code with
 * where to send it; a linked app can be unlinked.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), del: vi.fn() }));

vi.mock("@/client/client.gen", () => ({
    client: { get: api.get, post: api.post, delete: api.del },
}));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));

import { DecibylAppsSection } from "../DecibylAppsSection";

const STATE = {
    enabled: true,
    channels: [
        { channel: "whatsapp", available: false },
        { channel: "telegram", available: true },
        { channel: "slack", available: true },
        { channel: "teams", available: false },
    ],
    linked: [{ id: 4, channel: "slack", display_name: "Meera", handle: "AB12" }],
};

beforeEach(() => vi.clearAllMocks());

describe("DecibylAppsSection", () => {
    it("draws nothing while the feature is off", async () => {
        api.get.mockResolvedValue({ data: { ...STATE, enabled: false } });
        const { container } = render(<DecibylAppsSection />);
        await waitFor(() => expect(api.get).toHaveBeenCalled());
        expect(container.textContent).toBe("");
    });

    it("offers Connect only for the apps that are set up", async () => {
        api.get.mockResolvedValue({ data: STATE });
        render(<DecibylAppsSection />);
        expect(await screen.findByRole("button", { name: "Connect Telegram" })).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Connect WhatsApp" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Connect Microsoft Teams" })).toBeNull();
    });

    it("shows the code and where to send it", async () => {
        api.get.mockResolvedValue({ data: STATE });
        api.post.mockResolvedValue({
            data: {
                code: "ABC234",
                link: "https://t.me/decibyl_bot?start=ABC234",
                instructions: "Open the Decibyl bot in Telegram and send ABC234.",
                expires_in_seconds: 600,
            },
        });
        render(<DecibylAppsSection />);
        fireEvent.click(await screen.findByRole("button", { name: "Connect Telegram" }));
        expect(await screen.findByText("ABC234")).toBeTruthy();
        expect(api.post).toHaveBeenCalledWith({
            url: "/api/v1/channel-links/start",
            body: { channel: "telegram" },
        });
        expect(screen.getByText(/expires in 10 minutes/)).toBeTruthy();
    });

    it("unlinks a linked app", async () => {
        api.get.mockResolvedValue({ data: STATE });
        api.del.mockResolvedValue({ data: { unlinked: 4 } });
        render(<DecibylAppsSection />);
        fireEvent.click(await screen.findByRole("button", { name: "Unlink" }));
        await waitFor(() =>
            expect(api.del).toHaveBeenCalledWith({ url: "/api/v1/channel-links/4" }),
        );
    });
});
