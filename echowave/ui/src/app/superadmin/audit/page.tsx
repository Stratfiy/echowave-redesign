"use client";

import { ScrollText } from "lucide-react";

import { AuditLog } from "@/components/superadmin/AuditLog";
import { Card, CardContent } from "@/components/ui/card";

/**
 * The staff audit log (ADMIN-2, A6): impersonations, invites, trial changes,
 * credit and rate changes, newest first. Both logs were written for months and
 * readable only with SQL.
 */
export default function AuditPage() {
    return (
        <main className="container mx-auto max-w-6xl space-y-4 px-4 py-6">
            <div className="space-y-1">
                <h1 className="flex items-center gap-2 text-[26px] leading-tight">
                    <ScrollText className="h-5 w-5" aria-hidden /> Audit log
                </h1>
                <p className="text-sm text-muted-foreground">
                    Every sensitive staff action and every change to an account&apos;s money, in one place.
                </p>
            </div>
            <Card>
                <CardContent className="pt-5">
                    <AuditLog showFilters />
                </CardContent>
            </Card>
        </main>
    );
}
