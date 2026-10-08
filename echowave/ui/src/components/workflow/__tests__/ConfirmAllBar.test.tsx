/**
 * Confirm all: one press for a run of outreach send cards, still one
 * approval per card. Each card is named with the version it showed; a card
 * the server refuses is listed with its reason, the others go.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const confirmAll = vi.hoisted(() => vi.fn());
vi.mock("@/client/sdk.gen", () => ({
    confirmAllActionsApiV1TimelineActionsConfirmAllPost: confirmAll,
    settleActionApiV1TimelineActionsSettlePost: vi.fn(),
    checkOrderApiV1ReachOrdersOrderIdCheckPost: vi.fn(),
}));

import { ConfirmAllBar, waitingSends } from "../ConfirmAllBar";

const card = (id: number, to: string, extra: Record<string, unknown> = {}) => ({
    id,
    at: "2026-10-07T00:00:00Z",
    kind: "action_proposed",
    actor: "agent",
    summary: "Gmail — Send Email via gmail",
    payload: {
        action: "run_tool",
        state: "proposed",
        reaches_people: true,
        version: `v${id}`,
        preview: `To: ${to}\nSubject: Hello\n\nBody for ${to}`,
        ...extra,
    },
    is_deliverable: false,
    workflow_id: null,
    workflow_run_id: null,
    folder_id: null,
});

beforeEach(() => confirmAll.mockReset());

describe("which cards it may name", () => {
    it("only waiting sends that showed their recipient and text", () => {
        const events = [
            card(1, "a@example.com"),
            card(2, "b@example.com", { state: "armed" }),
            card(3, "c@example.com", { preview: "" }),
            card(4, "d@example.com", { action: "turn_bot_on" }),
            card(5, "e@example.com", { version: undefined }),
            card(6, "f@example.com"),
        ];
        expect(waitingSends(events as never).map((e) => e.id)).toEqual([1, 6]);
    });

    it("is not shown for a single card", () => {
        const { container } = render(<ConfirmAllBar events={[card(1, "a@example.com")] as never} />);
        expect(container.textContent).toBe("");
    });
});

describe("pressing it", () => {
    it("sends each card with its own version and lists the ones refused", async () => {
        confirmAll.mockResolvedValue({
            data: {
                confirmed: 1,
                results: [
                    { event_id: 1, ok: true, state: "armed" },
                    { event_id: 2, ok: false, reason: "This changed since you looked at it." },
                ],
            },
        });
        const onDone = vi.fn();
        render(
            <ConfirmAllBar
                events={[card(1, "a@example.com"), card(2, "b@example.com")] as never}
                onDone={onDone}
            />,
        );
        expect(screen.getByText(/2 emails are waiting/)).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Confirm all 2" }));
        await waitFor(() => expect(confirmAll).toHaveBeenCalled());
        expect(confirmAll.mock.calls[0][0].body).toEqual({
            items: [
                { event_id: 1, version: "v1" },
                { event_id: 2, version: "v2" },
            ],
        });
        await waitFor(() => expect(onDone).toHaveBeenCalled());
        expect(screen.getByText(/To: b@example.com: This changed since you looked at it/)).toBeTruthy();
    });
});
