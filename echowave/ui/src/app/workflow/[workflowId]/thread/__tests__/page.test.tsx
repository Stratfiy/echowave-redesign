/**
 * The agent's page (October 2026 design): its chat, About (voice, skills,
 * memory) beside it, Test in the header, and everything else -- sharing,
 * activity, the advanced setup -- in one menu. No tab strip.
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
// The menu's items render inline, so the test can see where they go.
vi.mock("@/components/ui/dropdown-menu", () => ({
    DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div role="menu">{children}</div>,
    DropdownMenuItem: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import BotChatPage from "../page";

// React's `use` reads a settled thenable synchronously, so the page renders
// on the first pass instead of suspending the whole tree in a test.
const params = Object.assign(Promise.resolve({ workflowId: "7" }), {
    status: "fulfilled",
    value: { workflowId: "7" },
}) as unknown as Promise<{ workflowId: string }>;

describe("the agent's page", () => {
    it("keeps About and Test in the header and the rest in one menu", async () => {
        render(
            <React.Suspense fallback={null}>
                <BotChatPage params={params} />
            </React.Suspense>,
        );
        const bar = await screen.findByRole("banner");
        expect(within(bar).getByRole("button", { name: /About/ })).toBeTruthy();
        expect(within(bar).getByRole("link", { name: /Call me to test/ }).getAttribute("href")).toBe("/workflow/7?onboarding=web_call");
        const menu = within(bar).getByRole("menu");
        expect(within(menu).getByRole("link", { name: "Share" }).getAttribute("href")).toBe("/workflow/7/settings?tab=share");
        expect(within(menu).getByRole("link", { name: "Activity" }).getAttribute("href")).toBe("/workflow/7/runs");
        expect(within(menu).getByRole("link", { name: "Advanced setup" }).getAttribute("href")).toBe("/workflow/7");
    });

    it("has no tab strip", async () => {
        render(
            <React.Suspense fallback={null}>
                <BotChatPage params={params} />
            </React.Suspense>,
        );
        await screen.findByRole("banner");
        expect(screen.queryByRole("navigation", { name: "Agent" })).toBeNull();
    });
});
