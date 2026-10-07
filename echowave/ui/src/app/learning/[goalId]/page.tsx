"use client";

import { useParams } from "next/navigation";

import { LearningProgress } from "@/components/learning/LearningProgress";
import SpinLoader from "@/components/SpinLoader";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";

/** Screen 14: one learning goal's progress (launch stream `learning`). */
export default function LearningGoalPage() {
    const { goalId } = useParams<{ goalId: string }>();
    const { user, loading } = useAuth();
    const learning = useFeature("learning");
    if (loading || !user) return <SpinLoader />;
    if (!learning) {
        return <p className="mx-auto max-w-[760px] px-4 py-10 text-sm text-muted-foreground">This page is not available.</p>;
    }
    return <LearningProgress goalId={goalId} />;
}
