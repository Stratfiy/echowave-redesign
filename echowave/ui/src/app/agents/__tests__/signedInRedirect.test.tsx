/**
 * "Agents" meant two places: the sidebar row went to /workflow (your agents)
 * and /agents was the public marketplace with no rail. A signed-in person who
 * opens /agents now lands on their own agents; a visitor still gets the
 * public marketplace.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { signedInAgentsDestination } from "@/lib/agentsRoute";

const serverUser = vi.hoisted(() => vi.fn());
const redirect = vi.hoisted(() =>
    vi.fn((to: string) => {
        throw new Error(`NEXT_REDIRECT ${to}`);
    }),
);

vi.mock("server-only", () => ({}));
vi.mock("@/lib/auth/server", () => ({ getServerUser: serverUser }));
vi.mock("next/navigation", () => ({ redirect }));
vi.mock("@/lib/publicMarketplace", () => ({
    fetchShelf: vi.fn(async () => ({
        jobs: ["Sales"],
        packs: [{ slug: "quoter", name: "Quoter", job: "Sales", summary: "Writes quotes.", badges: [] }],
    })),
}));

import PublicMarketplacePage from "../page";

const open = (params: { q?: string; job?: string } = {}) =>
    PublicMarketplacePage({ searchParams: Promise.resolve(params) });

beforeEach(() => {
    serverUser.mockReset();
    redirect.mockClear();
});

describe("where a signed-in person lands from /agents", () => {
    it("sends bare /agents to their own agents", () => {
        expect(signedInAgentsDestination({})).toBe("/workflow");
        expect(signedInAgentsDestination({ q: "  " })).toBe("/workflow");
    });

    it("sends a marketplace search to the in-app marketplace", () => {
        expect(signedInAgentsDestination({ q: "quotes" })).toBe("/marketplace");
        expect(signedInAgentsDestination({ job: "Sales" })).toBe("/marketplace");
    });
});

describe("/agents", () => {
    it("redirects a signed-in person to /workflow", async () => {
        serverUser.mockResolvedValue({ id: "1" });
        await expect(open()).rejects.toThrow("NEXT_REDIRECT /workflow");
        expect(redirect).toHaveBeenCalledWith("/workflow");
    });

    it("redirects a signed-in search to /marketplace", async () => {
        serverUser.mockResolvedValue({ id: "1" });
        await expect(open({ job: "Sales" })).rejects.toThrow("NEXT_REDIRECT /marketplace");
    });

    it("shows the public marketplace to a visitor", async () => {
        serverUser.mockResolvedValue(null);
        render(await open());
        expect(redirect).not.toHaveBeenCalled();
        expect(screen.getByRole("heading", { name: "Marketplace" })).toBeTruthy();
        expect(screen.getByText("Quoter").closest("a")?.getAttribute("href")).toBe("/agents/quoter");
    });

    it("shows the public marketplace when the sign-in check fails", async () => {
        serverUser.mockRejectedValue(new Error("backend down"));
        render(await open());
        expect(redirect).not.toHaveBeenCalled();
        expect(screen.getByRole("heading", { name: "Marketplace" })).toBeTruthy();
    });
});
