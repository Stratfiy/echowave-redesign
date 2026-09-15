/**
 * The two shelves: bots filed by industry and function, tools by category,
 * and a category card that filters the rows under it.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
    get: vi.fn(),
    connectors: vi.fn(),
    connect: vi.fn(),
}));
vi.mock("@/client/client.gen", () => ({ client: { get: api.get } }));
vi.mock("@/client/sdk.gen", () => ({
    listConnectorsApiV1ConnectorsGet: api.connectors,
    startConnectingApiV1ConnectorsSlugConnectPost: api.connect,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("next/navigation", () => ({
    usePathname: () => "/marketplace",
    useRouter: () => ({ push: vi.fn() }),
}));

import { MarketplaceScreen } from "../MarketplaceScreen";

const TEMPLATES = {
    templates: [
        {
            id: "clinic_appointment",
            name: "Clinic front desk",
            vertical: "Healthcare — clinics",
            industry: "Healthcare",
            function: "Answer calls",
            direction: "inbound",
            summary: "Books and confirms appointments.",
            languages: ["en", "ta"],
        },
        {
            id: "lending_payment_reminder",
            name: "Loan payment reminder",
            vertical: "Lending — NBFCs",
            industry: "Lending",
            function: "Collect payments",
            direction: "outbound",
            summary: "Reminds borrowers before the due date.",
            languages: ["hi"],
        },
    ],
};

const connector = (slug: string, name: string) => ({
    slug,
    name,
    description: `${name} for business`,
    logo: null,
    setup: "one_click",
    tools_count: 3,
    connected: false,
});

const CATALOGUE = {
    available: true,
    popular: [connector("whatsapp", "WhatsApp")],
    groups: [
        { group: "Messaging", connectors: [connector("whatsapp", "WhatsApp"), connector("slack", "Slack")] },
        { group: "Money", connectors: [connector("razorpay", "Razorpay")] },
    ],
    other: [connector("odd", "Odd one")],
    connected_count: 0,
    total: 4,
};

beforeEach(() => {
    api.get.mockReset();
    api.connectors.mockReset();
    api.get.mockResolvedValue({ data: TEMPLATES });
    api.connectors.mockResolvedValue({ data: CATALOGUE });
});

describe("the bot shelf", () => {
    it("files bots by industry and by function, and a card hires one", async () => {
        render(<MarketplaceScreen kind="bots" />);
        expect(await screen.findByText("Clinic front desk")).toBeTruthy();
        // Industry tiles and function pills, each with its count.
        expect(screen.getByRole("button", { name: /Healthcare\s*1 bot/ })).toBeTruthy();
        expect(screen.getByRole("button", { name: /Lending\s*1 bot/ })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Collect payments · 1" })).toBeTruthy();
        // Add goes to the first-agent flow with the template chosen.
        expect(
            screen.getByRole("link", { name: /Add Clinic front desk/ }).getAttribute("href"),
        ).toBe("/start?template=clinic_appointment");
    });

    it("an industry tile filters the rows, and pressing it again clears", async () => {
        render(<MarketplaceScreen kind="bots" />);
        await screen.findByText("Clinic front desk");
        const tile = screen.getByRole("button", { name: /Lending\s*1 bot/ });
        fireEvent.click(tile);
        expect(screen.queryByText("Clinic front desk")).toBeNull();
        expect(screen.getByText("Loan payment reminder")).toBeTruthy();
        expect(tile.getAttribute("aria-pressed")).toBe("true");
        fireEvent.click(tile);
        expect(screen.getByText("Clinic front desk")).toBeTruthy();
    });

    it("search narrows the shelf", async () => {
        render(<MarketplaceScreen kind="bots" />);
        await screen.findByText("Clinic front desk");
        fireEvent.change(screen.getByLabelText("Find a bot"), { target: { value: "borrowers" } });
        expect(screen.queryByText("Clinic front desk")).toBeNull();
        expect(screen.getByText("Loan payment reminder")).toBeTruthy();
    });
});

describe("the tool shelf", () => {
    it("shows categories with counts, the most asked for, and every group", async () => {
        render(<MarketplaceScreen kind="tools" />);
        expect(await screen.findByRole("button", { name: /Messaging\s*2 tools/ })).toBeTruthy();
        expect(screen.getByRole("button", { name: /Money\s*1 tool/ })).toBeTruthy();
        expect(screen.getByText("Most asked for")).toBeTruthy();
        expect(screen.getByText("Razorpay")).toBeTruthy();
        // The long tail is a door, never dropped.
        expect(screen.getByText("1 more tool")).toBeTruthy();
    });

    it("a category card narrows to that category", async () => {
        render(<MarketplaceScreen kind="tools" />);
        fireEvent.click(await screen.findByRole("button", { name: /Money\s*1 tool/ }));
        await waitFor(() => expect(screen.queryByText("Slack")).toBeNull());
        expect(screen.getByText("Razorpay")).toBeTruthy();
    });

    it("says when connecting apps is switched off", async () => {
        api.connectors.mockResolvedValue({
            data: { available: false, popular: [], groups: [], other: [], connected_count: 0, total: 0 },
        });
        render(<MarketplaceScreen kind="tools" />);
        expect(await screen.findByText(/not switched on/)).toBeTruthy();
    });
});
