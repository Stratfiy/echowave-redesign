/**
 * Two screens in this product were called Tools: the shop's ready-made ones
 * and the ones this account built. Somebody looking for the second found the
 * first, and neither page said which it was.
 *
 * This one is "Your tools", and it names the other and links to it.
 */

import { render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listTools = vi.hoisted(() => vi.fn());

vi.mock("@/client/sdk.gen", () => ({
    listToolsApiV1ToolsGet: listTools,
    createToolApiV1ToolsPost: vi.fn(),
    deleteToolApiV1ToolsToolUuidDelete: vi.fn(),
    unarchiveToolApiV1ToolsToolUuidUnarchivePost: vi.fn(),
    // The Integrations tab strip asks whether the dialer import is on.
}));
vi.mock("@/lib/auth", () => ({
    useAuth: () => ({
        user: { id: 1 },
        loading: false,
        getAccessToken: async () => "token",
        redirectToLogin: vi.fn(),
    }),
}));
vi.mock("@/components/ConfirmDialog", () => ({
    useConfirm: () => ({ confirm: vi.fn(), dialog: null }),
}));
vi.mock("next/navigation", () => ({
    useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
    usePathname: () => "/tools",
    useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/hooks/useOwnKeysAllowed", () => ({ useOwnKeysAllowed: () => false }));

import ToolsPage from "../page";

beforeEach(() => {
    listTools.mockReset();
    listTools.mockResolvedValue({ data: [] });
});

describe("the account's own tools", () => {
    it("says whose tools these are, and where the ready-made ones live", async () => {
        render(<ToolsPage />);
        expect((await screen.findAllByText("Your tools")).length).toBeGreaterThan(0);
        const shop = await screen.findByText("the Marketplace");
        expect(shop.closest("a")?.getAttribute("href")).toBe("/marketplace/tools");
    });
});
