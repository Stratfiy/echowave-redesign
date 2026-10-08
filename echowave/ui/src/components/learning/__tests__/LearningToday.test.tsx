/**
 * Learning in Today: due reviews and suggestions appear, each opening the
 * lesson in Chat; nothing due draws nothing (no manufactured urgency); a
 * failed load says so; switched off, nothing is asked of the server.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LearningToday } from "../LearningToday";

const api = vi.hoisted(() => ({ reviews: vi.fn(), suggestions: vi.fn(), today: vi.fn() }));
const flags = vi.hoisted(() => ({ learning: true, learning_today: true }));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]) }));
vi.mock("@/client/sdk.gen", () => ({
    learningReviewsDueApiV1LearningReviewsGet: api.reviews,
    learningSuggestionsApiV1LearningSuggestionsGet: api.suggestions,
    learningTodayApiV1LearningTodayGet: api.today,
}));

const review = { goal_id: "g-1", goal_title: "Spanish", skill_id: 4, skill_name: "Past tense", status: "practised", label: "Practised", due_at: "2026-10-07T09:00:00Z" };

beforeEach(() => {
    api.reviews.mockReset();
    api.suggestions.mockReset();
    api.today.mockReset();
    api.today.mockResolvedValue({ data: { streak: { days: 0, practised_today: false }, lessons: [] } });
    flags.learning = true;
    flags.learning_today = true;
});

describe("Learning in Today", () => {
    it("lists due reviews that open the lesson on that review", async () => {
        api.reviews.mockResolvedValue({ data: [review] });
        api.suggestions.mockResolvedValue({ data: [{ kind: "stuck", goal_id: "g-2", skill_id: 9, text: "Ratios has been hard twice in a row. Try a smaller step.", action: "easier" }] });
        render(<LearningToday />);
        const link = await screen.findByText("Review Past tense");
        expect(link.closest("a")?.getAttribute("href")).toBe("/overview?learn=g-1&review=4");
        expect(screen.getByText(/Ratios has been hard/)).toBeTruthy();
    });

    it("shows the day's lesson and the streak before anything is due", async () => {
        api.reviews.mockResolvedValue({ data: [] });
        api.suggestions.mockResolvedValue({ data: [] });
        api.today.mockResolvedValue({
            data: {
                streak: { days: 3, practised_today: false },
                lessons: [{ goal_id: "g-7", goal_title: "Python", kind: "continue", skill_id: null, text: "Today's lesson: Loops.", done_today: false }],
            },
        });
        render(<LearningToday />);
        const link = await screen.findByText("Today's lesson: Loops.");
        expect(link.closest("a")?.getAttribute("href")).toBe("/overview?learn=g-7");
        expect(screen.getByText(/3-day streak/)).toBeTruthy();
    });

    it("draws nothing when nothing is due", async () => {
        api.reviews.mockResolvedValue({ data: [] });
        api.suggestions.mockResolvedValue({ data: [] });
        const { container } = render(<LearningToday />);
        await waitFor(() => expect(api.reviews).toHaveBeenCalled());
        expect(container.textContent).toBe("");
    });

    it("does not repeat a review as a suggestion", async () => {
        api.reviews.mockResolvedValue({ data: [review] });
        api.suggestions.mockResolvedValue({ data: [{ kind: "review", goal_id: "g-1", skill_id: 4, text: "Past tense is due for a review.", action: "review" }] });
        render(<LearningToday />);
        await screen.findByText("Review Past tense");
        expect(screen.queryByText("Past tense is due for a review.")).toBeNull();
    });

    it("says a failed load failed, with retry", async () => {
        api.reviews.mockResolvedValueOnce({ error: { detail: "boom" } }).mockResolvedValueOnce({ data: [review] });
        api.suggestions.mockResolvedValue({ data: [] });
        render(<LearningToday />);
        fireEvent.click(await screen.findByText("Try again"));
        expect(await screen.findByText("Review Past tense")).toBeTruthy();
    });

    it("off, it asks nothing and shows nothing", async () => {
        flags.learning_today = false;
        const { container } = render(<LearningToday />);
        expect(container.textContent).toBe("");
        expect(api.reviews).not.toHaveBeenCalled();
    });
});
