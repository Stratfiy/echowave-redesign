import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MoreAppsPage from "../more/page";
import AppsPage from "../page";

const api = vi.hoisted(() => ({ list: vi.fn(), activity: vi.fn(), connect: vi.fn() }));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/client/sdk.gen", () => ({
    listConnectorsApiV1ConnectorsGet: api.list,
    connectorActivityApiV1ConnectorsActivityGet: api.activity,
    startConnectingApiV1ConnectorsSlugConnectPost: api.connect,
}));
vi.mock("@/components/integrations/GoogleCalendarConnect", () => ({
    GoogleCalendarConnect: () => <div>calendar</div>,
}));
vi.mock("@/components/integrations/IntegrationsTabs", () => ({
    IntegrationsTabs: () => null,
}));

const app = (slug: string, name: string) => ({
    slug,
    name,
    description: `${name} does things`,
    logo: null,
    setup: "one_click",
    tools_count: 12,
    connected: false,
});

const FULL = {
    available: true,
    popular: [app("whatsapp", "WhatsApp"), app("gmail", "Gmail")],
    groups: [{ group: "Email", connectors: [app("gmail", "Gmail")] }],
    other: [app("mystery", "Mystery"), app("oddity", "Oddity")],
    connected_count: 0,
    total: 4,
};

beforeEach(() => {
    api.list.mockReset();
    api.activity.mockResolvedValue({ data: [] });
});

describe("the catalogue leads with what people ask for", () => {
    it("shows the collection above the categories", async () => {
        api.list.mockResolvedValue({ data: FULL });
        render(<AppsPage />);
        expect(await screen.findByText("Most asked for")).toBeTruthy();
        expect(screen.getByText("WhatsApp")).toBeTruthy();
    });

    it("keeps a favourite in its category as well", async () => {
        // Both, deliberately. Somebody scanning uses the collection; somebody
        // who opened Email still expects Gmail there.
        api.list.mockResolvedValue({ data: FULL });
        render(<AppsPage />);
        await screen.findByText("Most asked for");
        expect(screen.getByText("Email")).toBeTruthy();
        expect(screen.getAllByText("Gmail").length).toBe(2);
    });

    it("drops the collection while searching rather than ranking twice", async () => {
        api.list.mockResolvedValue({ data: FULL });
        render(<AppsPage />);
        await screen.findByText("Most asked for");

        api.list.mockResolvedValue({ data: { ...FULL, popular: [] } });
        fireEvent.change(screen.getByPlaceholderText(/Search 1,500/), {
            target: { value: "mystery" },
        });
        await waitFor(() => expect(screen.queryByText("Most asked for")).toBeNull());
    });
});

describe("the long tail is reachable rather than dropped", () => {
    it("offers a door to it instead of ending the page on it", async () => {
        api.list.mockResolvedValue({ data: FULL });
        render(<AppsPage />);
        expect(await screen.findByText("2 more apps")).toBeTruthy();
        expect(
            screen.getByRole("link", { name: /browse all/i }).getAttribute("href"),
        ).toBe("/integrations/apps/more");
    });

    it("renders the matches inline while searching", async () => {
        // Somebody who typed a name wants the match, not a link to go looking
        // for it.
        api.list.mockResolvedValue({ data: FULL });
        render(<AppsPage />);
        await screen.findByText("2 more apps");

        fireEvent.change(screen.getByPlaceholderText(/Search 1,500/), {
            target: { value: "mystery" },
        });
        expect(await screen.findByText("Mystery")).toBeTruthy();
        expect(screen.queryByText("2 more apps")).toBeNull();
    });

    it("does not report a working vendor as broken when only Other has apps", async () => {
        // The regression the API bug taught: judging emptiness on groups alone
        // calls a catalogue unloadable over a result we actually have. The
        // apps stay behind the door here — the point is that the door exists
        // instead of an error card.
        api.list.mockResolvedValue({
            data: { ...FULL, groups: [], popular: [] },
        });
        render(<AppsPage />);
        expect(await screen.findByText("2 more apps")).toBeTruthy();
        expect(screen.queryByText(/could not be loaded/)).toBeNull();
        expect(screen.queryByText(/Nothing matches/)).toBeNull();
    });
});

describe("the more page", () => {
    it("lists only the uncategorised apps, with a way back", async () => {
        api.list.mockResolvedValue({ data: FULL });
        render(<MoreAppsPage />);
        expect(await screen.findByText("Mystery")).toBeTruthy();
        expect(screen.getByText("Oddity")).toBeTruthy();
        // Not the categorised ones — those are on the main screen.
        expect(screen.queryByText("WhatsApp")).toBeNull();
        expect(
            screen.getByRole("link", { name: /apps/i }).getAttribute("href"),
        ).toBe("/integrations/apps");
    });

    it("says a failed read is ours, not the account's", async () => {
        api.list.mockResolvedValue({ error: { detail: "boom" } });
        render(<MoreAppsPage />);
        expect(await screen.findByText(/could not be loaded/)).toBeTruthy();
        expect(screen.getByText(/This is us, not you/)).toBeTruthy();
    });
});
