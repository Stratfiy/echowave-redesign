import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HomeAboveTheFold } from "../HomeAboveTheFold";

const api = vi.hoisted(() => ({ home: vi.fn() }));
const builder = vi.hoisted(() => ({ seen: [] as unknown[] }));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/client/sdk.gen", () => ({ teamHomeApiV1TeamHomeGet: api.home }));
vi.mock("@/components/agent-builder/AgentBuilderPanel", () => ({
    AgentBuilderPanel: (props: { prefill?: { text: string } }) => {
        builder.seen.push(props.prefill);
        return <div data-testid="composer">{props.prefill?.text ?? ""}</div>;
    },
}));
vi.mock("@/components/team/TeamPanel", () => ({
    TeamPanel: ({ members }: { members?: { name: string }[] }) => (
        <div>team:{members?.map((m) => m.name).join(",")}</div>
    ),
}));

function member(overrides: Record<string, unknown> = {}) {
    return {
        workflow_id: 1,
        workflow_uuid: "a",
        name: "Front Desk",
        is_live: true,
        status: "9 calls, 6 answered",
        tone: "working",
        at: null,
        calls: 9,
        answered: 6,
        outcomes: 4,
        failures: 0,
        last_action: null,
        ...overrides,
    };
}

beforeEach(() => {
    api.home.mockReset();
    builder.seen.length = 0;
});

describe("the home screen above the fold", () => {
    it("opens on what happened, in a sentence", async () => {
        api.home.mockResolvedValue({
            data: {
                hours: 24,
                headline: { agents: 2, live: 2, calls: 14, answered: 9, outcomes: 6, needs_attention: 1 },
                suggestions: [],
                members: [member()],
            },
        });
        render(<HomeAboveTheFold firstName="Nithish" />);
        expect(await screen.findByText(/14 calls today, 9 answered, 6 finished\. 1 agent needs you\./)).toBeTruthy();
        expect(screen.getByText(/Nithish/)).toBeTruthy();
    });

    it("says nothing has come in rather than leaving the line blank", async () => {
        // A blank line reads as a screen that failed to load, and an owner who
        // cannot tell "quiet morning" from "broken" stops reading the screen.
        api.home.mockResolvedValue({
            data: {
                hours: 24,
                headline: { agents: 1, live: 1, calls: 0, answered: 0, outcomes: 0, needs_attention: 0 },
                suggestions: [],
                members: [member()],
            },
        });
        render(<HomeAboveTheFold />);
        expect(await screen.findByText("Nothing has come in yet today.")).toBeTruthy();
    });

    it("fills the composer from a chip and never sends it", async () => {
        api.home.mockResolvedValue({
            data: {
                hours: 24,
                headline: { agents: 1, live: 1, calls: 0, answered: 0, outcomes: 0, needs_attention: 0 },
                suggestions: [
                    { kind: "hire", text: "What else could an agent take off my hands?", action: "prompt", prompt: "What else could an agent take off my hands?", href: null },
                ],
                members: [member()],
            },
        });
        render(<HomeAboveTheFold />);
        fireEvent.click(await screen.findByRole("button", { name: /what else could an agent/i }));
        expect(screen.getByTestId("composer").textContent).toBe("What else could an agent take off my hands?");
    });

    it("renders an urgent chip as a link to the screen that fixes it", async () => {
        api.home.mockResolvedValue({
            data: {
                hours: 24,
                headline: { agents: 1, live: 1, calls: 9, answered: 9, outcomes: 0, needs_attention: 1 },
                suggestions: [
                    { kind: "connector_failing", text: "Googlecalendar failed 6 times this week — reconnect it", action: "link", prompt: null, href: "/integrations/apps" },
                ],
                members: [member()],
            },
        });
        render(<HomeAboveTheFold />);
        const link = await screen.findByRole("link", { name: /googlecalendar failed 6 times/i });
        expect(link.getAttribute("href")).toBe("/integrations/apps");
    });

    it("still shows the composer when the greeting fails to load", async () => {
        api.home.mockResolvedValue({ error: { detail: "boom" } });
        render(<HomeAboveTheFold />);
        expect(screen.getByTestId("composer")).toBeTruthy();
    });
});
