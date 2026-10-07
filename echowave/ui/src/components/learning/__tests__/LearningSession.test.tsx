/**
 * Screen 13, the lesson inside Chat: profile first, a new goal on any
 * subject, ask before saving something sensitive, one baseline question,
 * then lesson, practice and specific feedback with Try again and Next. An
 * answer is sent with one idempotency key until it is marked, so a retry
 * after a dropped connection is the same attempt and the draft is kept. No
 * teacher here is "needs setup", never a made-up lesson.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LearningSession } from "../LearningSession";

const api = vi.hoisted(() => ({
    status: vi.fn(),
    profile: vi.fn(),
    start: vi.fn(),
    session: vi.fn(),
    baseline: vi.fn(),
    next: vi.fn(),
    review: vi.fn(),
    attempt: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock("@/lib/features", () => ({ useFeature: () => false }));
vi.mock("@/lib/useDictation", () => ({
    appendDictation: (a: string, b: string) => `${a} ${b}`,
    canDictate: () => false,
    useDictation: () => ({ listening: false, transcribing: false, error: null, start: vi.fn(), stop: vi.fn() }),
}));
vi.mock("@/client/sdk.gen", () => ({
    learningStatusApiV1LearningStatusGet: api.status,
    saveLearnerProfileApiV1LearningProfilePut: api.profile,
    startLearningGoalApiV1LearningGoalsPost: api.start,
    learningSessionApiV1LearningGoalsGoalIdSessionGet: api.session,
    answerLearningBaselineApiV1LearningGoalsGoalIdBaselinePost: api.baseline,
    nextLearningExerciseApiV1LearningGoalsGoalIdNextPost: api.next,
    startLearningReviewApiV1LearningGoalsGoalIdReviewPost: api.review,
    submitLearningAttemptApiV1LearningGoalsGoalIdAttemptsPost: api.attempt,
}));

const profile = {
    explanation_language: "hi-IN",
    suggested_language: null,
    learner_kind: "adult",
    studying_for: null,
    adult_confirmed: true,
    revision: 1,
};
const status = { state: "available", reason: null, teacher: "model", profile, languages: ["en-IN", "hi-IN"], today: true };
const goal = {
    goal_id: "g-1",
    title: "Fractions",
    studying_for: "Class 10 boards",
    explanation_language: "hi-IN",
    has_material: false,
    status: "active",
    review_reminders: false,
    revision: 0,
};
const lesson = {
    lesson_id: 7,
    objective: "Add two fractions",
    explanation: "Find a common denominator first.",
    source_kind: "general",
    sources: [],
    skill: "Adding fractions",
    skill_id: 3,
};
const exercise = {
    exercise_id: "e-1",
    kind: "practice",
    prompt: "What is 1/2 + 1/4?",
    rubric: [{ criterion: "answer", description: "Gives 3/4" }],
    status: "open",
};
const waiting = { goal, state: "waiting_for_answer", lesson, exercise, attempts: [] };
const partly = {
    attempt_id: 1,
    answer: "2/6",
    outcome: "partly",
    rubric_results: [
        { criterion: "method", met: true, note: "Common denominator used." },
        { criterion: "answer", met: false, note: "The sum is 3/4." },
    ],
    feedback: "Good method; the final sum is off.",
};

function open(goalId: string | null = "g-1") {
    return render(<LearningSession goalId={goalId} onGoalChange={vi.fn()} onClose={vi.fn()} />);
}

beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    api.status.mockResolvedValue({ data: status });
    api.session.mockResolvedValue({ data: waiting });
});

describe("Honest states", () => {
    it("says needs setup and offers no lesson when no teacher can run", async () => {
        api.status.mockResolvedValue({ data: { ...status, state: "needs_setup", reason: "Lessons need a model key." } });
        open();
        expect(await screen.findByTestId("learning-needs-setup")).toBeTruthy();
        expect(screen.getByText("Lessons need a model key.")).toBeTruthy();
        expect(screen.queryByText("What is 1/2 + 1/4?")).toBeNull();
    });

    it("labels the sample teacher", async () => {
        api.status.mockResolvedValue({ data: { ...status, teacher: "sample" } });
        open();
        expect(await screen.findByText("Sample teacher")).toBeTruthy();
    });

    it("shows a failed load as a failure with retry", async () => {
        api.session.mockResolvedValue({ error: { detail: { code: "not_found", message: "That learning goal is not here." } } });
        open();
        expect(await screen.findByText("Could not load this lesson")).toBeTruthy();
        expect(screen.getByText("That learning goal is not here.")).toBeTruthy();
    });
});

describe("Profile and a new goal", () => {
    it("asks adults to confirm before anything else", async () => {
        api.status.mockResolvedValue({ data: { ...status, profile: { ...profile, adult_confirmed: false, revision: 0 } } });
        api.profile.mockResolvedValue({ data: { ...profile } });
        open(null);
        fireEvent.click(await screen.findByText("Continue"));
        expect(await screen.findByText(/Learning is for adults for now/)).toBeTruthy();
        expect(api.profile).not.toHaveBeenCalled();
        fireEvent.click(screen.getByLabelText("I am 18 or older."));
        fireEvent.click(screen.getByLabelText("A student"));
        fireEvent.click(screen.getByText("Continue"));
        await waitFor(() => expect(api.profile).toHaveBeenCalled());
        expect(api.profile.mock.calls[0][0].body).toMatchObject({ adult_confirmed: true, learner_kind: "student", revision: 0 });
        expect(await screen.findByText("What do you want to learn?")).toBeTruthy();
    });

    it("asks before saving something sensitive, and saves on yes", async () => {
        const onGoalChange = vi.fn();
        api.start
            .mockResolvedValueOnce({
                error: {
                    detail: {
                        code: "confirm_sensitive",
                        categories: ["health"],
                        message: "This looks personal (health). Save it to your learning record?",
                    },
                },
            })
            .mockResolvedValueOnce({ data: { goal, state: "baseline", baseline_question: "What do you know?", attempts: [] } });
        render(<LearningSession goalId={null} onGoalChange={onGoalChange} onClose={vi.fn()} />);
        fireEvent.change(await screen.findByLabelText("What do you want to learn?"), { target: { value: "Diabetes diet" } });
        fireEvent.click(screen.getByText("Start"));
        expect(await screen.findByTestId("learning-sensitive")).toBeTruthy();
        expect(api.start.mock.calls[0][0].body.confirm_sensitive).toBe(false);
        fireEvent.click(screen.getByText("Save it"));
        await waitFor(() => expect(onGoalChange).toHaveBeenCalledWith("g-1"));
        expect(api.start.mock.calls[1][0].body).toMatchObject({ title: "Diabetes diet", confirm_sensitive: true, explanation_language: "hi-IN" });
    });
});

describe("The lesson", () => {
    it("asks one baseline question first", async () => {
        api.session.mockResolvedValue({ data: { goal: { ...goal, status: "baseline" }, state: "baseline", baseline_question: "What do you know about fractions?", attempts: [] } });
        api.baseline.mockResolvedValue({ data: waiting });
        open();
        expect(await screen.findByText("What do you know about fractions?")).toBeTruthy();
        fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "A little" } });
        fireEvent.click(screen.getByText("Send answer"));
        expect(await screen.findByText("What is 1/2 + 1/4?")).toBeTruthy();
        expect(api.baseline.mock.calls[0][0].body).toEqual({ answer: "A little" });
    });

    it("shows a short lesson, says it is general, and one practice prompt", async () => {
        open();
        expect(await screen.findByText("Find a common denominator first.")).toBeTruthy();
        expect(screen.getByTestId("learning-source").textContent).toMatch(/General explanation/);
        expect(screen.getByText("What is 1/2 + 1/4?")).toBeTruthy();
        expect(screen.getByText("Progress").getAttribute("href")).toBe("/learning/g-1");
    });

    it("says when the lesson comes from the person's notes", async () => {
        api.session.mockResolvedValue({
            data: { ...waiting, lesson: { ...lesson, source_kind: "material", sources: ["Chapter 3: fractions"] } },
        });
        open();
        expect((await screen.findByTestId("learning-source")).textContent).toBe("From your notes");
        expect(screen.getByText("Chapter 3: fractions")).toBeTruthy();
    });

    it("gives specific feedback, then Try again and Next", async () => {
        api.attempt.mockResolvedValue({
            data: { attempt: partly, replayed: false, session: { ...waiting, state: "correction", attempts: [partly] } },
        });
        open();
        fireEvent.change(await screen.findByLabelText("Your answer"), { target: { value: "2/6" } });
        fireEvent.click(screen.getByText("Submit answer"));
        const block = await screen.findByTestId("learning-feedback");
        expect(block.getAttribute("data-outcome")).toBe("partly");
        expect(screen.getByText("Partly there")).toBeTruthy();
        expect(screen.getByText("Good method; the final sum is off.")).toBeTruthy();
        expect(screen.getByText(/The sum is 3\/4/)).toBeTruthy();
        expect(screen.getByLabelText("Try again")).toBeTruthy();
        expect(screen.getByText("Next exercise")).toBeTruthy();
        expect(screen.getByText("Try a smaller step")).toBeTruthy();
        // No score, no percentage.
        expect(document.body.textContent).not.toMatch(/%|score|mastery/i);
    });

    it("a dropped connection keeps the answer, and the retry is the same attempt", async () => {
        api.attempt.mockRejectedValueOnce(new Error("network")).mockResolvedValueOnce({
            data: { attempt: partly, replayed: false, session: { ...waiting, state: "correction", attempts: [partly] } },
        });
        open();
        const box = await screen.findByLabelText("Your answer");
        fireEvent.change(box, { target: { value: "2/6" } });
        fireEvent.click(screen.getByText("Submit answer"));
        expect(await screen.findByText(/It is kept here/)).toBeTruthy();
        expect((screen.getByLabelText("Your answer") as HTMLTextAreaElement).value).toBe("2/6");
        fireEvent.click(screen.getByText("Submit answer"));
        await screen.findByTestId("learning-feedback");
        const first = api.attempt.mock.calls[0][0].headers["Idempotency-Key"];
        const second = api.attempt.mock.calls[1][0].headers["Idempotency-Key"];
        expect(first).toBeTruthy();
        expect(second).toBe(first);
    });

    it("a completed exercise offers the next one", async () => {
        const passed = { ...partly, outcome: "passed", rubric_results: [{ criterion: "answer", met: true }], feedback: "Correct." };
        api.session.mockResolvedValue({ data: { ...waiting, state: "completed", exercise: { ...exercise, status: "passed" }, attempts: [passed] } });
        api.next.mockResolvedValue({ data: { ...waiting, exercise: { ...exercise, exercise_id: "e-2", prompt: "What is 1/3 + 1/3?" } } });
        open();
        expect(await screen.findByText("Correct")).toBeTruthy();
        fireEvent.click(screen.getByText("Next exercise"));
        expect(await screen.findByText("What is 1/3 + 1/3?")).toBeTruthy();
    });

    it("opens on a review when asked from Today", async () => {
        api.review.mockResolvedValue({ data: { ...waiting, exercise: { ...exercise, kind: "review" } } });
        render(<LearningSession goalId="g-1" reviewSkillId={3} onGoalChange={vi.fn()} onClose={vi.fn()} />);
        await waitFor(() => expect(api.review).toHaveBeenCalled());
        expect(api.review.mock.calls[0][0]).toMatchObject({ path: { goal_id: "g-1" }, body: { skill_id: 3 } });
        expect(await screen.findByText(/Review · Adding fractions/)).toBeTruthy();
    });
});
