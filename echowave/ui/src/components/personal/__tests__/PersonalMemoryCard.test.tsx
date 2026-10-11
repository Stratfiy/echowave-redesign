/**
 * The person's own memory card: what Decibyl keeps about them, each item
 * with where it came from and when, and Correct / Forget on each -- in the
 * thread, never on another screen. A correction that cannot be read is said
 * in words and nothing changes; a forget asks once more first. A proposal
 * keeps nothing until Save.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getCard = vi.hoisted(() => vi.fn());
const answer = vi.hoisted(() => vi.fn());
const correct = vi.hoisted(() => vi.fn());
const forget = vi.hoisted(() => vi.fn());
const correctLearned = vi.hoisted(() => vi.fn());
const forgetLearned = vi.hoisted(() => vi.fn());
vi.mock("@/client/sdk.gen", () => ({
    cardApiV1PersonalCardsEventIdGet: getCard,
    answerProposalApiV1PersonalCardsEventIdAnswerPost: answer,
    correctPreferenceApiV1PersonalPreferencesPreferenceIdCorrectPost: correct,
    forgetPreferenceApiV1PersonalPreferencesPreferenceIdDelete: forget,
    correctLearnedApiV1PersonalLearnedFactIdCorrectPost: correctLearned,
    forgetLearnedApiV1PersonalLearnedFactIdDelete: forgetLearned,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import { PersonalMemoryCard, sourceLine } from "../PersonalMemoryCard";

const event = (view: string) =>
    ({
        id: 77,
        at: "2026-10-09T00:00:00Z",
        kind: "personal_memory",
        actor: "agent",
        summary: "What Decibyl keeps about you",
        payload: { view, private_to: 1 },
        is_deliverable: false,
        workflow_id: null,
        workflow_run_id: null,
        folder_id: null,
    }) as never;

const tamil = {
    id: 5,
    kind: "language",
    topic: "calls",
    label: "Calls in Tamil",
    value: "ta-IN",
    status: "confirmed",
    observed_at: "2026-10-09T05:00:00Z",
    source: { kind: "message", excerpt: "Tamil for calls" },
    history: [{ id: 4, label: "Calls in English", observed_at: "2026-10-02T05:00:00Z" }],
};

beforeEach(() => {
    for (const mock of [getCard, answer, correct, forget, correctLearned, forgetLearned]) mock.mockReset();
});

describe("PersonalMemoryCard", () => {
    it("lists preferences and learned items with source, date and history", async () => {
        getCard.mockResolvedValue({
            data: {
                event_id: 77,
                view: "about_me",
                preferences: [tamil],
                learned: [
                    {
                        id: 9,
                        label: "tea: masala",
                        observed_at: "2026-10-08T05:00:00Z",
                        source: { line: "From your conversations with Decibyl" },
                    },
                ],
            },
        });
        render(<PersonalMemoryCard event={event("about_me")} />);
        expect(await screen.findByText("Calls in Tamil")).toBeTruthy();
        expect(screen.getByText(/You said “Tamil for calls”/)).toBeTruthy();
        expect(screen.getByTestId("memory-history").textContent).toContain("Calls in English");
        expect(screen.getByText("tea: masala")).toBeTruthy();
        expect(screen.getByText("Only you can see this.")).toBeTruthy();
        expect(screen.getAllByRole("button", { name: /Correct/ })).toHaveLength(2);
        expect(screen.getAllByRole("button", { name: /Forget/ })).toHaveLength(2);
        expect(getCard).toHaveBeenCalledWith({ path: { event_id: 77 } });
    });

    it("says when nothing is kept yet", async () => {
        getCard.mockResolvedValue({ data: { event_id: 77, view: "about_me", preferences: [], learned: [] } });
        render(<PersonalMemoryCard event={event("about_me")} />);
        expect(await screen.findByTestId("memory-empty")).toBeTruthy();
    });

    it("corrects in place, and an unreadable correction is said, not applied", async () => {
        getCard.mockResolvedValue({ data: { event_id: 77, view: "about_me", preferences: [tamil], learned: [] } });
        correct.mockResolvedValueOnce({ error: { detail: "Say the language, like “Hindi”." } });
        render(<PersonalMemoryCard event={event("about_me")} />);
        fireEvent.click(await screen.findByRole("button", { name: /Correct/ }));
        const input = screen.getByLabelText("Correct Calls in Tamil");
        fireEvent.change(input, { target: { value: "purple" } });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(await screen.findByRole("alert")).toBeTruthy();
        expect(screen.getByRole("alert").textContent).toContain("Hindi");
        expect(correct).toHaveBeenCalledWith({ path: { preference_id: 5 }, body: { text: "purple" } });

        correct.mockResolvedValueOnce({ data: { preference: { ...tamil, id: 6, label: "Calls in Hindi" } } });
        getCard.mockResolvedValue({
            data: { event_id: 77, view: "about_me", preferences: [{ ...tamil, id: 6, label: "Calls in Hindi" }], learned: [] },
        });
        fireEvent.change(screen.getByLabelText("Correct Calls in Tamil"), { target: { value: "Hindi" } });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(await screen.findByText("Calls in Hindi")).toBeTruthy();
    });

    it("forgets only after a second press", async () => {
        getCard.mockResolvedValue({ data: { event_id: 77, view: "saved", preferences: [tamil], refused: [] } });
        forget.mockResolvedValue({ data: { forgotten: 2 } });
        render(<PersonalMemoryCard event={event("saved")} />);
        fireEvent.click(await screen.findByRole("button", { name: /Forget/ }));
        expect(forget).not.toHaveBeenCalled();
        expect(screen.getByText("Forget this for good?")).toBeTruthy();
        const group = screen.getByRole("group", { name: "Forget this" });
        fireEvent.click(group.querySelector("button") as HTMLButtonElement);
        await waitFor(() => expect(forget).toHaveBeenCalledWith({ path: { preference_id: 5 } }));
        expect(await screen.findByTestId("memory-forgotten")).toBeTruthy();
    });

    it("shows what was refused on a saved card", async () => {
        getCard.mockResolvedValue({
            data: {
                event_id: 77,
                view: "saved",
                preferences: [],
                refused: ["Calls can only ring between 09:00 and 21:00, so a call from 07:00 was not saved."],
            },
        });
        render(<PersonalMemoryCard event={event("saved")} />);
        expect(await screen.findByText(/was not saved/)).toBeTruthy();
    });

    it("a proposal keeps nothing until Save, and says what happened", async () => {
        getCard.mockResolvedValueOnce({
            data: {
                event_id: 77,
                view: "proposal",
                proposal: { kind: "length", topic: "chat", value: "short", label: "Short replies" },
                why: "From your feedback on a reply",
                state: "open",
            },
        });
        answer.mockResolvedValue({ data: { state: "saved" } });
        render(<PersonalMemoryCard event={event("proposal")} />);
        expect(await screen.findByText("Short replies")).toBeTruthy();
        expect(screen.getByText("Keep this as a preference?")).toBeTruthy();
        // Never a claim that ratings train anything.
        expect(document.body.textContent).not.toMatch(/train/i);
        getCard.mockResolvedValueOnce({
            data: {
                event_id: 77,
                view: "proposal",
                proposal: { kind: "length", topic: "chat", value: "short", label: "Short replies" },
                state: "saved",
            },
        });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        await waitFor(() => expect(answer).toHaveBeenCalledWith({ path: { event_id: 77 }, body: { save: true } }));
        expect(await screen.findByText(/Saved to your preferences/)).toBeTruthy();
    });

    it("a card that is not yours reads as an error, not as empty", async () => {
        getCard.mockResolvedValue({ error: { detail: "That card is not here." } });
        render(<PersonalMemoryCard event={event("about_me")} />);
        expect((await screen.findByRole("alert")).textContent).toContain("not here");
    });
});

describe("sourceLine", () => {
    it("names where each came from", () => {
        expect(sourceLine({ id: 1, source: { kind: "feedback" } })).toBe("From your feedback on a reply");
        expect(sourceLine({ id: 1, source: { kind: "correction" } })).toBe("You corrected it");
        expect(sourceLine({ id: 1, source: { kind: "message", excerpt: "call me after 10" } })).toBe(
            "You said “call me after 10”",
        );
    });
});
