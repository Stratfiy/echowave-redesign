"use client";

import { Activity } from "lucide-react";

import { SystemStatusPanel } from "@/components/superadmin/SystemStatus";

/**
 * Platform health for staff (ADMIN-2, A7): which build is live, and whether
 * the database, Redis, the background worker, the job queue and our provider
 * accounts are all right -- without an SSH session.
 */
export default function SystemPage() {
    return (
        <main className="container mx-auto max-w-5xl space-y-4 px-4 py-6">
            <div className="space-y-1">
                <h1 className="flex items-center gap-2 text-[26px] leading-tight">
                    <Activity className="h-5 w-5" aria-hidden /> System status
                </h1>
                <p className="text-sm text-muted-foreground">
                    Refreshes every 30 seconds while this page is open.
                </p>
            </div>
            <SystemStatusPanel />
        </main>
    );
}
