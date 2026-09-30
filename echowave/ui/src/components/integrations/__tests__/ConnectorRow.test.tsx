/**
 * Connections per person (WS-1, KAN-196): the row says whose connection an
 * app is, and a member can add one for themselves without an admin. With
 * the flag off the row reads exactly as it did.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ConnectorResponse } from "@/client/types.gen";
import { ConnectorRow } from "@/components/integrations/ConnectorRow";

const flags = { connections_per_person: false };
vi.mock("@/lib/features", () => ({
    useFeature: (name: string) => name === "connections_per_person" && flags.connections_per_person,
}));

const api = {
    connect: vi.fn(),
    connectForMe: vi.fn(),
    tools: vi.fn(),
};
vi.mock("@/client/sdk.gen", () => ({
    startConnectingApiV1ConnectorsSlugConnectPost: (...args: unknown[]) => api.connect(...args),
    startConnectingForMeApiV1ConnectorsSlugConnectMinePost: (...args: unknown[]) => api.connectForMe(...args),
    listAppToolsApiV1ConnectorsSlugToolsGet: (...args: unknown[]) => api.tools(...args),
    syncAppToolsApiV1ConnectorsSlugToolsSyncPost: vi.fn(),
}));

function gmail(overrides: Partial<ConnectorResponse> = {}): ConnectorResponse {
    return {
        slug: "gmail",
        name: "Gmail",
        description: "Mail",
        logo: null,
        setup: "one_click",
        tools_count: 12,
        connected: false,
        setup_url: null,
        also_connectable: false,
        connected_by: null,
        ...overrides,
    };
}

describe("ConnectorRow", () => {
    beforeEach(() => {
        flags.connections_per_person = false;
        api.connect.mockReset();
        api.connectForMe.mockReset();
        api.tools.mockResolvedValue({ data: { app: "gmail", app_name: "Gmail", tools: [] } });
        vi.spyOn(window, "open").mockImplementation(() => null);
    });

    it("with the flag off reads as it always did", () => {
        render(<ConnectorRow connector={gmail({ connected: true })} onConnected={() => {}} />);
        expect(screen.getByText("Added")).toBeTruthy();
        expect(screen.queryByRole("button", { name: /for me/i })).toBeNull();
    });

    it("names whose connection it is: Mine, Workspace, or both", () => {
        flags.connections_per_person = true;
        const { rerender } = render(
            <ConnectorRow connector={gmail({ connected: true, connected_by: "me" })} onConnected={() => {}} />,
        );
        expect(screen.getByText("Mine")).toBeTruthy();
        expect(screen.queryByRole("button", { name: /for me/i })).toBeNull();

        rerender(<ConnectorRow connector={gmail({ connected: true, connected_by: "workspace" })} onConnected={() => {}} />);
        expect(screen.getByText("Workspace")).toBeTruthy();
        // The workspace's Gmail is not this member's: they can still add their own.
        expect(screen.getByRole("button", { name: "Add Gmail for me" })).toBeTruthy();

        rerender(<ConnectorRow connector={gmail({ connected: true, connected_by: "both" })} onConnected={() => {}} />);
        expect(screen.getByText("Mine · Workspace")).toBeTruthy();
        expect(screen.queryByRole("button", { name: /for me/i })).toBeNull();
    });

    it("adds for me through the member route, not the admin one", async () => {
        flags.connections_per_person = true;
        api.connectForMe.mockResolvedValue({ data: { connect_url: "https://c/mine" } });
        const onConnected = vi.fn();
        render(<ConnectorRow connector={gmail({ connected: true, connected_by: "workspace" })} onConnected={onConnected} />);
        fireEvent.click(screen.getByRole("button", { name: "Add Gmail for me" }));
        await waitFor(() => expect(onConnected).toHaveBeenCalled());
        expect(api.connectForMe).toHaveBeenCalledWith({ path: { slug: "gmail" } });
        expect(api.connect).not.toHaveBeenCalled();
        expect(window.open).toHaveBeenCalledWith("https://c/mine", "_blank", "noopener");
    });

    it("an unconnected app offers both, and says which is which", () => {
        flags.connections_per_person = true;
        render(<ConnectorRow connector={gmail()} onConnected={() => {}} />);
        expect(screen.getByRole("button", { name: "Add Gmail" }).textContent).toContain("Add for the workspace");
        expect(screen.getByRole("button", { name: "Add Gmail for me" })).toBeTruthy();
    });
});
