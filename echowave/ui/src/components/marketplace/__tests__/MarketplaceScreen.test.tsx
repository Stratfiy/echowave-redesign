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
    library: vi.fn(),
    connect: vi.fn(),
}));
vi.mock("@/client/client.gen", () => ({ client: { get: api.get } }));
vi.mock("@/client/sdk.gen", () => ({
    listConnectorsApiV1ConnectorsGet: api.connectors,
    getToolLibraryApiV1ToolLibraryGet: api.library,
    startConnectingApiV1ConnectorsSlugConnectPost: api.connect,
    // The workspace's own roles (MP-2), switched off here; their shelf has
    // its own tests in WorkspaceRolesShelf.test.tsx.
    listMyOrganizationsApiV1OrganizationsMineGet: vi.fn().mockResolvedValue({ data: [] }),
}));
const flags = vi.hoisted(() => ({ shell: false }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => name === "shell" && flags.shell }));
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
    flags.shell = false;
    api.get.mockReset();
    api.connectors.mockReset();
    api.library.mockReset();
    api.library.mockResolvedValue({ data: LIBRARY });
    api.get.mockResolvedValue({ data: TEMPLATES });
    api.connectors.mockResolvedValue({ data: CATALOGUE });
});

describe("the agent shelf", () => {
    it("files agents by industry and by function, and a card hires one", async () => {
        render(<MarketplaceScreen kind="bots" />);
        expect(await screen.findByText("Clinic front desk")).toBeTruthy();
        // Industry tiles and function pills, each with its count.
        expect(screen.getByRole("button", { name: /Healthcare\s*1 agent/ })).toBeTruthy();
        expect(screen.getByRole("button", { name: /Lending\s*1 agent/ })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Collect payments · 1" })).toBeTruthy();
        // Add goes to the first-agent flow with the template chosen.
        expect(
            screen.getByRole("link", { name: /Add Clinic front desk/ }).getAttribute("href"),
        ).toBe("/start?template=clinic_appointment");
    });

    it("draws the jobs as pictures", async () => {
        render(<MarketplaceScreen kind="bots" />);
        await screen.findByText("Clinic front desk");
        const strips = screen.getAllByTestId("role-art");
        expect(strips).toHaveLength(2);
        expect(strips[0].querySelector("img")?.getAttribute("src")).toBe("/art/3d/notify-heart.webp");
        expect(strips[1].querySelector("img")?.getAttribute("src")).toBe("/art/3d/wallet.webp");
        expect(screen.getByTestId("hero-art").querySelectorAll("img")).toHaveLength(4);
        // The industry tiles carry their picture instead of the icon.
        const tile = screen.getByRole("button", { name: /Lending\s*1 agent/ });
        expect(tile.querySelector("img")?.getAttribute("src")).toBe("/art/3d/money-bag.webp");
    });

    it("an industry tile filters the rows, and pressing it again clears", async () => {
        render(<MarketplaceScreen kind="bots" />);
        await screen.findByText("Clinic front desk");
        const tile = screen.getByRole("button", { name: /Lending\s*1 agent/ });
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
        fireEvent.change(screen.getByLabelText("Find an agent"), { target: { value: "borrowers" } });
        expect(screen.queryByText("Clinic front desk")).toBeNull();
        expect(screen.getByText("Loan payment reminder")).toBeTruthy();
    });
});

const LIBRARY = {
    catalog: "v1",
    vendors: ["Zoho CRM", "Shopify"],
    tools: [
        { key: "zoho-lead", vendor: "Zoho CRM", display_name: "Create a lead", summary: "File the caller as a lead", tool_name: "create_lead", tool_description: "x", method: "POST", url: "https://x", parameters: [] },
        { key: "shopify-order", vendor: "Shopify", display_name: "Look up an order", summary: "Tell the caller where the order is", tool_name: "order_status", tool_description: "x", method: "GET", url: "https://y", parameters: [] },
    ],
};

describe("the tools shelf", () => {
    it("lists the ready-made tools by the app they act on, and offers to build one", async () => {
        render(<MarketplaceScreen kind="tools" />);
        expect(await screen.findByText("Create a lead")).toBeTruthy();
        expect(screen.getByText("Look up an order")).toBeTruthy();
        expect(screen.getByRole("button", { name: /^Zoho CRM\s*1$/ })).toBeTruthy();
        expect(screen.getByRole("link", { name: "Add Create a lead" }).getAttribute("href")).toBe("/settings/apps?library=zoho-lead");
        expect(screen.getByRole("link", { name: "Build a tool" }).getAttribute("href")).toBe("/settings/apps");
    });

    it("a vendor chip narrows the list", async () => {
        render(<MarketplaceScreen kind="tools" />);
        fireEvent.click(await screen.findByRole("button", { name: /^Shopify\s*1$/ }));
        await waitFor(() => expect(screen.queryByText("Create a lead")).toBeNull());
        expect(screen.getByText("Look up an order")).toBeTruthy();
    });
});

describe("the integrations shelf", () => {
    it("shows category chips with counts, Featured first, every group, and Other", async () => {
        render(<MarketplaceScreen kind="integrations" />);
        expect(await screen.findByRole("button", { name: /^Messaging\s*2$/ })).toBeTruthy();
        expect(screen.getByRole("button", { name: /^Money\s*1$/ })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Featured" })).toBeTruthy();
        expect(screen.getByRole("heading", { name: "Featured" })).toBeTruthy();
        expect(screen.getByText("Razorpay")).toBeTruthy();
        // The long tail is a chip and a section, never dropped.
        expect(screen.getByRole("button", { name: /^Other\s*1$/ })).toBeTruthy();
        expect(screen.getByText("Odd one")).toBeTruthy();
        // Rows, with Add on each app not yet on.
        expect(screen.getAllByRole("button", { name: /^Add / }).length).toBeGreaterThan(0);
    });

    it("a chip narrows to that category", async () => {
        render(<MarketplaceScreen kind="integrations" />);
        fireEvent.click(await screen.findByRole("button", { name: /^Money\s*1$/ }));
        await waitFor(() => expect(screen.queryByText("Slack")).toBeNull());
        expect(screen.getByText("Razorpay")).toBeTruthy();
    });

    it("says when connecting apps is switched off", async () => {
        api.connectors.mockResolvedValue({
            data: { available: false, popular: [], groups: [], other: [], connected_count: 0, total: 0 },
        });
        render(<MarketplaceScreen kind="integrations" />);
        expect(await screen.findByText(/not switched on/)).toBeTruthy();
    });
});
