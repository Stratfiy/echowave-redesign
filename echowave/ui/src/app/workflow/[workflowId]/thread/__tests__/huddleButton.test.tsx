/**
 * The call button on an agent's thread (flag `huddle`): it opens the huddle
 * in place, beside the thread, for every kind of agent -- chat and
 * scheduled ones too. Off, the old "Call me to test" link stays.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
    useRouter: () => ({ push: vi.fn() }),
    usePathname: () => "/workflow/7/thread",
    useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false, getAccessToken: vi.fn() }) }));
const fetchWorkflow = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({ huddle: false }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => (name === "huddle" ? flags.huddle : false) }));
vi.mock("@/client/sdk.gen", () => ({
    getWorkflowApiV1WorkflowFetchWorkflowIdGet: fetchWorkflow,
}));
vi.mock("@/components/channel/ChannelStream", () => ({ ChannelStream: () => <div data-testid="stream" /> }));
vi.mock("@/components/channel/ChannelComposer", () => ({ ChannelComposer: () => <div data-testid="composer" /> }));
vi.mock("@/app/workflow/[workflowId]/components/AboutPanel", () => ({ AboutPanel: () => <div data-testid="about" /> }));
vi.mock("@/components/ui/dropdown-menu", () => ({
    DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div role="menu">{children}</div>,
    DropdownMenuItem: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
const huddleState = vi.hoisted(() => ({ phase: "idle" }));
vi.mock("@/components/huddle/useHuddle", () => ({
    useHuddle: () => ({ state: { phase: huddleState.phase, captions: [], muted: false }, end: vi.fn() }),
}));
vi.mock("@/components/huddle/HuddlePanel", () => ({
    HuddlePanel: ({ agentName, chatOnly }: { agentName: string; chatOnly: boolean }) => (
        <div data-testid="huddle-panel" data-chat-only={String(chatOnly)}>
            {agentName}
        </div>
    ),
    HuddleStrip: ({ onOpen }: { onOpen: () => void }) =>
        huddleState.phase === "listening" ? (
            <button type="button" data-testid="huddle-strip" onClick={onOpen}>
                Open
            </button>
        ) : null,
}));

import BotChatPage from "../page";

const params = Object.assign(Promise.resolve({ workflowId: "7" }), {
    status: "fulfilled",
    value: { workflowId: "7" },
}) as unknown as Promise<{ workflowId: string }>;

function renderPage() {
    return render(
        <React.Suspense fallback={null}>
            <BotChatPage params={params} />
        </React.Suspense>,
    );
}

beforeEach(() => {
    flags.huddle = false;
    huddleState.phase = "idle";
    fetchWorkflow.mockResolvedValue({ data: { name: "Front desk" } });
});

describe("the call button on an agent's thread", () => {
    it("off, keeps the old link and offers no huddle", async () => {
        renderPage();
        const bar = await screen.findByRole("banner");
        expect(within(bar).getByRole("link", { name: /Call me to test/ })).toBeTruthy();
        expect(within(bar).queryByTestId("huddle-button")).toBeNull();
    });

    it("on, replaces the link with a button that opens the huddle in place", async () => {
        flags.huddle = true;
        renderPage();
        const bar = await screen.findByRole("banner");
        await screen.findAllByText("Front desk");
        expect(within(bar).queryByRole("link", { name: /Call me to test/ })).toBeNull();
        const button = within(bar).getByRole("button", { name: "Huddle with Front desk" });
        expect(screen.queryByTestId("huddle-panel")).toBeNull();
        fireEvent.click(button);
        const panel = await screen.findByTestId("huddle-panel");
        expect(panel.textContent).toBe("Front desk");
        expect(screen.getByRole("complementary", { name: "Huddle with Front desk" })).toBeTruthy();
        // No navigation: the thread is still on the page.
        expect(screen.getByTestId("stream")).toBeTruthy();
        expect(button.getAttribute("aria-pressed")).toBe("true");
    });

    it("is offered for a chat agent too, whose customer test is a chat", async () => {
        flags.huddle = true;
        fetchWorkflow.mockResolvedValue({ data: { name: "Research agent", workflow_configurations: { channel: "chat" } } });
        renderPage();
        const bar = await screen.findByRole("banner");
        await screen.findAllByText("Research agent");
        fireEvent.click(within(bar).getByRole("button", { name: "Huddle with Research agent" }));
        expect((await screen.findByTestId("huddle-panel")).getAttribute("data-chat-only")).toBe("true");
    });

    it("opening About puts the huddle panel away without ending it", async () => {
        flags.huddle = true;
        huddleState.phase = "listening";
        renderPage();
        const bar = await screen.findByRole("banner");
        await screen.findAllByText("Front desk");
        fireEvent.click(within(bar).getByRole("button", { name: "Huddle with Front desk" }));
        await screen.findByTestId("huddle-panel");
        fireEvent.click(within(bar).getByRole("button", { name: /About/ }));
        expect(screen.queryByTestId("huddle-panel")).toBeNull();
        // Still live: the strip above the composer brings it back.
        fireEvent.click(screen.getByTestId("huddle-strip"));
        expect(await screen.findByTestId("huddle-panel")).toBeTruthy();
    });
});
