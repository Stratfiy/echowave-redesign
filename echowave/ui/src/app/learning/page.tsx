"use client";

/**
 * A person's learning goals: where the progress pages are reached from
 * besides the lesson's own link and Today. A list, not a dashboard: name,
 * language and when it was last practised, and what to do next.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { learningSuggestionsApiV1LearningSuggestionsGet,myLearningGoalsApiV1LearningGoalsGet } from "@/client/sdk.gen";
import type { LearningGoal, LearningSuggestion } from "@/client/types.gen";
import { suggestionHref } from "@/components/learning/LearningToday";
import { EmptyState } from "@/components/shell";
import { ErrorState } from "@/components/shell/ErrorState";
import SpinLoader from "@/components/SpinLoader";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import { formatDay, languageName } from "@/lib/learning";

export default function LearningGoalsPage() {
    const { user, loading: authLoading } = useAuth();
    const learning = useFeature("learning");
    const [goals, setGoals] = useState<LearningGoal[] | null>(null);
    const [suggestions, setSuggestions] = useState<LearningSuggestion[]>([]);
    const [failed, setFailed] = useState(false);
    const [deleted, setDeleted] = useState(false);

    const load = async () => {
        setFailed(false);
        const [list, ideas] = await Promise.all([
            myLearningGoalsApiV1LearningGoalsGet(),
            learningSuggestionsApiV1LearningSuggestionsGet(),
        ]);
        if (list.error || !list.data) {
            setFailed(true);
            return;
        }
        setGoals(list.data);
        setSuggestions(ideas.data ?? []);
    };

    useEffect(() => {
        try {
            setDeleted(new URLSearchParams(window.location.search).get("deleted") === "1");
        } catch {
            // No address to read.
        }
    }, []);

    useEffect(() => {
        if (authLoading || !user || !learning) return;
        void load();
    }, [authLoading, user, learning]);

    if (authLoading || !user) return <SpinLoader />;
    if (!learning) {
        return <p className="mx-auto max-w-[760px] px-4 py-10 text-sm text-muted-foreground">This page is not available.</p>;
    }
    return (
        <div className="mx-auto flex w-full max-w-[760px] flex-col gap-5 px-4 py-6 sm:px-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <h1 className="text-2xl font-semibold leading-8">Learning</h1>
                <Link
                    href="/overview?learn=new"
                    className="motion-m1 inline-flex min-h-11 items-center rounded-[10px] bg-foreground px-4 text-sm font-medium text-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#245B9A] focus-visible:ring-offset-2"
                >
                    Learn something new
                </Link>
            </div>
            {deleted && (
                <p role="status" className="text-sm text-muted-foreground">
                    The goal and all its practice were deleted.
                </p>
            )}
            {failed && <ErrorState title="Could not load your learning goals" onRetry={() => void load()} />}
            {!failed && goals === null && <p role="status" className="text-sm text-muted-foreground">Loading…</p>}
            {goals && goals.length === 0 && (
                <EmptyState
                    title="No learning goals yet"
                    description="Pick anything you want to learn. Lessons are short and go at your pace."
                />
            )}
            {suggestions.length > 0 && (
                <ul className="flex flex-col gap-1" aria-label="Suggestions">
                    {suggestions.map((s) => (
                        <li key={`${s.kind}:${s.goal_id}:${s.skill_id}`}>
                            <Link href={suggestionHref(s)} className="flex min-h-11 items-center text-sm underline-offset-4 hover:underline">
                                {s.text}
                            </Link>
                        </li>
                    ))}
                </ul>
            )}
            {goals && goals.length > 0 && (
                <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
                    {goals.map((goal) => (
                        <li key={goal.goal_id}>
                            <Link
                                href={`/learning/${goal.goal_id}`}
                                className="motion-m1 flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-0.5 px-3 py-2.5 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#245B9A]"
                            >
                                <span className="min-w-0 break-words font-medium">{goal.title}</span>
                                <span className="text-sm text-muted-foreground">
                                    {languageName(goal.explanation_language)}
                                    {goal.last_practised_at ? ` · Practised ${formatDay(goal.last_practised_at)}` : " · Not practised yet"}
                                </span>
                            </Link>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
