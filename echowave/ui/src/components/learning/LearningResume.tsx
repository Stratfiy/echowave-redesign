"use client";

/**
 * The resume link on the Chat start (screen 13: "Learning Guide in Chat or
 * resume link"): the goal practised most recently, one tap back into its
 * lesson. Nothing when there is no goal, and nothing while loading -- an
 * empty Chat start must not flash a row that then disappears.
 */

import { BookOpen } from "lucide-react";
import { useEffect, useState } from "react";

import { myLearningGoalsApiV1LearningGoalsGet } from "@/client/sdk.gen";
import type { LearningGoal } from "@/client/types.gen";
import { useAuth } from "@/lib/auth";

export function LearningResume({ onOpen }: { onOpen: (goalId: string) => void }) {
    const { user, loading } = useAuth();
    const userId = user?.id ?? null;
    const [goal, setGoal] = useState<LearningGoal | null>(null);

    useEffect(() => {
        if (loading || userId === null) return;
        let cancelled = false;
        void myLearningGoalsApiV1LearningGoalsGet().then((response) => {
            if (!cancelled && response.data && response.data.length > 0) setGoal(response.data[0]);
        });
        return () => {
            cancelled = true;
        };
    }, [loading, userId]);

    if (!goal) return null;
    return (
        <button
            type="button"
            onClick={() => onOpen(goal.goal_id)}
            className="motion-m1 mt-3 inline-flex min-h-11 max-w-full items-center gap-2 rounded-full border border-border px-4 text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#245B9A]"
            data-testid="learning-resume"
        >
            <BookOpen aria-hidden className="h-4 w-4 shrink-0" />
            <span className="truncate">Continue learning: {goal.title}</span>
        </button>
    );
}

export default LearningResume;
