"use client";

/**
 * The meeting pages exist only while `meeting_capture` is on. Off, a link
 * someone kept says so plainly rather than showing a screen that cannot work.
 */

import Link from "next/link";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/EmptyState";
import SpinLoader from "@/components/SpinLoader";
import { Button } from "@/components/ui/button";
import { useAppConfig } from "@/context/AppConfigContext";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";

export function MeetingsGate({ children }: { children: ReactNode }) {
    const { user, loading } = useAuth();
    const { config } = useAppConfig();
    const on = useFeature("meeting_capture");
    if (loading || !user || !config) return <SpinLoader />;
    if (!on) {
        return (
            <div className="mx-auto w-full max-w-[640px] px-4 py-10">
                <EmptyState
                    title="Meeting mode is not available yet"
                    description="It has not been switched on for this workspace."
                    action={
                        <Button asChild variant="outline" className="min-h-11">
                            <Link href="/overview">Back to Chat</Link>
                        </Button>
                    }
                />
            </div>
        );
    }
    return <>{children}</>;
}

export default MeetingsGate;
