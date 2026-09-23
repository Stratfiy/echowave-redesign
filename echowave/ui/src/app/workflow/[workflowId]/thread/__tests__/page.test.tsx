/**
 * On a bot's chat the About, Test and Share buttons live in the header bar,
 * and the tab strip has a row of its own. Sharing one row clipped the last
 * tabs on any laptop-sized window.
 */

import { render, screen, within } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
    useRouter: () => ({ push: vi.fn() }),
    usePathname: () => "/workflow/7/thread",
    useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/client/sdk.gen", () => ({
    getWorkflowApiV1WorkflowFetchWorkflowIdGet: vi.fn().mockResolvedValue({ data: { name: "Front desk" } }),
}));
vi.mock("@/components/channel/ChannelStream", () => ({ ChannelStream: () => <div data-testid="stream" /> }));
vi.mock("@/components/channel/ChannelComposer", () => ({ ChannelComposer: () => <div data-testid="composer" /> }));
vi.mock("@/app/workflow/[workflowId]/components/AboutPanel", () => ({ AboutPanel: () => <div /> }));
vi.mock("@/app/workflow/[workflowId]/components/AgentTabs", () => ({
    AgentTabs: () => <nav aria-label="Agent tabs" />,
}));

import BotChatPage from "../page";

// React's `use` reads a settled thenable synchronously, so the page renders
// on the first pass instead of suspending the whole tree in a test.
const params = Object.assign(Promise.resolve({ workflowId: "7" }), {
    status: "fulfilled",
    value: { workflowId: "7" },
}) as unknown as Promise<{ workflowId: string }>;

describe("the agent's chat", () => {
    it("keeps About, Test and Share in the header, off the tab strip's row", async () => {
        render(
            <React.Suspense fallback={null}>
                <BotChatPage params={params} />
            </React.Suspense>,
        );
        const bar = await screen.findByRole("banner");
        expect(within(bar).getByRole("button", { name: /About/ })).toBeTruthy();
        expect(within(bar).getByRole("link", { name: /Test/ })).toBeTruthy();
        expect(within(bar).getByRole("link", { name: /Share/ })).toBeTruthy();

        const tabs = screen.getByRole("navigation", { name: "Agent tabs" });
        expect(bar.contains(tabs)).toBe(false);
        // The strip is a direct child of the page column, so it gets the whole row.
        expect(tabs.parentElement).toBe(bar.parentElement);
    });
});
