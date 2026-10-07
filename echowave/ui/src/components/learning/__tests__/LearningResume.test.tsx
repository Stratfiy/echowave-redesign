/** The resume link on the Chat start: the latest goal, or nothing at all. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LearningResume } from "../LearningResume";

const api = vi.hoisted(() => ({ goals: vi.fn() }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/client/sdk.gen", () => ({ myLearningGoalsApiV1LearningGoalsGet: api.goals }));

beforeEach(() => api.goals.mockReset());

describe("LearningResume", () => {
    it("opens the most recent goal", async () => {
        api.goals.mockResolvedValue({ data: [{ goal_id: "g-1", title: "Spanish" }, { goal_id: "g-2", title: "Chess" }] });
        const onOpen = vi.fn();
        render(<LearningResume onOpen={onOpen} />);
        fireEvent.click(await screen.findByText("Continue learning: Spanish"));
        expect(onOpen).toHaveBeenCalledWith("g-1");
    });

    it("shows nothing without a goal, or when the list fails", async () => {
        api.goals.mockResolvedValue({ data: [] });
        const { container, rerender } = render(<LearningResume onOpen={vi.fn()} />);
        await waitFor(() => expect(api.goals).toHaveBeenCalled());
        expect(container.textContent).toBe("");
        api.goals.mockResolvedValue({ error: { detail: "x" } });
        rerender(<LearningResume onOpen={vi.fn()} />);
        expect(container.textContent).toBe("");
    });
});
