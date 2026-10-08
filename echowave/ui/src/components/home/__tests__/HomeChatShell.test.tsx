/**
 * Chat start under `chat_shell` (screen 03): no more than three starters,
 * each filling the box rather than sending; the first task from onboarding
 * asked once; Stop sent to the server; and a history that failed is never
 * drawn as the empty greeting.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HomeAboveTheFold, MAX_STARTERS } from "../HomeAboveTheFold";

const api = vi.hoisted(() => ({ home: vi.fn(), post: vi.fn(), stop: vi.fn() }));
const seen = vi.hoisted(() => ({
    stream: [] as Record<string, unknown>[],
    composer: [] as Record<string, unknown>[],
    rows: 0,
    load: "ready" as "ready" | "error",
}));
const flags = vi.hoisted(() => ({ chat_shell: true, learning: false }));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({
    useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]),
    useFeaturesSettled: () => true,
}));
vi.mock("@/client/sdk.gen", () => ({
    teamHomeApiV1TeamHomeGet: api.home,
    postMessageApiV1TimelineMessagePost: api.post,
    stopReplyApiV1ShellChatStopPost: api.stop,
    getWorkflowsApiV1WorkflowFetchGet: async () => ({ data: [] }),
}));
vi.mock("@/components/channel/ChannelStream", () => ({
    ChannelStream: (props: Record<string, unknown>) => {
        seen.stream.push(props);
        React.useEffect(() => {
            (props.onCountChange as (n: number) => void)?.(seen.rows);
            (props.onLoadState as (s: string) => void)?.(seen.load);
        }, [props.onCountChange, props.onLoadState]);
        return (
            <div data-testid="stream">
                <button
                    type="button"
                    onClick={() =>
                        (props.onOpenSources as (s: unknown[], id: number) => void)?.(
                            [{ kind: "team", label: "Your team", status: "read" }],
                            9,
                        )
                    }
                >
                    open sources
                </button>
            </div>
        );
    },
}));
vi.mock("@/components/home/ThreadList", () => ({ ThreadList: () => <div /> }));
vi.mock("@/components/learning/LearningResume", () => ({
    LearningResume: (props: { onOpen: (id: string) => void }) => (
        <button type="button" onClick={() => props.onOpen("g-7")}>
            Continue learning: Fractions
        </button>
    ),
}));
vi.mock("@/components/learning/LearningSession", () => ({
    LearningSession: (props: { goalId: string | null; reviewSkillId?: number | null; onClose: () => void }) => (
        <div data-testid="lesson" data-goal={props.goalId ?? "new"} data-review={props.reviewSkillId ?? ""}>
            <button type="button" onClick={props.onClose}>
                back
            </button>
        </div>
    ),
}));
vi.mock("@/components/channel/ChannelComposer", () => ({
    ChannelComposer: (props: Record<string, unknown>) => {
        seen.composer.push(props);
        return (
            <div data-testid="composer" data-draft={(props.draftRequest as { text?: string } | null)?.text ?? ""}>
                <button type="button" onClick={() => (props.onStop as () => void)?.()}>
                    stop
                </button>
            </div>
        );
    },
}));

const headline = { agents: 1, live: 1, calls: 0, answered: 0, outcomes: 0, needs_attention: 0 };

beforeEach(() => {
    api.home.mockReset();
    api.post.mockReset();
    api.stop.mockReset();
    api.post.mockResolvedValue({ data: { asked: [] } });
    api.stop.mockResolvedValue({ data: { requested: true } });
    seen.stream.length = 0;
    seen.composer.length = 0;
    seen.rows = 0;
    seen.load = "ready";
    flags.chat_shell = true;
    flags.learning = false;
    window.history.replaceState(null, "", "/overview");
});

const openers = ["What needs my attention today?", "Help me plan today", "Teach me something", "Help with a reply", "What happened this week?"].map(
    (text) => ({ kind: "time", text }),
);

describe("Chat start", () => {
    it("shows at most three starters, and one fills the box without sending", async () => {
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers } });
        render(<HomeAboveTheFold firstName="Asha" />);
        await screen.findByText("Help me plan today");
        expect(screen.queryByText("Help with a reply")).toBeNull();
        expect(MAX_STARTERS).toBe(3);
        fireEvent.click(screen.getByText("Help me plan today"));
        await waitFor(() => expect(screen.getByTestId("composer").getAttribute("data-draft")).toBe("Help me plan today"));
        expect(api.post).not.toHaveBeenCalled();
    });

    it("offers everyday starters, not agent-building jobs, when the server has none", async () => {
        api.home.mockResolvedValue({ data: { headline: { ...headline, agents: 0 }, suggestions: [], openers: [] } });
        render(<HomeAboveTheFold />);
        expect(await screen.findByText("Help me plan today")).toBeTruthy();
        expect(screen.getByText("Teach me something")).toBeTruthy();
        expect(screen.getByText("Help with a reply")).toBeTruthy();
        expect(screen.queryByText("Answer my phone and book appointments")).toBeNull();
    });

    it("asks the first task from onboarding exactly once, then takes it off the address", async () => {
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers: [] } });
        window.history.replaceState(null, "", "/overview?ask=Plan%20my%20week");
        const { rerender } = render(<HomeAboveTheFold />);
        await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
        expect(api.post.mock.calls[0][0].body).toMatchObject({ assistant: true, text: "Plan my week" });
        expect(window.location.search).toBe("");
        rerender(<HomeAboveTheFold />);
        expect(api.post).toHaveBeenCalledTimes(1);
    });

    it("a first task that could not be sent is kept in the box, not lost", async () => {
        // Phase 3: a failed send of the first task (or of a starter with the
        // flag off) was dropped without a word.
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers: [] } });
        api.post.mockResolvedValue({ error: { detail: "Thread not found" } });
        window.history.replaceState(null, "", "/overview?ask=Plan%20my%20week");
        render(<HomeAboveTheFold />);
        await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
        await waitFor(() => expect(screen.getByTestId("composer").getAttribute("data-draft")).toBe("Plan my week"));
        expect(screen.getByRole("alert").textContent).toContain("Thread not found");
    });

    it("sends Stop to the server for this thread", async () => {
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers: [] } });
        window.history.replaceState(null, "", "/overview?thread=t-9");
        render(<HomeAboveTheFold />);
        fireEvent.click(await screen.findByText("stop"));
        await waitFor(() => expect(api.stop).toHaveBeenCalledWith({ body: { thread_id: "t-9" } }));
    });

    it("says when Stop did not land", async () => {
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers: [] } });
        api.stop.mockResolvedValue({ data: { requested: false } });
        render(<HomeAboveTheFold />);
        fireEvent.click(await screen.findByText("stop"));
        expect(await screen.findByText(/Stop did not reach Decibyl/)).toBeTruthy();
    });

    it("never draws the empty greeting over a history that failed to load", async () => {
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers: [] } });
        seen.load = "error";
        render(<HomeAboveTheFold />);
        await waitFor(() => expect(seen.stream.length).toBeGreaterThan(0));
        await waitFor(() => expect(screen.queryByText("Hi, I'm Decibyl!")).toBeNull());
    });

    it("passes the Chat states down to the stream and the composer", async () => {
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers: [] } });
        render(<HomeAboveTheFold />);
        await waitFor(() => expect(seen.stream.length).toBeGreaterThan(0));
        const stream = seen.stream[seen.stream.length - 1];
        expect(stream.chatShell).toBe(true);
        expect(typeof stream.onOpenSources).toBe("function");
        expect(seen.composer[seen.composer.length - 1].chatShell).toBe(true);
    });

    it("opens sources beside the thread; Escape closes them and focus goes back", async () => {
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers: [] } });
        seen.rows = 2;
        render(<HomeAboveTheFold />);
        const trigger = await screen.findByText("open sources");
        trigger.focus();
        fireEvent.click(trigger);
        expect(await screen.findByRole("complementary", { name: "Sources" })).toBeTruthy();
        expect(screen.getByText("Your team")).toBeTruthy();
        fireEvent.keyDown(document, { key: "Escape" });
        await waitFor(() => expect(screen.queryByRole("complementary", { name: "Sources" })).toBeNull());
        await waitFor(() => expect(document.activeElement).toBe(trigger));
    });

    it("keeps the old behaviour with the flag off: every opener sends", async () => {
        flags.chat_shell = false;
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers } });
        render(<HomeAboveTheFold />);
        expect(await screen.findByText("Help with a reply")).toBeTruthy();
        fireEvent.click(screen.getByText("Help me plan today"));
        await waitFor(() => expect(api.post).toHaveBeenCalled());
    });
});

describe("Learning inside Chat (screen 13)", () => {
    it("off, Teach me something fills the box as before", async () => {
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers: [] } });
        render(<HomeAboveTheFold />);
        fireEvent.click(await screen.findByText("Teach me something"));
        await waitFor(() => expect(screen.getByTestId("composer").getAttribute("data-draft")).toBe("Teach me something"));
        expect(screen.queryByTestId("lesson")).toBeNull();
    });

    it("on, Teach me something opens a lesson in Chat and the composer steps aside", async () => {
        flags.learning = true;
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers: [] } });
        render(<HomeAboveTheFold />);
        fireEvent.click(await screen.findByText("Teach me something"));
        const lesson = await screen.findByTestId("lesson");
        expect(lesson.getAttribute("data-goal")).toBe("new");
        expect(screen.getByTestId("composer").parentElement?.className).toMatch(/hidden/);
        expect(window.location.search).toBe("?learn=new");
        expect(api.post).not.toHaveBeenCalled();
        fireEvent.click(screen.getByText("back"));
        await waitFor(() => expect(screen.queryByTestId("lesson")).toBeNull());
        expect(window.location.search).toBe("");
    });

    it("keeps the teach starter among three when the server's cards lack it", async () => {
        flags.learning = true;
        const server = ["What needs my attention today?", "Help me plan today", "Help with a reply"].map((text) => ({ kind: "time", text }));
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers: server } });
        render(<HomeAboveTheFold />);
        expect(await screen.findByText("Teach me something")).toBeTruthy();
        expect(screen.getByText("What needs my attention today?")).toBeTruthy();
        expect(screen.getByText("Help me plan today")).toBeTruthy();
        expect(screen.queryByText("Help with a reply")).toBeNull();
    });

    it("offers a resume link that opens the lesson on that goal", async () => {
        flags.learning = true;
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers: [] } });
        render(<HomeAboveTheFold />);
        fireEvent.click(await screen.findByText("Continue learning: Fractions"));
        expect((await screen.findByTestId("lesson")).getAttribute("data-goal")).toBe("g-7");
    });

    it("resumes a goal, on a review, from the address", async () => {
        flags.learning = true;
        window.history.replaceState(null, "", "/overview?learn=g-9&review=4");
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers: [] } });
        render(<HomeAboveTheFold />);
        const lesson = await screen.findByTestId("lesson");
        expect(lesson.getAttribute("data-goal")).toBe("g-9");
        expect(lesson.getAttribute("data-review")).toBe("4");
    });

    it("off, a learn address opens nothing", async () => {
        window.history.replaceState(null, "", "/overview?learn=g-9");
        api.home.mockResolvedValue({ data: { headline, suggestions: [], openers: [] } });
        render(<HomeAboveTheFold />);
        await screen.findByTestId("composer");
        expect(screen.queryByTestId("lesson")).toBeNull();
    });
});
