/**
 * During a live call the huddle is that call's whisper channel: marked "This
 * call only", with a box to type a whisper; nothing while live supervision is
 * off, the huddle is not live, or the agent has no single call to send to.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ liveCall: vi.fn(), whisper: vi.fn() }));
const flags = vi.hoisted(() => ({ live_supervision: true }) as Record<string, boolean>);

vi.mock("@/client/sdk.gen", () => ({
    huddleLiveCallApiV1HuddleWorkflowIdLiveCallGet: api.liveCall,
    huddleWhisperApiV1HuddleWorkflowIdWhisperPost: api.whisper,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(flags[name]) }));

import { HuddleLiveCall, THIS_CALL_ONLY_COPY } from "../HuddleLiveCall";

const LIVE = {
    live: true,
    label: "This call only",
    run_id: 88,
    direction: "inbound",
    caller: "+919812345678",
    started_at: "2026-10-09T06:00:00Z",
    calls: 1,
};

beforeEach(() => {
    api.liveCall.mockReset();
    api.whisper.mockReset();
    flags.live_supervision = true;
});

describe("HuddleLiveCall", () => {
    it("marks the huddle 'This call only' and sends a typed whisper", async () => {
        api.liveCall.mockResolvedValue({ data: LIVE });
        api.whisper.mockResolvedValue({
            data: { id: "w1", at: "2026-10-09T06:00:05Z", text: "Offer the 4pm slot", run_id: 88, label: "This call only" },
        });
        render(<HuddleLiveCall workflowId={7} active />);
        expect(await screen.findByText("This call only")).toBeTruthy();
        expect(screen.getByText(THIS_CALL_ONLY_COPY)).toBeTruthy();
        expect(screen.getByText("+919812345678")).toBeTruthy();
        fireEvent.change(screen.getByLabelText("Type a whisper to the call"), { target: { value: "Offer the 4pm slot" } });
        fireEvent.click(screen.getByRole("button", { name: /Send/ }));
        await waitFor(() =>
            expect(api.whisper).toHaveBeenCalledWith({ path: { workflow_id: 7 }, body: { text: "Offer the 4pm slot" } }),
        );
        expect(await screen.findByText("Sent: Offer the 4pm slot")).toBeTruthy();
    });

    it("says why a whisper did not go", async () => {
        api.liveCall.mockResolvedValue({ data: LIVE });
        api.whisper.mockResolvedValue({ error: { detail: "This call has ended." } });
        render(<HuddleLiveCall workflowId={7} active />);
        fireEvent.change(await screen.findByLabelText("Type a whisper to the call"), { target: { value: "Hello" } });
        fireEvent.click(screen.getByRole("button", { name: /Send/ }));
        expect((await screen.findByRole("alert")).textContent).toBe("This call has ended.");
    });

    it("with several live calls, chooses none and says so", async () => {
        api.liveCall.mockResolvedValue({ data: { live: false, calls: 2 } });
        render(<HuddleLiveCall workflowId={7} active />);
        expect(await screen.findByText(/The agent is on 2 live calls/)).toBeTruthy();
        expect(screen.queryByLabelText("Type a whisper to the call")).toBeNull();
    });

    it("shows nothing with no live call", async () => {
        api.liveCall.mockResolvedValue({ data: { live: false, calls: 0 } });
        const { container } = render(<HuddleLiveCall workflowId={7} active />);
        await waitFor(() => expect(api.liveCall).toHaveBeenCalled());
        expect(container.innerHTML).toBe("");
    });

    it("asks nothing while live supervision is off or the huddle is not live", () => {
        flags.live_supervision = false;
        const { container, rerender } = render(<HuddleLiveCall workflowId={7} active />);
        expect(container.innerHTML).toBe("");
        flags.live_supervision = true;
        rerender(<HuddleLiveCall workflowId={7} active={false} />);
        expect(container.innerHTML).toBe("");
        expect(api.liveCall).not.toHaveBeenCalled();
    });
});
