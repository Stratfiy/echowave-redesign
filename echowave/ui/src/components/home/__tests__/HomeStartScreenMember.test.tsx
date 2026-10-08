/**
 * Phase 3, shell: Chat's start screen for a plain member with private threads
 * on. The original conversation is not theirs, so the screen must not read
 * it (that drew "Could not load this conversation") and the first message
 * must start a new conversation of their own (it failed with "Thread not
 * found"). A conversation Talk starts from here is followed. With the flag
 * off, or for the original's owner, nothing changes.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { announceThreadStarted, resetEntryPoints } from "@/lib/shell/chatEntryPoints";

import { HomeAboveTheFold } from "../HomeAboveTheFold";

const api = vi.hoisted(() => ({ post: vi.fn(), threads: vi.fn() }));
const seen = vi.hoisted(() => ({
    stream: [] as Record<string, unknown>[],
    composer: [] as Record<string, unknown>[],
}));
const flags = vi.hoisted(() => ({ chat_shell: true, decibyl_private_threads: true, settled: true }));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 2 }, loading: false }) }));
vi.mock("@/lib/features", () => ({
    useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]),
    useFeaturesSettled: () => flags.settled,
}));
vi.mock("@/client/sdk.gen", () => ({
    teamHomeApiV1TeamHomeGet: async () => ({ data: { headline: null, suggestions: [], openers: [] } }),
    postMessageApiV1TimelineMessagePost: api.post,
    stopReplyApiV1ShellChatStopPost: async () => ({ data: { requested: true } }),
    getWorkflowsApiV1WorkflowFetchGet: async () => ({ data: [] }),
    threadsApiV1TimelineThreadsGet: api.threads,
}));
vi.mock("@/components/channel/ChannelStream", () => ({
    ChannelStream: (props: Record<string, unknown>) => {
        seen.stream.push(props);
        React.useEffect(() => {
            (props.onCountChange as (n: number) => void)?.(props.threadId ? 1 : 0);
            (props.onLoadState as (s: string) => void)?.("ready");
        }, [props.onCountChange, props.onLoadState, props.threadId]);
        return <div data-testid="stream" data-thread={String(props.threadId ?? "original")} />;
    },
}));
vi.mock("@/components/home/ThreadList", () => ({ ThreadList: () => <div /> }));
vi.mock("@/components/learning/LearningResume", () => ({ LearningResume: () => null }));
vi.mock("@/components/learning/LearningSession", () => ({ LearningSession: () => null }));
vi.mock("@/components/channel/ChannelComposer", () => ({
    ChannelComposer: (props: Record<string, unknown>) => {
        seen.composer.push(props);
        return (
            <div data-testid="composer" data-starts-new={String(Boolean(props.startsNewThread))}>
                <button
                    type="button"
                    onClick={() => (props.onSent as (a: number[], t?: string) => void)?.([], props.startsNewThread ? "t-minted" : undefined)}
                >
                    send
                </button>
            </div>
        );
    },
}));

const lastStream = () => seen.stream[seen.stream.length - 1];

beforeEach(() => {
    api.post.mockReset();
    api.threads.mockReset();
    api.post.mockResolvedValue({ data: { asked: [] } });
    seen.stream.length = 0;
    seen.composer.length = 0;
    flags.chat_shell = true;
    flags.decibyl_private_threads = true;
    flags.settled = true;
    resetEntryPoints();
    window.history.replaceState(null, "", "/overview");
});

describe("Chat start for a member whose original conversation is not theirs", () => {
    beforeEach(() => api.threads.mockResolvedValue({ data: { threads: [], original_is_yours: false } }));

    it("opens on the greeting, never reading the original conversation", async () => {
        render(<HomeAboveTheFold firstName="Bala" />);
        expect(await screen.findByText(/What can I do for you/)).toBeTruthy();
        expect(seen.stream).toHaveLength(0);
        await waitFor(() => expect(screen.getByTestId("composer").getAttribute("data-starts-new")).toBe("true"));
    });

    it("follows the conversation the first message started", async () => {
        render(<HomeAboveTheFold />);
        await waitFor(() => expect(screen.getByTestId("composer").getAttribute("data-starts-new")).toBe("true"));
        fireEvent.click(screen.getByText("send"));
        await waitFor(() => expect(lastStream()?.threadId).toBe("t-minted"));
        expect(new URLSearchParams(window.location.search).get("thread")).toBe("t-minted");
        // Once in a conversation of their own, sends go there.
        expect(screen.getByTestId("composer").getAttribute("data-starts-new")).toBe("false");
    });

    it("follows a conversation Talk started from here", async () => {
        render(<HomeAboveTheFold />);
        await screen.findByText(/What can I do for you/);
        act(() => announceThreadStarted("t-voice"));
        await waitFor(() => expect(lastStream()?.threadId).toBe("t-voice"));
    });

    it("the first task from onboarding starts a new conversation, asked once", async () => {
        // An invited member's first task (screen 02) arrives as "?ask=" and
        // was sent before the screen knew the original was not theirs: a
        // 404, swallowed, and the task never asked.
        let answer: (value: unknown) => void = () => {};
        api.threads.mockReturnValue(new Promise((resolve) => (answer = resolve)));
        window.history.replaceState(null, "", "/overview?ask=Plan%20my%20week");
        render(<HomeAboveTheFold />);
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(api.post).not.toHaveBeenCalled();
        answer({ data: { threads: [], original_is_yours: false } });
        await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
        const body = api.post.mock.calls[0][0].body;
        expect(body.text).toBe("Plan my week");
        expect(body.thread_id).toMatch(/^[0-9a-f-]{36}$/);
        await waitFor(() => expect(lastStream()?.threadId).toBe(body.thread_id));
        expect(api.post).toHaveBeenCalledTimes(1);
    });

    it("a starter sent from the start screen starts a new conversation too", async () => {
        flags.chat_shell = false;
        render(<HomeAboveTheFold />);
        fireEvent.click(await screen.findByText("What happened this week?"));
        await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
        const sentTo = api.post.mock.calls[0][0].body.thread_id;
        expect(sentTo).toMatch(/^[0-9a-f-]{36}$/);
        await waitFor(() => expect(lastStream()?.threadId).toBe(sentTo));
    });
});

describe("Before the flags have answered", () => {
    // Found in the browser: the flag map arrives after the screen mounts, so
    // for a moment private threads read as off and the member's start screen
    // asked for the original conversation -- a 404 before the greeting.
    it("the original conversation is not read", async () => {
        flags.settled = false;
        flags.decibyl_private_threads = false;
        const view = render(<HomeAboveTheFold />);
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(seen.stream).toHaveLength(0);
        flags.settled = true;
        flags.decibyl_private_threads = true;
        api.threads.mockResolvedValue({ data: { threads: [], original_is_yours: false } });
        view.rerender(<HomeAboveTheFold />);
        expect(await screen.findByText(/What can I do for you/)).toBeTruthy();
        expect(seen.stream).toHaveLength(0);
    });
});

describe("Nothing changes", () => {
    it("for the original's owner", async () => {
        api.threads.mockResolvedValue({ data: { threads: [], original_is_yours: true } });
        render(<HomeAboveTheFold />);
        await waitFor(() => expect(seen.stream.length).toBeGreaterThan(0));
        expect(lastStream()?.threadId).toBeNull();
        expect(screen.getByTestId("composer").getAttribute("data-starts-new")).toBe("false");
    });

    it("with private threads off: the original is read and written, no extra call", async () => {
        flags.decibyl_private_threads = false;
        render(<HomeAboveTheFold />);
        await waitFor(() => expect(seen.stream.length).toBeGreaterThan(0));
        expect(lastStream()?.threadId).toBeNull();
        expect(api.threads).not.toHaveBeenCalled();
        expect(screen.getByTestId("composer").getAttribute("data-starts-new")).toBe("false");
    });

    it("inside a named conversation, Talk does not move the screen", async () => {
        api.threads.mockResolvedValue({ data: { threads: [], original_is_yours: false } });
        window.history.replaceState(null, "", "/overview?thread=t-mine");
        render(<HomeAboveTheFold />);
        await waitFor(() => expect(lastStream()?.threadId).toBe("t-mine"));
        act(() => announceThreadStarted("t-other"));
        expect(lastStream()?.threadId).toBe("t-mine");
    });
});
