/**
 * The trust page answers a security review before anybody signs up, so it
 * must render for a visitor with no session, and every fact on it must come
 * from the server rather than from the page's own source.
 */

import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiClient", () => ({ resolveBrowserBackendUrl: () => "https://api.test" }));

import TrustPage from "../page";

const BODY = {
    region: "ap-south-1",
    retention: { recording_days: 90, transcript_days: 365, subprocessor_window_days: 90 },
    subprocessors: [
        { name: "deepgram", purpose: "Speech recognition", data: "Call audio", basis: "configured" },
        {
            name: "Amazon Web Services",
            purpose: "Application hosting",
            data: "All customer and call data at rest",
            basis: "infrastructure",
        },
    ],
    grievance_officer: { name: null, email: "privacy@decibyl.ai", address: null },
};

beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => BODY }) as never;
});

describe("the trust page", () => {
    it("asks the public endpoint, with no token", async () => {
        render(<TrustPage />);
        await waitFor(() => expect(global.fetch).toHaveBeenCalled());
        const [url, init] = (global.fetch as unknown as { mock: { calls: [string, RequestInit?][] } })
            .mock.calls[0];
        expect(url).toBe("https://api.test/api/v1/public/trust");
        expect(init).toBeUndefined();
    });

    it("names the region as a place, and the retention the server gave", async () => {
        render(<TrustPage />);
        expect(await screen.findByText("Mumbai, India")).toBeTruthy();
        expect(screen.getByText(/Recordings: 90 days/)).toBeTruthy();
        expect(screen.getByText(/Transcripts and call context: 365 days/)).toBeTruthy();
    });

    it("lists every sub-processor with why it is on the list", async () => {
        render(<TrustPage />);
        expect(await screen.findByText("deepgram")).toBeTruthy();
        expect(screen.getByText("Amazon Web Services")).toBeTruthy();
        expect(screen.getByText("We hold a key for it")).toBeTruthy();
        expect(screen.getByText("Runs the platform itself")).toBeTruthy();
    });

    it("prints an unknown region as itself rather than guessing a city", async () => {
        global.fetch = vi
            .fn()
            .mockResolvedValue({ ok: true, json: async () => ({ ...BODY, region: "xx-moon-9" }) }) as never;
        render(<TrustPage />);
        expect(await screen.findByText("xx-moon-9")).toBeTruthy();
    });

    it("says so, calmly, when the list cannot be read", async () => {
        global.fetch = vi.fn().mockRejectedValue(new Error("down")) as never;
        render(<TrustPage />);
        expect(await screen.findByRole("alert")).toBeTruthy();
    });
});
