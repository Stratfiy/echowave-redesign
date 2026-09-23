"use client";

import { useParams } from "next/navigation";

import { TaskPage } from "@/components/desk/TaskPage";
import SpinLoader from "@/components/SpinLoader";
import { useAuth } from "@/lib/auth";

/** One task on its own page (TB-2). */
export default function TaskDetailPage() {
    const { taskId } = useParams<{ taskId: string }>();
    const { user, loading } = useAuth();
    if (loading || !user) return <SpinLoader />;
    return <TaskPage taskId={Number(taskId)} />;
}
