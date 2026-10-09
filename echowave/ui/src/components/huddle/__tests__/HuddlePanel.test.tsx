/**
 * The huddle panel: it starts the conversation when it opens, shows the
 * live transcript with the agent's name, keeps Mute and End in reach, says
 * a proposed change waits on a card, swaps to the agent's real customer
 * test in place (ending the huddle first), and lists -- and forgets -- the
 * notes the agent keeps, saying so when they cannot be read.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
    notes: vi.fn(),
    forget: vi.fn(),
    start: vi.fn(),
    end: vi.fn(),
    get: vi.fn(),
    move: vi.fn(),
    heartbeat: vi.fn(),
}));
vi.mock("@/client/sdk.gen", () => ({
    huddleNotesApiV1HuddleWorkflowIdNotesGet: api.notes,
    forgetHuddleNotesApiV1HuddleWorkflowIdNotesDelete: api.forget,
    startHuddleApiV1HuddleWorkflowIdSessionsPost: api.start,
    endHuddleApiV1HuddleSessionsSessionIdEndPost: api.end,
    getHuddleApiV1HuddleSessionsSessionIdGet: api.get,
    moveHuddleApiV1HuddleSessionsSessionIdMovePost: api.move,
    heartbeatHuddleApiV1HuddleSessionsSessionIdHeartbeatPost: api.heartbeat,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false, getAccessToken: vi.fn() }) }));
vi.mock("@/app/workflow/[workflowId]/components/WorkflowTesterPanel", () => ({
    WorkflowTesterPanel: ({ workflowId, channel }: { workflowId: number; channel: string }) => (
        <div data-testid="tester" data-workflow={workflowId} data-channel={channel} />
    ),
}));

import { INITIAL, type VoiceState } from "@/lib/voice/sessionState";

import { huddleLabel, HuddlePanel, HuddleStrip } from "../HuddlePanel";
import type { Huddle } from "../useHuddle";
import { huddleApi } from "../useHuddle";

function fakeHuddle(state: Partial<VoiceState> = {}): Huddle {
    return {
        state: { ...INITIAL, ...state },
        inputLevel: 0.1,
        start: vi.fn(async () => undefined),
        end: vi.fn(async () => undefined),
        toggleMute: vi.fn(async () => undefined),
        minimize: vi.fn(),
        close: vi.fn(),
    } as unknown as Huddle;
}

beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    api.notes.mockResolvedValue({ data: { notes: [] } });
    api.forget.mockResolvedValue({ data: { notes: [] } });
});

describe("HuddlePanel", () => {
    it("starts the huddle once when it opens", async () => {
        const huddle = fakeHuddle();
        render(<HuddlePanel workflowId={7} agentName="Front desk" chatOnly={false} huddle={huddle} />);
        await waitFor(() => expect(huddle.start).toHaveBeenCalledTimes(1));
        expect(huddle.start).toHaveBeenCalledWith({ threadId: null, draft: "" });
    });

    it("does not start a second one over a live huddle", async () => {
        const huddle = fakeHuddle({ phase: "listening", sessionId: 3 });
        render(<HuddlePanel workflowId={7} agentName="Front desk" chatOnly={false} huddle={huddle} />);
        await waitFor(() => expect(api.notes).toHaveBeenCalled());
        expect(huddle.start).not.toHaveBeenCalled();
    });

    it("shows the live transcript by name, and Mute and End work", async () => {
        const huddle = fakeHuddle({
            phase: "speaking",
            sessionId: 3,
            captions: [
                { id: "a", who: "you", text: "How many calls today?", final: true },
                { id: "b", who: "decibyl", text: "Four, one escalated.", final: true },
            ],
        });
        render(<HuddlePanel workflowId={7} agentName="Front desk" chatOnly={false} huddle={huddle} />);
        expect(screen.getByTestId("huddle-state").textContent).toBe("Front desk is speaking");
        const log = screen.getByRole("log", { name: "Huddle transcript" });
        expect(log.textContent).toContain("You: How many calls today?");
        expect(log.textContent).toContain("Front desk: Four, one escalated.");
        fireEvent.click(screen.getByRole("button", { name: /Mute/ }));
        expect(huddle.toggleMute).toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: /End huddle/ }));
        expect(huddle.end).toHaveBeenCalled();
    });

    it("says a proposed change waits on a card and is not made by voice", () => {
        const huddle = fakeHuddle({ phase: "listening", sessionId: 3, approvalWaiting: true });
        render(<HuddlePanel workflowId={7} agentName="Front desk" chatOnly={false} huddle={huddle} />);
        expect(screen.getByTestId("huddle-approval").textContent).toMatch(/Nothing changes until you publish it there/);
    });

    it("Try it as a customer ends the huddle and opens the real test in place", async () => {
        const huddle = fakeHuddle({ phase: "listening", sessionId: 3 });
        render(<HuddlePanel workflowId={7} agentName="Front desk" chatOnly={false} huddle={huddle} />);
        fireEvent.click(screen.getByRole("switch", { name: "Try it as a customer" }));
        const tester = await screen.findByTestId("tester");
        expect(huddle.end).toHaveBeenCalled();
        expect(tester.getAttribute("data-workflow")).toBe("7");
        expect(tester.getAttribute("data-channel")).toBe("voice");
        expect(screen.queryByRole("log", { name: "Huddle transcript" })).toBeNull();
    });

    it("a chat agent's customer test is a chat", async () => {
        const huddle = fakeHuddle({ phase: "ended" });
        render(<HuddlePanel workflowId={7} agentName="Research" chatOnly huddle={huddle} />);
        fireEvent.click(screen.getByRole("switch", { name: "Try it as a customer" }));
        expect((await screen.findByTestId("tester")).getAttribute("data-channel")).toBe("chat");
    });

    it("lists the agent's notes and forgets them", async () => {
        api.notes.mockResolvedValue({ data: { notes: ["Weekly numbers, not daily"] } });
        const huddle = fakeHuddle({ phase: "ended" });
        render(<HuddlePanel workflowId={7} agentName="Front desk" chatOnly={false} huddle={huddle} />);
        expect(await screen.findByText("Weekly numbers, not daily")).toBeTruthy();
        expect(api.notes).toHaveBeenCalledWith({ path: { workflow_id: 7 } });
        fireEvent.click(screen.getByRole("button", { name: "Forget" }));
        await waitFor(() => expect(screen.queryByText("Weekly numbers, not daily")).toBeNull());
        expect(api.forget).toHaveBeenCalledWith({ path: { workflow_id: 7 } });
    });

    it("says when the notes cannot be read, as text", async () => {
        api.notes.mockResolvedValue({ error: { detail: [{ msg: "bad", loc: ["path", "workflow_id"] }] } });
        render(<HuddlePanel workflowId={7} agentName="Front desk" chatOnly={false} huddle={fakeHuddle({ phase: "ended" })} />);
        const notes = await screen.findByTestId("huddle-notes");
        expect(notes.textContent).toContain("bad");
        expect(notes.textContent).not.toContain("[object Object]");
    });

    it("an ended huddle offers to start again", () => {
        const huddle = fakeHuddle({ phase: "ended", notice: "Ended." });
        render(<HuddlePanel workflowId={7} agentName="Front desk" chatOnly={false} huddle={huddle} />);
        fireEvent.click(screen.getByRole("button", { name: /Start again/ }));
        expect(huddle.start).toHaveBeenCalled();
    });
});

describe("HuddleStrip", () => {
    it("shows only while live, with Open and End", () => {
        const onOpen = vi.fn();
        const live = fakeHuddle({ phase: "listening", sessionId: 3 });
        const { rerender } = render(<HuddleStrip agentName="Front desk" huddle={live} onOpen={onOpen} />);
        fireEvent.click(screen.getByRole("button", { name: "Open" }));
        expect(onOpen).toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: "End" }));
        expect(live.end).toHaveBeenCalled();
        rerender(<HuddleStrip agentName="Front desk" huddle={fakeHuddle({ phase: "ended" })} onOpen={onOpen} />);
        expect(screen.queryByTestId("huddle-strip")).toBeNull();
    });

    it("names the agent when it speaks", () => {
        expect(huddleLabel("speaking", "Meera")).toBe("Meera is speaking");
        expect(huddleLabel("listening", "Meera")).toBe("Listening");
    });
});

describe("huddleApi", () => {
    it("starts on the agent's route and connects on the huddle socket", async () => {
        api.start.mockResolvedValue({ data: { id: 5, state: "connecting", state_version: 1 } });
        const routes = huddleApi(7);
        await routes.start({ threadId: null, draft: "" });
        expect(api.start).toHaveBeenCalledWith({ path: { workflow_id: 7 } });
        expect(routes.socketPath(5)).toBe("/api/v1/ws/huddle/5");
        await routes.end(5, "user_ended");
        expect(api.end).toHaveBeenCalledWith({ path: { session_id: 5 }, body: { reason: "user_ended" } });
    });
});
