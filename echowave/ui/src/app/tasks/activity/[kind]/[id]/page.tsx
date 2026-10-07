"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import SpinLoader from "@/components/SpinLoader";
import { ActivityDetailView } from "@/components/today/ActivityDetailView";
import { NotOn } from "@/components/today/NotOn";
import { useFeature } from "@/lib/features";

function Detail() {
    const { kind, id } = useParams<{ kind: string; id: string }>();
    const query = useSearchParams().toString();
    return (
        <>
            <PageHeader title="Activity" />
            <div className="mx-auto flex w-full max-w-[640px] flex-col gap-3 px-4 py-4 sm:px-6">
                {/* Back keeps the filters the list had. */}
                <Link href={`/tasks/activity${query ? `?${query}` : ""}`} className="inline-flex min-h-11 items-center text-sm text-muted-foreground hover:text-foreground md:min-h-8">
                    ← Back to Activity
                </Link>
                <ActivityDetailView kind={kind} id={Number(id)} />
            </div>
        </>
    );
}

/** Screen 09: one item's detail, on its own address. */
export default function ActivityDetailPage() {
    if (!useFeature("today_list")) return <NotOn what="Activity" />;
    return (
        <Suspense fallback={<SpinLoader />}>
            <Detail />
        </Suspense>
    );
}
