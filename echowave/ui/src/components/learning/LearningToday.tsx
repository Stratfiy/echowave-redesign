"use client";

/**
 * Learning in Today: reviews that are due, and at most three suggestions
 * from marked practice (stuck, improving, review).
 *
 * Shown only when `learning` and `learning_today` are on, and only when it
 * has something: no reviews and no suggestions draws nothing, because Today
 * must not manufacture urgency (handoff 22). A failed load says so in one
 * line with Retry instead of disappearing. Each row opens the lesson inside
 * Chat (screen 13) on that review.
 */

import { BookOpen } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
    learningReviewsDueApiV1LearningReviewsGet,
    learningSuggestionsApiV1LearningSuggestionsGet,
} from "@/client/sdk.gen";
import type { LearningReviewDue, LearningSuggestion } from "@/client/types.gen";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import { formatDay, resumeHref } from "@/lib/learning";

export function suggestionHref(suggestion: LearningSuggestion): string {
    if (suggestion.action === "review") return resumeHref(suggestion.goal_id, suggestion.skill_id);
    return resumeHref(suggestion.goal_id);
}

export function LearningToday() {
    const learning = useFeature("learning");
    const today = useFeature("learning_today");
    const { user, loading: authLoading } = useAuth();
    const userId = user?.id ?? null;
    const [reviews, setReviews] = useState<LearningReviewDue[] | null>(null);
    const [suggestions, setSuggestions] = useState<LearningSuggestion[]>([]);
    const [failed, setFailed] = useState(false);

    const load = useCallback(async () => {
        setFailed(false);
        const [due, ideas] = await Promise.all([
            learningReviewsDueApiV1LearningReviewsGet(),
            learningSuggestionsApiV1LearningSuggestionsGet(),
        ]);
        if (due.error || !due.data) {
            setFailed(true);
            return;
        }
        setReviews(due.data);
        // Suggestions are extra: a failed read leaves them out, not the reviews.
        setSuggestions(ideas.data ?? []);
    }, []);

    useEffect(() => {
        if (!learning || !today || authLoading || userId === null) return;
        void load();
    }, [learning, today, authLoading, userId, load]);

    if (!learning || !today) return null;
    if (failed) {
        return (
            <p role="alert" className="mx-4 my-2 text-sm text-muted-foreground sm:mx-6">
                Learning reviews could not load.{" "}
                <button type="button" className="min-h-11 underline underline-offset-4" onClick={() => void load()}>
                    Try again
                </button>
            </p>
        );
    }
    if (!reviews) return null;
    // Suggestions that repeat a review row add nothing.
    const reviewKeys = new Set(reviews.map((r) => `${r.goal_id}:${r.skill_id}`));
    const extra = suggestions.filter((s) => !(s.kind === "review" && reviewKeys.has(`${s.goal_id}:${s.skill_id}`)));
    if (reviews.length === 0 && extra.length === 0) return null;

    return (
        <section
            aria-labelledby="learning-today"
            className="mx-4 my-3 rounded-lg border border-border p-4 sm:mx-6"
            data-testid="learning-today"
        >
            <h2 id="learning-today" className="flex items-center gap-1.5 text-sm font-semibold">
                <BookOpen aria-hidden className="h-4 w-4" />
                Learning
            </h2>
            {reviews.length > 0 && (
                <ul className="mt-2 flex flex-col divide-y divide-border" aria-label="Reviews due">
                    {reviews.map((review) => (
                        <li key={`${review.goal_id}:${review.skill_id}`} className="motion-m6-enter">
                            <Link
                                href={resumeHref(review.goal_id, review.skill_id)}
                                className="motion-m1 flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-0.5 py-2 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#245B9A]"
                            >
                                <span className="min-w-0 break-words">
                                    <span className="font-medium">Review {review.skill_name}</span>
                                    <span className="text-muted-foreground"> · {review.goal_title}</span>
                                </span>
                                <span className="text-sm text-muted-foreground">
                                    {review.label} · due {formatDay(review.due_at)}
                                </span>
                            </Link>
                        </li>
                    ))}
                </ul>
            )}
            {extra.length > 0 && (
                <ul className="mt-2 flex flex-col gap-1" aria-label="Suggestions">
                    {extra.map((suggestion) => (
                        <li key={`${suggestion.kind}:${suggestion.goal_id}:${suggestion.skill_id}`}>
                            <Link
                                href={suggestionHref(suggestion)}
                                className="motion-m1 flex min-h-11 items-center text-sm underline-offset-4 hover:underline"
                            >
                                {suggestion.text}
                            </Link>
                        </li>
                    ))}
                </ul>
            )}
        </section>
    );
}

export default LearningToday;
