/**
 * A huddle in the thread: who said what, the latest lines first in view,
 * the rest behind "Show all", a cut-off reply marked, and how many changes
 * it proposed.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TimelineEvent } from "@/client/types.gen";

import { HuddleEvent, huddleLines } from "../HuddleEvent";

function event(turns: unknown[], cards: number[] = []): TimelineEvent {
    return {
        id: 1,
        kind: "huddle",
        actor: "human",
        summary: "Huddle with Front desk",
        at: "2026-10-09T10:00:00Z",
        workflow_id: 7,
        payload: { turns, cards },
    } as unknown as TimelineEvent;
}

describe("HuddleEvent", () => {
    it("shows who said what, with a cut-off reply marked", () => {
        render(
            <ul>
                <HuddleEvent
                    event={event(
                        [
                            { who: "you", text: "Why did the 3pm call escalate?" },
                            { who: "agent", text: "The caller asked for a refund", interrupted: true },
                        ],
                        [42],
                    )}
                    agentName="Front desk"
                    when="9 Oct, 3:30 pm"
                />
            </ul>,
        );
        const row = screen.getByTestId("huddle-event");
        expect(row.textContent).toContain("You: Why did the 3pm call escalate?");
        expect(row.textContent).toContain("Front desk: The caller asked for a refund (cut off)");
        expect(row.textContent).toContain("1 change proposed");
    });

    it("keeps a long huddle compact until asked", () => {
        const turns = Array.from({ length: 7 }, (_, i) => ({ who: i % 2 ? "agent" : "you", text: `line ${i}` }));
        render(
            <ul>
                <HuddleEvent event={event(turns)} agentName="Front desk" when="" />
            </ul>,
        );
        expect(screen.queryByText(/line 0/)).toBeNull();
        expect(screen.getByText(/line 6/)).toBeTruthy();
        expect(screen.getByText("3 earlier lines")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Show all 7 lines" }));
        expect(screen.getByText(/line 0/)).toBeTruthy();
    });

    it("reads nothing as nothing rather than failing", () => {
        expect(huddleLines({ payload: null } as unknown as TimelineEvent)).toEqual([]);
        render(
            <ul>
                <HuddleEvent event={event([])} agentName="Front desk" when="" />
            </ul>,
        );
        expect(screen.getByText("Nothing was said.")).toBeTruthy();
    });
});
