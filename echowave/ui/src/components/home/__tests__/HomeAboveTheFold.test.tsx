import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HomeAboveTheFold } from "../HomeAboveTheFold";

const api = vi.hoisted(() => ({ home: vi.fn(), post: vi.fn() }));
const seen = vi.hoisted(() => ({ stream: [] as unknown[], composer: [] as unknown[], rows: 0 }));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/client/sdk.gen", () => ({
    teamHomeApiV1TeamHomeGet: api.home,
    postMessageApiV1TimelineMessagePost: api.post,
    // The roster for @ on Decibyl's thread: no bots in these tests.
    getWorkflowsApiV1WorkflowFetchGet: async () => ({ data: [] }),
}));
vi.mock("@/components/channel/ChannelStream", () => ({
    ChannelStream: (props: Record<string, unknown>) => {
        seen.stream.push(props);
        // The real stream reports its row count once loaded; the greeting
        // waits on that.
        React.useEffect(() => {
            (props.onCountChange as (n: number) => void)?.(seen.rows);
        }, [props.onCountChange]);
        return <div data-testid="stream" />;
    },
}));
vi.mock("@/components/channel/ChannelComposer", () => ({
    ChannelComposer: (props: Record<string, unknown>) => {
        seen.composer.push(props);
        return <div data-testid="composer" />;
    },
}));

const headline = { agents: 1, live: 1, calls: 9, answered: 6, outcomes: 2, needs_attention: 0 };

beforeEach(() => {
    api.home.mockReset();
    api.post.mockReset();
    seen.stream.length = 0;
    seen.composer.length = 0;
    seen.rows = 0;
});

describe("home is Decibyl's thread", () => {
    it("steps the greeting aside once there is a conversation", async () => {
        // The tiles were staying on screen above the thread after the first
        // reply, so the chat started a screen and a half down. Once rows
        // exist, one line names Decibyl and the thread has the screen.
        seen.rows = 4;
        api.home.mockResolvedValue({ data: { hours: 24, headline, suggestions: [], members: [] } });
        render(<HomeAboveTheFold firstName="Nithish" />);
        await waitFor(() => expect(screen.queryByText(/Hi, I'm Decibyl/)).toBeNull());
        expect(screen.queryByRole("button", { name: "What happened this week?" })).toBeNull();
        expect(await screen.findByText(/9 calls today/)).toBeTruthy();
        expect(screen.getByTestId("composer")).toBeTruthy();
    });

    it("says hello with the numbers, and mounts the thread and composer in assistant mode", async () => {
        api.home.mockResolvedValue({ data: { hours: 24, headline, suggestions: [], members: [] } });
        render(<HomeAboveTheFold firstName="Nithish" />);
        expect(await screen.findByText(/Hi, I'm Decibyl/)).toBeTruthy();
        expect(await screen.findByText(/9 calls today, 6 answered, 2 finished/)).toBeTruthy();
        expect(screen.getByTestId("stream")).toBeTruthy();
        expect(screen.getByTestId("composer")).toBeTruthy();
        expect((seen.stream[0] as { assistant: boolean }).assistant).toBe(true);
        expect((seen.composer[0] as { assistant: boolean; channelName: string }).channelName).toBe("Decibyl");
    });

    it("an opener is sent to Decibyl as a message, then the thread waits for the reply", async () => {
        api.home.mockResolvedValue({ data: { hours: 24, headline, suggestions: [], members: [] } });
        api.post.mockResolvedValue({ data: { asked: [], unknown: [], ambiguous: [] } });
        render(<HomeAboveTheFold />);
        fireEvent.click(screen.getByRole("button", { name: "What happened this week?" }));
        await waitFor(() => expect(api.post).toHaveBeenCalled());
        expect(api.post.mock.calls[0][0].body).toEqual({ assistant: true, text: "What happened this week?" });
        await waitFor(() => {
            const last = seen.stream[seen.stream.length - 1] as { waitingFor: { bots: number[] } | null };
            expect(last.waitingFor?.bots).toEqual([0]);
        });
    });

    it("shows the server's own cards when it sends them, and pressing one sends that line", async () => {
        // ChatGPT's opening suggestions come from what you did before; so do
        // ours. When the server has cards, the two fixed questions step aside.
        api.home.mockResolvedValue({
            data: {
                hours: 24,
                headline,
                suggestions: [],
                openers: [
                    { kind: "asked_before", text: "Which bots took calls today?" },
                    { kind: "missed_calls", text: "Call back the 3 people who rang and got nobody" },
                    { kind: "time", text: "What happened since yesterday?" },
                ],
                members: [],
            },
        });
        api.post.mockResolvedValue({ data: { asked: [], unknown: [], ambiguous: [] } });
        render(<HomeAboveTheFold />);
        expect(await screen.findByRole("button", { name: "Which bots took calls today?" })).toBeTruthy();
        expect(screen.getByRole("button", { name: /Call back the 3 people/ })).toBeTruthy();
        expect(screen.queryByRole("button", { name: "What happened this week?" })).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Which bots took calls today?" }));
        await waitFor(() => expect(api.post).toHaveBeenCalled());
        expect(api.post.mock.calls[0][0].body).toEqual({ assistant: true, text: "Which bots took calls today?" });
    });

    it("a brand-new account gets the first jobs for its business from the server", async () => {
        api.home.mockResolvedValue({
            data: {
                hours: 24,
                headline: { ...headline, agents: 0 },
                suggestions: [],
                openers: [{ kind: "first_job", text: "Quote shipments from our rate card" }],
                members: [],
            },
        });
        render(<HomeAboveTheFold />);
        expect(await screen.findByRole("button", { name: "Quote shipments from our rate card" })).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Answer my phone and book appointments" })).toBeNull();
    });

    it("a link chip opens the screen that fixes it; a prompt chip opens the shelf", async () => {
        api.home.mockResolvedValue({
            data: {
                hours: 24,
                headline,
                suggestions: [
                    { kind: "connector_failing", text: "Googlecalendar failed 6 times this week — reconnect it", action: "link", prompt: null, href: "/integrations/apps" },
                    { kind: "hire", text: "What else could an agent take off my hands?", action: "prompt", prompt: "x", href: null },
                ],
                members: [],
            },
        });
        render(<HomeAboveTheFold />);
        expect((await screen.findByRole("link", { name: /googlecalendar failed/i })).getAttribute("href")).toBe("/integrations/apps");
        expect(screen.getByRole("link", { name: /what else could an agent/i }).getAttribute("href")).toBe("/marketplace");
    });

    it("still says hello when the numbers fail to load", () => {
        api.home.mockResolvedValue({ error: { detail: "boom" } });
        render(<HomeAboveTheFold />);
        expect(screen.getByText(/Hi, I'm Decibyl/)).toBeTruthy();
        expect(screen.getByTestId("composer")).toBeTruthy();
    });
});
