/**
 * Screen 14: evidence only (plain labels, marked answers, no percentages),
 * "no practice yet" is not zero ability, a skill opens to its rubric and
 * attempts, the editor keeps the save contract, and Delete is the controls
 * approval card -- nothing is deleted on the first press.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LearningProgress } from "../LearningProgress";

const api = vi.hoisted(() => ({
    progress: vi.fn(),
    skill: vi.fn(),
    status: vi.fn(),
    deletion: vi.fn(),
    settle: vi.fn(),
    update: vi.fn(),
    exported: vi.fn(),
    session: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/client/sdk.gen", () => ({
    learningProgressApiV1LearningGoalsGoalIdProgressGet: api.progress,
    learningSkillApiV1LearningGoalsGoalIdSkillsSkillIdGet: api.skill,
    learningStatusApiV1LearningStatusGet: api.status,
    requestLearningDeletionApiV1LearningGoalsGoalIdDeletionPost: api.deletion,
    settleActionApiV1TimelineActionsSettlePost: api.settle,
    updateLearningGoalApiV1LearningGoalsGoalIdPatch: api.update,
    exportLearningGoalApiV1LearningGoalsGoalIdExportGet: api.exported,
    learningSessionApiV1LearningGoalsGoalIdSessionGet: api.session,
}));

const goal = {
    goal_id: "g-1",
    title: "Fractions",
    studying_for: null,
    explanation_language: "ta-IN",
    has_material: false,
    status: "active",
    review_reminders: false,
    revision: 2,
};
const practised = {
    goal,
    practice_count: 3,
    state: "practised",
    skills: [
        { skill_id: 1, name: "Adding fractions", status: "practised", label: "Practised", evaluated_attempts: 2, passed_attempts: 1, review_due: false, next_review_at: "2026-10-09T09:00:00Z" },
        { skill_id: 2, name: "Simplifying", status: "needs_another_attempt", label: "Needs another attempt", evaluated_attempts: 1, passed_attempts: 0, review_due: true, next_review_at: "2026-10-06T09:00:00Z" },
    ],
    recent: [
        { attempt_id: 9, answer: "3/4", outcome: "passed", rubric_results: [], feedback: "Correct.", created_at: "2026-10-07T09:00:00Z", exercise_prompt: "1/2 + 1/4?", exercise_kind: "practice" },
    ],
    next_step: { kind: "review", skill_id: 2, text: "Review Simplifying." },
};

beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    api.status.mockResolvedValue({ data: { languages: ["en-IN", "ta-IN"] } });
    api.progress.mockResolvedValue({ data: practised });
});

describe("Evidence", () => {
    it("shows plain labels from marked answers and one next step, never a score", async () => {
        render(<LearningProgress goalId="g-1" />);
        expect(await screen.findByText("Fractions")).toBeTruthy();
        expect(screen.getByText("3 practice answers marked")).toBeTruthy();
        expect(screen.getByText("Practised")).toBeTruthy();
        expect(screen.getByText("Needs another attempt")).toBeTruthy();
        expect(screen.getByText(/Review due/)).toBeTruthy();
        expect(screen.getByText("Review Simplifying.")).toBeTruthy();
        expect(screen.getByText("Start the review").getAttribute("href")).toBe("/overview?learn=g-1&review=2");
        expect(screen.getByText("Continue practice").getAttribute("href")).toBe("/overview?learn=g-1");
        expect(document.body.textContent).not.toMatch(/%|streak|fluent|score/i);
    });

    it("says no practice yet, not zero", async () => {
        api.progress.mockResolvedValue({ data: { ...practised, practice_count: 0, state: "no_practice", skills: [], recent: [], next_step: { kind: "continue", text: "Continue practice." } } });
        render(<LearningProgress goalId="g-1" />);
        expect((await screen.findByTestId("learning-no-practice")).textContent).toMatch(/No practice yet/);
        expect(screen.getByText("0 practice answers marked")).toBeTruthy();
    });

    it("opens a skill to its rubric and attempts", async () => {
        api.skill.mockResolvedValue({
            data: {
                skill: practised.skills[0],
                rubric: [{ criterion: "answer", description: "Gives the sum." }],
                attempts: [{ attempt_id: 4, answer: "2/6", outcome: "not_yet", rubric_results: [], feedback: "Find a common denominator." }],
            },
        });
        render(<LearningProgress goalId="g-1" />);
        fireEvent.click(await screen.findByText("Adding fractions"));
        expect(await screen.findByText("Gives the sum.")).toBeTruthy();
        expect(screen.getByText("Find a common denominator.")).toBeTruthy();
    });

    it("keeps what was loaded, labelled, when a refresh fails", async () => {
        api.progress.mockResolvedValueOnce({ error: { detail: { code: "not_found", message: "That learning goal is not here." } } });
        render(<LearningProgress goalId="g-1" />);
        expect(await screen.findByText("Could not load this goal's progress")).toBeTruthy();
    });
});

describe("Editor and delete", () => {
    it("a stale save shows what is saved now and keeps the draft", async () => {
        api.update.mockResolvedValue({
            error: { detail: { code: "conflict", message: "This was changed somewhere else.", stored: { ...goal, title: "Decimals", revision: 3 } } },
        });
        render(<LearningProgress goalId="g-1" />);
        fireEvent.click(await screen.findByText("Edit goal"));
        fireEvent.change(screen.getByLabelText("Goal"), { target: { value: "Fractions and ratios" } });
        fireEvent.click(screen.getByText("Save"));
        expect((await screen.findByTestId("learning-conflict")).textContent).toMatch(/Decimals/);
        expect((screen.getByLabelText("Goal") as HTMLInputElement).value).toBe("Fractions and ratios");
        expect(api.update.mock.calls[0][0].body).toMatchObject({ revision: 2, title: "Fractions and ratios" });
    });

    it("Delete puts up the approval card and only Approve settles it, with its version", async () => {
        api.deletion.mockResolvedValue({ data: { status: "proposed", event_id: 55, payload: { state: "proposed", version: "abc123" } } });
        api.settle.mockResolvedValue({ data: {} });
        api.session.mockResolvedValue({ data: {} });
        render(<LearningProgress goalId="g-1" />);
        fireEvent.click(await screen.findByText("Delete…"));
        const card = await screen.findByTestId("action-preview");
        expect(card.textContent).toMatch(/Delete learning goal/);
        expect(card.textContent).toMatch(/Fractions/);
        expect(card.textContent).toMatch(/cannot be undone/);
        expect(api.settle).not.toHaveBeenCalled();
        fireEvent.click(screen.getByText("Approve"));
        await waitFor(() => expect(api.settle).toHaveBeenCalled());
        expect(api.settle.mock.calls[0][0].body).toEqual({ event_id: 55, verb: "confirm", version: "abc123" });
    });

    it("Cancel on the card declines it and nothing is deleted", async () => {
        api.deletion.mockResolvedValue({ data: { status: "proposed", event_id: 56, payload: { state: "proposed" } } });
        api.settle.mockResolvedValue({ data: {} });
        render(<LearningProgress goalId="g-1" />);
        fireEvent.click(await screen.findByText("Delete…"));
        fireEvent.click(await screen.findByText("Cancel"));
        await waitFor(() => expect(api.settle.mock.calls[0][0].body.verb).toBe("decline"));
        expect(await screen.findByText("Cancelled. Nothing was done.")).toBeTruthy();
    });
});
