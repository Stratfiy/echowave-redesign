"use client";

import { useParams } from "next/navigation";

import { PageHeader } from "@/components/layout/PageHeader";
import SpinLoader from "@/components/SpinLoader";
import { ApprovalDetail } from "@/components/today/ApprovalDetail";
import { NotOn } from "@/components/today/NotOn";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";

/** Screen 08 on its own address: a chat card, Today or a link all land here. */
export default function ApprovalPage() {
    const { eventId } = useParams<{ eventId: string }>();
    const { user, loading } = useAuth();
    const listOn = useFeature("today_list");
    const dockOn = useFeature("approval_dock");
    const on = listOn || dockOn;
    if (loading || !user) return <SpinLoader />;
    if (!on) return <NotOn what="This approval screen" />;
    return (
        <>
            <PageHeader title="Approval" />
            <div className="mx-auto w-full max-w-[560px] px-4 py-4 sm:px-6">
                <ApprovalDetail eventId={Number(eventId)} backHref="/tasks" />
            </div>
        </>
    );
}
